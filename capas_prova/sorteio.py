"""Sorteio balanceado do caderno de cada aluno (ex.: C0201 ou C0202 para o 2º ano).

Como funciona
  * Dentro de cada turma os alunos são embaralhados (ordem aleatória) e recebem os cadernos
    alternadamente -> em toda turma a diferença entre os cadernos é no máximo 1.
  * Quando a turma tem número ímpar, o aluno "extra" vai para o caderno que está em menor
    quantidade na escola (na série e depois no total da escola) e, em seguida, no geral.
    Resultado: diferença de no máximo 1 também por escola/série e no total.
  * É reprodutível: a mesma semente (config.yaml -> sorteio.semente) gera o mesmo sorteio.
  * É estável: o resultado fica salvo em `sorteio_cadernos.csv` na pasta de saída. Rodar de novo
    (ex.: após corrigir um nome ou incluir alunos novos) NÃO muda quem já foi sorteado; alunos novos
    entram no caderno que deixa a turma mais equilibrada. Use --refazer-sorteio para sortear tudo de novo.
  * Se a planilha tiver uma coluna "Caderno" preenchida, ela é respeitada (não é sorteada).
"""
from __future__ import annotations

import csv
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

ARQUIVO = "sorteio_cadernos.csv"
CAMPOS = ["ra", "escola", "serie", "turma", "estudante", "caderno", "origem", "data"]


def cadernos_da_serie(serie: str, cfg: dict) -> list[str]:
    m = re.search(r"\d+", serie or "")
    return list(cfg.get("cadernos_por_serie", {}).get(int(m.group()), [])) if m else []


def _carregar(caminho: Path) -> dict[str, dict]:
    if not caminho.exists():
        return {}
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        return {r["ra"]: r for r in csv.DictReader(f)}


def sortear(df: pd.DataFrame, cfg: dict, saida: Path, refazer: bool = False,
            salvar: bool = True) -> tuple[dict[str, str], list[str]]:
    """Retorna ({ra: caderno}, mensagens). `df` precisa das colunas escola, serie, turma, ra, estudante."""
    conf = cfg.get("sorteio", {})
    semente = str(conf.get("semente", cfg.get("edicao", "")))
    caminho = Path(saida) / ARQUIVO
    anteriores = {} if refazer else _carregar(caminho)
    msgs: list[str] = []
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")

    # um registro por RA (RA repetido fica com a 1ª linha, como na geração)
    alunos = df[df["ra"] != ""].drop_duplicates("ra", keep="first")
    col_caderno = "caderno" in alunos.columns

    resultado: dict[str, dict] = {}
    c_turma, c_escola_serie, c_escola, c_geral = Counter(), Counter(), Counter(), Counter()

    def registrar(a: dict, caderno: str, origem: str, data: str):
        ops = cadernos_da_serie(a["serie"], cfg)
        i = ops.index(caderno) if caderno in ops else 0
        s = ops[0][:3] if ops else ""
        c_turma[(a["escola"], a["turma"], s, i)] += 1
        c_escola_serie[(a["escola"], s, i)] += 1
        c_escola[(a["escola"], i)] += 1
        c_geral[(s, i)] += 1
        resultado[a["ra"]] = {k: a.get(k, "") for k in ("ra", "escola", "serie", "turma", "estudante")} | {
            "caderno": caderno, "origem": origem, "data": data}

    pendentes = []
    for a in alunos.to_dict("records"):
        ops = cadernos_da_serie(a["serie"], cfg)
        fixo = a.get("caderno", "").strip().upper() if col_caderno else ""
        ant = anteriores.get(a["ra"])
        if fixo:
            registrar(a, fixo, "planilha", agora)
        elif ant and ant["caderno"] in ops:
            registrar(a, ant["caderno"], "sorteio anterior", ant.get("data", ""))
        elif ops:
            pendentes.append(a)
        else:
            msgs.append(f"RA {a['ra']}: série '{a['serie']}' sem cadernos configurados")

    # turmas em ordem fixa; alunos da turma por RA e depois embaralhados (independe da ordem da planilha)
    grupos: dict[tuple, list] = {}
    for a in pendentes:
        grupos.setdefault((a["escola"], a["serie"], a["turma"]), []).append(a)
    for chave in sorted(grupos):
        # cada turma tem seu próprio gerador aleatório: mudar uma turma não altera o sorteio das outras
        rng = random.Random(f"{semente}|{'|'.join(chave)}")
        turma = sorted(grupos[chave], key=lambda a: a["ra"])
        rng.shuffle(turma)
        for a in turma:
            ops = cadernos_da_serie(a["serie"], cfg)
            s = ops[0][:3]
            i = min(range(len(ops)), key=lambda i: (
                c_turma[(a["escola"], a["turma"], s, i)],
                c_escola_serie[(a["escola"], s, i)],
                c_escola[(a["escola"], i)],
                c_geral[(s, i)],
                rng.random(),
            ))
            registrar(a, ops[i], "sorteio", agora)

    novos = sum(1 for r in resultado.values() if r["origem"] == "sorteio")
    if anteriores and novos:
        msgs.append(f"{novos} aluno(s) novo(s) sorteado(s); os demais mantiveram o caderno do sorteio anterior")
    if salvar:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        # mantém no arquivo alunos de sorteios anteriores que não estão nesta planilha (histórico)
        todos = {ra: r for ra, r in anteriores.items() if ra not in resultado} | resultado
        with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            w.writeheader()
            w.writerows(sorted(todos.values(), key=lambda r: (r["escola"], r["serie"], r["turma"], r["ra"])))
    return {ra: r["caderno"] for ra, r in resultado.items()}, msgs
