"""Remove dados pessoais do PDF exportado do Canva antes de colocá-lo no repositório.

O modelo do Canva costuma vir com os dados de um aluno de exemplo (texto e QR antigo) e com o nome
de quem editou nos metadados. Este script:
  * troca o texto dos 6 campos (ESCOLA, ANO / SÉRIE, TURMA, ESTUDANTE, RA/CÓDIGO, CÓDIGO DO CARTÃO)
    por marcadores genéricos, mantendo posição, fonte, tamanho e cor (o gerador usa essas medidas);
  * deixa em branco as imagens que o gerador descarta (QR antigo com os dados do aluno e recortes do
    cartão antigo), mantendo o tamanho em pixels (o gerador as identifica por `imagens_remover`);
  * remove a árvore de acessibilidade e os marcadores (bookmarks), que repetem o texto dos campos;
  * limpa os metadados (autor, título, palavras-chave).

    python ferramentas/anonimizar_modelo.py Capa_do_Canva.pdf modelos/Capa_Caderno_Prova.pdf
"""
from __future__ import annotations

import sys
import zlib
from pathlib import Path

import pymupdf
import yaml

RAIZ = Path(__file__).resolve().parent.parent
MARCADORES = {
    "ESCOLA": "ESCOLA: NOME DA ESCOLA",
    "ANO / SÉRIE": "ANO / SÉRIE: 0º ANO",
    "TURMA": "TURMA: NOME DA TURMA",
    "ESTUDANTE": "ESTUDANTE: NOME DO ESTUDANTE",
    "RA/CÓDIGO": "RA/CÓDIGO: 000000000",
    "CÓDIGO DO CARTÃO": "CÓDIGO DO CARTÃO: 00000000",
}


def anonimizar(origem: Path, destino: Path) -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))
    tamanhos_qr = {tuple(t) for t in cfg["imagens_remover"]}
    fonte = str(RAIZ / cfg["fontes"]["dados_regular"])
    doc = pymupdf.open(origem)

    for pagina in doc:
        # 1) texto dos campos
        trocas = []
        for bloco in pagina.get_text("dict")["blocks"]:
            for linha in bloco.get("lines", []):
                for span in linha["spans"]:
                    txt = span["text"].strip().upper()
                    rot = next((r for r in MARCADORES if txt.startswith(r + ":")), None)
                    if rot:
                        c = span["color"]
                        cor = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
                        trocas.append((rot, span["origin"], span["size"], cor))
                        pagina.add_redact_annot(pymupdf.Rect(span["bbox"]), fill=False)
        pagina.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        pagina.insert_font(fontname="aleo", fontfile=fonte)
        for rot, origem_txt, tam, cor in trocas:
            pagina.insert_text(origem_txt, MARCADORES[rot], fontname="aleo", fontsize=tam, color=cor)

        # 2) imagens que o gerador descarta (QR antigo e recortes do cartão antigo) -> brancas, mesmo tamanho
        for xref, _, w, h, *_ in pagina.get_images(full=True):
            if (w, h) in tamanhos_qr:
                branco = zlib.compress(b"\xff" * (w * h * 3))
                doc.update_stream(xref, branco, compress=False)
                doc.xref_set_key(xref, "Filter", "/FlateDecode")
                doc.xref_set_key(xref, "DecodeParms", "null")
                doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")

    # o Canva também copia o texto para a árvore de acessibilidade e para os marcadores (bookmarks)
    cat = doc.pdf_catalog()
    for chave in ("StructTreeRoot", "MarkInfo", "Outlines", "ParentTree"):
        doc.xref_set_key(cat, chave, "null")
    for pagina in doc:
        doc.xref_set_key(pagina.xref, "StructParents", "null")
    doc.set_toc([])
    doc.set_metadata({"title": "Modelo de capa (anonimizado)", "author": "", "subject": "",
                      "keywords": "", "creator": "Canva", "producer": "capas_prova"})
    doc.del_xml_metadata()
    doc.subset_fonts()
    doc.save(destino, garbage=4, deflate=True)
    verificar_limpo(destino, [s for s in _textos_originais(origem)])


def _textos_originais(origem: Path) -> list[str]:
    """Valores de exemplo que estavam nos campos do modelo original (para conferir que sumiram)."""
    valores = []
    for pagina in pymupdf.open(origem):
        for linha in pagina.get_text().splitlines():
            rot = next((r for r in MARCADORES if linha.strip().upper().startswith(r + ":")), None)
            if rot and linha.split(":", 1)[1].strip():
                valores.append(linha.split(":", 1)[1].strip())
    return valores


def verificar_limpo(arquivo: Path, proibidos: list[str]) -> None:
    doc = pymupdf.open(arquivo)
    conteudo = doc.tobytes(expand=255).decode("latin-1")
    for x in range(1, doc.xref_length()):
        conteudo += doc.xref_object(x, compressed=False)
    restos = sorted({p for p in proibidos if p and len(p) > 3 and p in conteudo})
    if restos:
        raise SystemExit(f"ATENÇÃO: ainda há dados do modelo original no PDF: {restos}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    anonimizar(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Modelo anonimizado salvo em {sys.argv[2]}")
