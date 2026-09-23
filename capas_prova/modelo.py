"""Preparação dos modelos: limpa a página do Canva e desenha as partes fixas de cada caderno.

Para cada caderno gera-se, uma única vez, um PDF-base contendo:
  * o design original do Canva (fundo, cabeçalho, logos, quadro de instruções);
  * o código do caderno no rótulo do cabeçalho;
  * a grade de respostas vetorial em cor de dropout, com marcadores ArUco;
e um mapa (JSON) com a posição exata de cada bolha, usado pelo leitor.
Os dados do estudante e o QR Code são inseridos depois, por aluno (ver gerar.py).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

ARUCO_DICT = "DICT_4X4_50"
ROTULOS_DADOS = ["ESCOLA", "ANO / SÉRIE", "TURMA", "ESTUDANTE", "RA/CÓDIGO", "CÓDIGO DO CARTÃO"]

# Geometria da grade (pt)
MARCADOR = 20.0      # lado do marcador ArUco
DIAM = 11.0          # diâmetro da bolha
PASSO_X = 17.0       # distância entre centros de alternativas
PASSO_Y_MAX = 17.5   # distância máxima entre linhas
LARG_NUM = 18.0      # largura da coluna de números
PAD = 6.0            # margem interna da caixa


def rgb(c):
    return tuple(v / 255 for v in c)


@dataclass
class SlotTexto:
    rotulo: str
    origem: tuple[float, float]
    tamanho: float
    cor: tuple[float, float, float]
    largura_max: float


@dataclass
class ModeloPreparado:
    caderno: str
    pdf: bytes
    slots: list[SlotTexto]
    qr: tuple[float, float, float, float]
    limite_dados: float
    mapa: dict = field(default_factory=dict)


# ---------------------------------------------------------------- limpeza

def _amostrar_cores(pagina: pymupdf.Page, rect) -> tuple[tuple, tuple]:
    """Retorna (cor do fundo, cor do texto) do rótulo do caderno, amostradas da página renderizada."""
    r = pymupdf.Rect(rect) + (-3, -3, 3, 3)
    pix = pagina.get_pixmap(dpi=200, clip=r)
    im = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    sat = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)[:, :, 1]
    fundo = np.median(im[sat < 60], axis=0) if (sat < 60).any() else np.array([255, 255, 255])
    texto = np.median(im[sat > 110], axis=0)
    return tuple(fundo / 255), tuple(texto / 255)


def _limpar(pagina: pymupdf.Page, cfg: dict, mod: dict) -> list[SlotTexto]:
    tamanhos = {tuple(t) for t in cfg["imagens_remover"]}
    for info in pagina.get_image_info(xrefs=True):
        if (info["width"], info["height"]) in tamanhos and info["xref"]:
            try:
                pagina.delete_image(info["xref"])
            except Exception:  # mesma imagem já removida (várias ocorrências)
                pass

    slots: list[SlotTexto] = []
    qr_x0 = mod["qr"][0]
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for span in linha["spans"]:
                texto = span["text"].strip()
                rot = next((r for r in ROTULOS_DADOS if texto.upper().startswith(r + ":")), None)
                if rot is None:
                    continue
                ox, oy = span["origin"]
                c = span["color"]
                slots.append(SlotTexto(
                    rotulo=rot, origem=(ox, oy), tamanho=round(span["size"], 2),
                    cor=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                    largura_max=qr_x0 - 20 - ox,  # respeita a zona branca do QR
                ))
                pagina.add_redact_annot(pymupdf.Rect(span["bbox"]), fill=False)
    faltando = set(ROTULOS_DADOS) - {s.rotulo for s in slots}
    if faltando:
        raise ValueError(f"Página {mod['pagina']}: campos não encontrados no modelo: {faltando}")

    # remove as bordas vetoriais antigas da grade (só o que estiver inteiramente dentro da área)
    pagina.add_redact_annot(pymupdf.Rect(mod["area_grade"]) + (-4, -4, 4, 4), fill=False)
    pagina.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )
    return slots


def _otimizar_fundo(pagina: pymupdf.Page, dpi: int, qualidade: int) -> None:
    """Converte a imagem de fundo (PNG com transparência, ~1 MB) em JPEG sobre branco.

    O fundo é a primeira coisa desenhada sobre a página branca, então compor com branco
    não altera a aparência; reduz o tamanho de cada capa em ~70%.
    """
    area_pag = pagina.rect.width * pagina.rect.height
    for info in pagina.get_image_info(xrefs=True):
        bbox = pymupdf.Rect(info["bbox"])
        if not info["xref"] or bbox.get_area() < 0.8 * area_pag:
            continue
        doc = pagina.parent
        pix = pymupdf.Pixmap(doc, info["xref"])
        smask = doc.extract_image(info["xref"]).get("smask", 0)
        if smask:  # a transparência fica num objeto separado (SMask)
            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
        if pix.colorspace and pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        img = Image.frombytes("RGBA" if pix.alpha else "RGB", (pix.w, pix.h), pix.samples)
        if img.mode == "RGBA":
            fundo = Image.new("RGB", img.size, (255, 255, 255))
            fundo.paste(img, mask=img.split()[3])
            img = fundo
        largura_alvo = int(bbox.width / 72 * dpi)
        if img.width > largura_alvo:
            img = img.resize((largura_alvo, round(img.height * largura_alvo / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=qualidade, optimize=True)
        pagina.replace_image(info["xref"], stream=buf.getvalue())
        return


def _comprimir_imagens(pagina: pymupdf.Page, qualidade: int, min_bytes: int = 50_000) -> None:
    """Recodifica em JPEG as demais imagens grandes (logos, faixas do cabeçalho).

    A transparência (SMask) é mantida como está: só a parte de cor vira JPEG, então a imagem
    continua se sobrepondo ao fundo exatamente como no Canva.
    """
    doc = pagina.parent
    for xref, *_ in pagina.get_images(full=True):
        filtro = doc.xref_get_key(xref, "Filter")[1]
        if "DCT" in filtro or len(doc.xref_stream_raw(xref) or b"") < min_bytes:
            continue
        pix = pymupdf.Pixmap(doc, xref)
        if pix.alpha or not pix.colorspace or pix.colorspace.n != 3 or doc.xref_get_key(xref, "BitsPerComponent")[1] != "8":
            continue
        buf = io.BytesIO()
        Image.frombytes("RGB", (pix.w, pix.h), pix.samples).save(buf, "JPEG", quality=qualidade, optimize=True)
        doc.update_stream(xref, buf.getvalue(), compress=False)
        doc.xref_set_key(xref, "Filter", "/DCTDecode")
        doc.xref_set_key(xref, "DecodeParms", "null")
        doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")


# ---------------------------------------------------------------- desenho

class Desenho:
    def __init__(self, pagina: pymupdf.Page, cfg: dict, base: Path):
        self.p = pagina
        self.dropout = rgb(cfg["cores"]["dropout"])
        self.tinta = rgb(cfg["cores"]["tinta"])
        f = cfg["fontes"]
        self.fontes = {}
        for nome, chave in [("sansr", "sans_regular"), ("sansb", "sans_negrito"), ("rotulo", "rotulo")]:
            arq = str(base / f[chave])
            pagina.insert_font(fontname=nome, fontfile=arq)
            self.fontes[nome] = pymupdf.Font(fontfile=arq)

    def texto(self, x, y, s, fonte="sansr", tam=9.0, cor=None, alinhar="esq"):
        largura = self.fontes[fonte].text_length(s, fontsize=tam)
        if alinhar == "centro":
            x -= largura / 2
        elif alinhar == "dir":
            x -= largura
        self.p.insert_text((x, y), s, fontname=fonte, fontsize=tam, color=cor or self.tinta)
        return largura

    def texto_centralizado(self, cx, cy, s, fonte, tam, cor):
        """Centraliza pela altura das maiúsculas (cap height ~0,72 em)."""
        self.texto(cx, cy + tam * 0.36, s, fonte, tam, cor, alinhar="centro")

    # --- rótulo "caderno CXXXX"
    def rotulo_caderno(self, rect, codigo: str):
        fundo, cor = _amostrar_cores(self.p, rect)
        r = pymupdf.Rect(rect)
        self.p.draw_rect(r + (-3, -2.5, 3, 2.5), color=None, fill=fundo)
        f = self.fontes["rotulo"]
        # tamanho que faz o texto ocupar a mesma largura e altura do original
        tam = min(r.width / f.text_length(codigo, fontsize=1), r.height / 0.70)
        self.texto_centralizado((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, codigo, "rotulo", tam, cor)

    # --- instruções e exemplo
    def instrucoes_topo(self, x, y):
        self.texto(x, y, "Não amasse, não dobre, não suje e não rasure esta folha.", tam=9.5)
        self.texto(x, y + 14, "Utilize somente caneta esferográfica de tinta preta.", "sansb", 9.5)
        self.texto(x, y + 28, "Preencha completamente o círculo da resposta como no exemplo:", tam=9.5)

    def caixa_exemplo(self, rect):
        r = pymupdf.Rect(rect)
        self.p.draw_rect(r, color=self.tinta, width=0.9)
        yt, yb = r.y0 + 10, r.y0 + r.height * 0.66
        rr = DIAM / 2
        xa = r.x0 + r.width * 0.2
        self.texto(xa, yt, "FAÇA ASSIM", "sansb", 6.2, alinhar="centro")
        self.bolha(xa, yb, "", preenchida=1.0)
        xs = [r.x0 + r.width * f for f in (0.60, 0.73, 0.86)]
        self.texto(xs[1], yt, "NÃO FAÇA ASSIM", "sansb", 6.2, alinhar="centro")
        # meia marcação, X, ponto
        self.bolha(xs[0], yb, "")
        self.p.draw_sector((xs[0], yb), (xs[0] - rr + 0.5, yb), 180, color=None, fill=self.tinta)
        self.bolha(xs[1], yb, "")
        d = rr * 0.7
        for a, b in [((-d, -d), (d, d)), ((-d, d), (d, -d))]:
            self.p.draw_line((xs[1] + a[0], yb + a[1]), (xs[1] + b[0], yb + b[1]), color=self.tinta, width=1.1)
        self.bolha(xs[2], yb, "")
        self.p.draw_circle((xs[2], yb), rr * 0.35, color=None, fill=self.tinta)

    def bolha(self, cx, cy, letra, preenchida=0.0):
        self.p.draw_circle((cx, cy), DIAM / 2, color=self.dropout, width=0.8,
                           fill=self.tinta if preenchida else None)
        if letra:
            self.texto_centralizado(cx, cy, letra, "sansb", 6.3, self.dropout)

    # --- marcadores
    def aruco(self, x, y, id_):
        dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ARUCO_DICT))
        bits = cv2.aruco.generateImageMarker(dic, id_, 6)  # 4x4 + borda preta
        cel = MARCADOR / 6
        self.p.draw_rect((x - cel, y - cel, x + MARCADOR + cel, y + MARCADOR + cel), color=None, fill=(1, 1, 1))
        forma = self.p.new_shape()
        for i in range(6):
            for j in range(6):
                if bits[i, j] == 0:
                    forma.draw_rect((x + j * cel, y + i * cel, x + (j + 1) * cel + 0.02, y + (i + 1) * cel + 0.02))
        forma.finish(color=None, fill=self.tinta)
        forma.commit()


def desenhar_grade(d: Desenho, area, layout: dict) -> dict:
    x0, y0, x1, y1 = area
    base_id = 4 * layout["id"]
    cantos = {  # ordem: sup-esq, sup-dir, inf-dir, inf-esq
        base_id + 0: (x0, y0),
        base_id + 1: (x1 - MARCADOR, y0),
        base_id + 2: (x1 - MARCADOR, y1 - MARCADOR),
        base_id + 3: (x0, y1 - MARCADOR),
    }
    for id_, (mx, my) in cantos.items():
        d.aruco(mx, my, id_)

    alts = layout["alternativas"]
    colunas = layout["colunas"]
    assert sum(colunas) == layout["questoes"], "soma das colunas difere do nº de questões"
    larg_caixa = 2 * PAD + (len(alts) - 1) * PASSO_X + DIAM
    larg_bloco = LARG_NUM + 3 + larg_caixa
    folga = (x1 - x0 - len(colunas) * larg_bloco) / (len(colunas) + 1)
    if folga < 4:
        raise ValueError("colunas não cabem na área da grade")
    topo, fundo = y0 + MARCADOR + 7, y1 - MARCADOR - 7
    passo_y = min(PASSO_Y_MAX, (fundo - topo - 2 * 4) / max(colunas))

    bolhas = []
    q = 1
    for c, n in enumerate(colunas):
        bx = x0 + folga + c * (larg_bloco + folga) + LARG_NUM + 3
        caixa = pymupdf.Rect(bx, topo, bx + larg_caixa, topo + 8 + n * passo_y)
        d.p.draw_rect(caixa, color=d.dropout, width=1.3)
        for i in range(n):
            cy = topo + 4 + passo_y * (i + 0.5)
            d.texto(bx - 3, cy + 3.6, f"{q:02d}", "sansb", 10, alinhar="dir")
            for k, letra in enumerate(alts):
                cx = bx + PAD + DIAM / 2 + k * PASSO_X
                d.bolha(cx, cy, letra)
                bolhas.append({"q": q, "alt": letra, "cx": round(cx, 2), "cy": round(cy, 2)})
            q += 1

    return {
        "aruco_dict": ARUCO_DICT,
        "marcadores": {str(k): [round(v, 2) for v in (x, y, x + MARCADOR, y + MARCADOR)] for k, (x, y) in cantos.items()},
        "raio": DIAM / 2,
        "alternativas": alts,
        "questoes": layout["questoes"],
        "bolhas": bolhas,
    }


# ---------------------------------------------------------------- orquestração

def preparar(cfg: dict, base: Path, caderno: str) -> ModeloPreparado:
    info = cfg["cadernos"][caderno]
    mod = cfg["modelos"][info["modelo"]]
    layout = cfg["layouts"][info["layout"]]

    origem = pymupdf.open(base / cfg["pdf_origem"])
    doc = pymupdf.open()
    doc.insert_pdf(origem, from_page=mod["pagina"], to_page=mod["pagina"])
    pagina = doc[0]

    slots = _limpar(pagina, cfg, mod)
    otm = cfg.get("otimizacao", {})
    if otm.get("fundo_jpeg", True):
        _otimizar_fundo(pagina, otm.get("fundo_dpi", 200), otm.get("jpeg_qualidade", 88))
        _comprimir_imagens(pagina, otm.get("jpeg_qualidade", 88))
    d = Desenho(pagina, cfg, base)
    d.rotulo_caderno(mod["rotulo_codigo"], caderno)
    if "instrucoes_topo" in mod:
        d.instrucoes_topo(*mod["instrucoes_topo"])
    d.caixa_exemplo(mod["caixa_exemplo"])
    mapa = desenhar_grade(d, mod["area_grade"], layout)
    mapa.update({
        "caderno": caderno, "modelo": info["modelo"], "layout": info["layout"], "layout_id": layout["id"],
        "pagina_pt": [pagina.rect.width, pagina.rect.height], "qr": mod["qr"],
    })
    pagina.clean_contents()
    doc.subset_fonts()
    pdf = doc.tobytes(garbage=4, deflate=True)
    slots.sort(key=lambda s: s.origem[1])
    return ModeloPreparado(caderno=caderno, pdf=pdf, slots=slots, qr=tuple(mod["qr"]),
                           limite_dados=mod["limite_dados"], mapa=mapa)
