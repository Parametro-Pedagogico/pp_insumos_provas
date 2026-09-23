"""Gera uma capa (PDF) por aluno/caderno a partir de uma planilha.

Uso:
    python -m capas_prova.gerar --planilha dados.xlsx --saida saida/
    python -m capas_prova.gerar --planilha dados.xlsx --saida saida/ --limite 20   # teste rápido

Saídas (em --saida):
    capas/<ESCOLA>/<TURMA>/<CADERNO>_<RA>_<NOME>.pdf   um arquivo por aluno/caderno
    manifesto.csv          codigo_cartao -> aluno, caderno, arquivo (base para juntar capa+caderno e para o leitor)
    codigos_registrados.csv registro persistente dos códigos emitidos (não apagar entre lotes)
    layouts/<CADERNO>.json  posição de cada bolha e marcador (usado pelo leitor)
    relatorio.txt          avisos: linhas ignoradas, textos reduzidos, duplicidades
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import pymupdf
import qrcode
import yaml

from .codigos import RegistroCodigos
from .modelo import ModeloPreparado, preparar
from .nomes import PARTICULAS_PADRAO, padronizar_nome
from .sorteio import ARQUIVO as ARQUIVO_SORTEIO
from .sorteio import sortear
from .verificar import gerar_verificacao

TAM_REDUZIDO = 10.5   # abaixo disso, prefere quebrar a linha
TAM_MIN = 9.0         # menor tamanho aceito em linha quebrada
CAMPOS_TEXTO = {  # rótulo impresso -> campo interno
    "ESCOLA": "escola",
    "ANO / SÉRIE": "serie",
    "TURMA": "turma",
    "ESTUDANTE": "estudante",
    "RA/CÓDIGO": "ra",
    "CÓDIGO DO CARTÃO": "codigo_cartao",
}


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def chave_coluna(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", sem_acento(str(s)).upper())


def nome_arquivo(s: str, limite: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", sem_acento(str(s))).strip("_")
    return s[:limite] or "SEM_NOME"


def ler_planilha(caminho: Path, cfg: dict) -> pd.DataFrame:
    """Lê a planilha e monta os campos da capa conforme `campos` do config.yaml."""
    if caminho.suffix.lower() in (".csv", ".txt"):
        df = pd.read_csv(caminho, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    else:
        df = pd.read_excel(caminho, dtype=str)
    df = df.fillna("")
    colunas = {chave_coluna(c): c for c in df.columns}
    opcionais = {"caderno", "codigo_cartao"}
    saida = pd.DataFrame(index=df.index)
    for campo, modelo in cfg["campos"].items():
        nomes = re.findall(r"{([^}]+)}", modelo)
        faltam = [n for n in nomes if chave_coluna(n) not in colunas]
        if faltam:
            if campo in opcionais:
                continue
            raise SystemExit(f"Campo '{campo}': coluna(s) {faltam} não encontrada(s) na planilha. "
                             f"Colunas disponíveis: {list(df.columns)}")
        fontes = [df[colunas[chave_coluna(n)]].astype(str).str.strip() for n in nomes]
        valores = []
        for linha in zip(*fontes) if fontes else [()] * len(df):
            v = modelo
            for n, x in zip(nomes, linha):
                v = v.replace("{" + n + "}", x)
            # campo sem dado (ex.: "{Marca} - {Unidade}" com as duas vazias) fica vazio
            valores.append(re.sub(r"\s+", " ", v).strip() if any(linha) else "")
        saida[campo] = valores
    return saida


def cadernos_do_aluno(aluno: dict, cfg: dict) -> list[str]:
    if aluno.get("caderno"):
        return [c.strip().upper() for c in re.split(r"[;,]", aluno["caderno"]) if c.strip()]
    m = re.search(r"\d+", aluno["serie"])
    return list(cfg.get("cadernos_por_serie", {}).get(int(m.group()), [])) if m else []


class Gerador:
    def __init__(self, cfg: dict, base: Path, saida: Path):
        self.cfg, self.base, self.saida = cfg, base, saida
        self.modelos: dict[str, ModeloPreparado] = {}
        self.avisos: list[str] = []
        self.fonte_arq = str(base / cfg["fontes"]["dados_regular"])
        self.fonte = pymupdf.Font(fontfile=self.fonte_arq)

    def modelo(self, caderno: str) -> ModeloPreparado:
        if caderno not in self.modelos:
            m = preparar(self.cfg, self.base, caderno)
            self.modelos[caderno] = m
            destino = self.saida / "layouts" / f"{caderno}.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(json.dumps(m.mapa, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.modelos[caderno]

    def _quebrar(self, rotulo: str, valor: str, tam: float, largura: float) -> list[str] | None:
        """Quebra 'ROTULO: valor' em até 2 linhas (a 2ª alinhada após o rótulo). None se não couber."""
        recuo = self.fonte.text_length(rotulo + ": ", fontsize=tam)
        tokens = re.findall(r"[^\s/]+/?|/", valor)   # permite quebrar depois de "/"
        linhas, atual = [], rotulo + ":"
        for tk in tokens:
            sep = "" if atual.endswith("/") else " "
            cand = atual + sep + tk
            lim = largura if not linhas else largura - recuo
            if self.fonte.text_length(cand if not linhas else cand.strip(), fontsize=tam) <= lim or atual.endswith(":"):
                atual = cand
            else:
                linhas.append(atual)
                atual = tk
        linhas.append(atual)
        if len(linhas) > 2 or any(self.fonte.text_length(l, fontsize=tam) > largura - (recuo if i else 0)
                                   for i, l in enumerate(linhas)):
            return None
        return [linhas[0]] + [l.strip() for l in linhas[1:]]

    def _escrever_dados(self, pagina, m: ModeloPreparado, aluno: dict, ident: str):
        """Escreve o bloco de dados; campos longos diminuem até TAM_REDUZIDO e depois quebram linha."""
        linhas = []  # (texto, tamanho, recuo, slot)
        for slot in m.slots:
            valor = aluno[CAMPOS_TEXTO[slot.rotulo]]
            if not (slot.rotulo == "ESTUDANTE" and self.cfg.get("nomes", {}).get("padronizar", True)):
                valor = valor.upper()   # nome padronizado mantém maiúsculas/minúsculas
            texto = f"{slot.rotulo}: {valor}"
            tam, L = slot.tamanho, slot.largura_max
            larg = self.fonte.text_length(texto, fontsize=tam)
            if larg <= L:
                linhas.append((texto, tam, 0.0, slot))
                continue
            if tam * L / larg >= TAM_REDUZIDO:
                tam = tam * L / larg
                self.avisos.append(f"{ident}: '{slot.rotulo}' reduzido para {tam:.1f} pt")
                linhas.append((texto, tam, 0.0, slot))
                continue
            partes = None
            t2 = slot.tamanho
            while partes is None and t2 >= TAM_MIN:
                partes = self._quebrar(slot.rotulo, valor, t2, L)
                t2 = t2 if partes else t2 - 0.25
            if partes is None:  # nem em 2 linhas: trunca
                t2 = TAM_MIN
                partes = self._quebrar(slot.rotulo, valor[: int(len(valor) * 0.8)] + "…", t2, L) or [texto[:60] + "…"]
                self.avisos.append(f"{ident}: '{slot.rotulo}' truncado (texto muito longo)")
            else:
                self.avisos.append(f"{ident}: '{slot.rotulo}' quebrado em {len(partes)} linhas")
            recuo = self.fonte.text_length(slot.rotulo + ": ", fontsize=t2)
            for i, parte in enumerate(partes):
                linhas.append((parte, t2, recuo if i else 0.0, slot))

        y0 = m.slots[0].origem[1]
        passo = m.slots[1].origem[1] - y0
        if len(linhas) > 1:
            passo = min(passo, (m.limite_dados - y0) / (len(linhas) - 1))
        for i, (texto, tam, recuo, slot) in enumerate(linhas):
            pagina.insert_text((slot.origem[0] + recuo, y0 + i * passo), texto,
                               fontname="aleo", fontsize=tam, color=slot.cor)

    @staticmethod
    def _qr(pagina, rect, conteudo: str):
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=0)
        qr.add_data(conteudo)
        qr.make(fit=True)
        mat = qr.get_matrix()
        x0, y0, x1, y1 = rect
        lado = min(x1 - x0, y1 - y0)
        m = lado / len(mat)
        pagina.draw_rect((x0 - 4 * m, y0 - 4 * m, x0 + lado + 4 * m, y0 + lado + 4 * m), color=None, fill=(1, 1, 1))
        forma = pagina.new_shape()
        for i, linha in enumerate(mat):
            j = 0
            while j < len(linha):  # agrupa módulos pretos consecutivos em um retângulo
                if linha[j]:
                    k = j
                    while k < len(linha) and linha[k]:
                        k += 1
                    forma.draw_rect((x0 + j * m, y0 + i * m, x0 + k * m, y0 + (i + 1) * m + 0.05))
                    j = k
                else:
                    j += 1
        forma.finish(color=None, fill=(0, 0, 0))
        forma.commit()

    def capa(self, aluno: dict) -> bytes:
        m = self.modelo(aluno["caderno"])
        doc = pymupdf.open("pdf", m.pdf)
        pagina = doc[0]
        pagina.insert_font(fontname="aleo", fontfile=self.fonte_arq)
        self._escrever_dados(pagina, m, aluno, f"{aluno['ra']}/{aluno['caderno']}")
        self._qr(pagina, m.qr, aluno["codigo_cartao"])
        doc.set_metadata({"title": f"Capa {aluno['caderno']} - {aluno['estudante']}",
                          "subject": aluno["codigo_cartao"], "creator": "capas_prova"})
        pagina.clean_contents()
        doc.subset_fonts()
        return doc.tobytes(garbage=3, deflate=True)


def salvar_manifesto(caminho: Path, novos: list[dict]) -> None:
    """Acumula entre execuções (ex.: gerar escola por escola): a mesma capa (RA+caderno) é substituída."""
    linhas: dict[tuple[str, str], dict] = {}
    if caminho.exists():
        with open(caminho, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                linhas[(r["ra"], r["caderno"])] = r
    for r in novos:
        linhas[(r["ra"], r["caderno"])] = r
    if not linhas:
        return
    campos = list(dict.fromkeys(k for r in linhas.values() for k in r))
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas.values())


def remover_capas_obsoletas(saida: Path, sorteados: dict[str, str]) -> int:
    """Apaga capas já geradas de um caderno que não é o sorteado para o aluno (ex.: geração antiga
    com os dois cadernos, ou após --refazer-sorteio) e tira essas linhas do manifesto."""
    arq = saida / "manifesto.csv"
    if not arq.exists():
        return 0
    with open(arq, newline="", encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f))
    manter, removidas = [], 0
    for r in linhas:
        if r["ra"] in sorteados and sorteados[r["ra"]] != r["caderno"]:
            (saida / r["arquivo"]).unlink(missing_ok=True)
            removidas += 1
        else:
            manter.append(r)
    if removidas:
        arq.unlink()
        salvar_manifesto(arq, manter)
    return removidas


class _PaginaNula:
    """Página que não desenha nada: usada no modo --validar para medir textos sem gerar PDF."""

    def insert_text(self, *a, **k):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planilha", required=True, type=Path)
    ap.add_argument("--saida", required=True, type=Path)
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config.yaml")
    ap.add_argument("--limite", type=int, default=0, help="usa só as N primeiras linhas da planilha (teste)")
    ap.add_argument("--escola", help="gera só as escolas que contêm este texto (ex.: 'ECB - SUDOESTE')")
    ap.add_argument("--validar", action="store_true",
                    help="só confere a planilha (textos longos, RA repetido etc.), sem gerar PDFs")
    ap.add_argument("--caderno", help="gera só estes cadernos, separados por vírgula (ex.: C0201,C0501)")
    ap.add_argument("--refazer-sorteio", action="store_true",
                    help="sorteia de novo o caderno de TODOS os alunos (descarta o sorteio salvo)")
    args = ap.parse_args(argv)

    base = args.config.resolve().parent
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    df = ler_planilha(args.planilha, cfg)
    # o sorteio é sempre feito sobre a base INTEIRA (antes dos filtros), para não depender do lote
    if cfg.get("sorteio", {}).get("ativo", True):
        sorteados, msgs = sortear(df, cfg, args.saida, refazer=args.refazer_sorteio)
        df["caderno"] = df["ra"].map(sorteados).fillna("")
        for m in msgs:
            print("  sorteio:", m)
        print(f"Sorteio de cadernos: {len(sorteados)} alunos (ver {args.saida / ARQUIVO_SORTEIO})")
        if not args.validar:
            removidas = remover_capas_obsoletas(args.saida, sorteados)
            if removidas:
                print(f"  {removidas} capa(s) de caderno não sorteado removida(s) da pasta de saída")
    if args.limite:
        df = df.head(args.limite)
    if args.escola:
        df = df[df["escola"].map(chave_coluna).str.contains(chave_coluna(args.escola), regex=False)]
    so_cadernos = {c.strip().upper() for c in args.caderno.split(",")} if args.caderno else None

    cfg_nomes = cfg.get("nomes", {})
    ajustes: dict[tuple[str, str], str] = {}   # (ra, caderno) -> textos reduzidos/quebrados
    g = Gerador(cfg, base, args.saida)
    registro = RegistroCodigos(args.saida / "codigos_registrados.csv")
    vistos: dict[tuple[str, str], int] = {}
    manifesto = []
    ok = 0
    total = len(df)
    for i, (idx, linha) in enumerate(df.iterrows(), start=1):
        n = idx + 2  # linha no Excel (1 = cabeçalho)
        base_aluno = {k: linha.get(k, "") for k in CAMPOS_TEXTO.values()} | {"caderno": linha.get("caderno", "")}
        vazios = [k for k in ("escola", "serie", "turma", "estudante", "ra") if not base_aluno[k]]
        if vazios:
            g.avisos.append(f"linha {n}: ignorada, campos vazios {vazios}")
            continue
        if cfg_nomes.get("padronizar", True):
            base_aluno["estudante"] = padronizar_nome(
                base_aluno["estudante"], cfg_nomes.get("particulas_minusculas", PARTICULAS_PADRAO),
                cfg_nomes.get("remover_parenteses", True))[0]
        cadernos = cadernos_do_aluno(base_aluno, cfg)
        if not cadernos:
            g.avisos.append(f"linha {n}: ignorada, nenhum caderno para a série '{base_aluno['serie']}'")
            continue
        for caderno in cadernos:
            if so_cadernos and caderno not in so_cadernos:
                continue
            aluno = base_aluno | {"caderno": caderno}
            if caderno not in cfg["cadernos"]:
                g.avisos.append(f"linha {n}: caderno '{caderno}' não configurado")
                continue
            chave = (aluno["ra"], caderno)
            if chave in vistos:
                g.avisos.append(f"linha {n}: ignorada, RA {aluno['ra']} repetido (já gerado pela linha "
                                f"{vistos[chave]}) - conferir na planilha")
                continue
            vistos[chave] = n
            try:
                aluno["codigo_cartao"] = registro.obter(cfg["edicao"], aluno["ra"], caderno,
                                                        linha.get("codigo_cartao") or None)
            except ValueError as e:
                g.avisos.append(f"linha {n}: ignorada, {e}")
                continue

            ident = f"{aluno['ra']}/{caderno}"
            antes = len(g.avisos)
            if args.validar:
                g._escrever_dados(_PaginaNula(), g.modelo(caderno), aluno, ident)
                ajustes[chave] = "; ".join(a.split(": ", 1)[1] for a in g.avisos[antes:])
                ok += 1
                continue
            rel = Path("capas", nome_arquivo(aluno["escola"]), nome_arquivo(aluno["turma"]),
                       f"{caderno}_{nome_arquivo(aluno['ra'])}_{nome_arquivo(aluno['estudante'], 40)}.pdf")
            destino = args.saida / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(g.capa(aluno))
            ajustes[chave] = "; ".join(a.split(": ", 1)[1] for a in g.avisos[antes:])
            info = cfg["cadernos"][caderno]
            manifesto.append(aluno | {"linha_planilha": n, "modelo": info["modelo"], "layout": info["layout"],
                                      "arquivo": rel.as_posix(), "ajustes_texto": ajustes[chave]})
            ok += 1
        if i % 200 == 0 or i == total:
            print(f"\r  {i}/{total} alunos processados, {ok} capas", end="", flush=True)
    print()

    args.saida.mkdir(parents=True, exist_ok=True)
    if not args.validar:
        registro.salvar()
        salvar_manifesto(args.saida / "manifesto.csv", manifesto)
    nome_rel = "relatorio_validacao.txt" if args.validar else "relatorio.txt"
    (args.saida / nome_rel).write_text(
        f"Capas {'validadas' if args.validar else 'geradas'}: {ok}\nAvisos: {len(g.avisos)}\n\n" + "\n".join(g.avisos) + "\n", encoding="utf-8")
    print(f"{ok} capas {'validadas' if args.validar else 'geradas'} em {args.saida}  |  {len(g.avisos)} avisos (ver {nome_rel})")
    arq = gerar_verificacao(cfg, args.planilha, args.saida, ajustes, validacao=args.validar)
    print(f"Verificação: {arq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
