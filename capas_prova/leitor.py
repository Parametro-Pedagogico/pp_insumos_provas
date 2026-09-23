"""Protótipo de leitura do cartão-resposta (prova de viabilidade para o leitor definitivo).

Etapas:
  1. Marcadores ArUco -> identifica o layout (ids 4*L..4*L+3) e a orientação da folha.
  2. Homografia marcadores(pt) -> imagem(px): corrige escala, rotação, inclinação e perspectiva.
  3. Mapa de tinta:
       - digitalização colorida: canal vermelho (a grade vermelha some, fica só a caneta);
       - digitalização em tons de cinza / impressão P&B (escape): limiar em tom escuro,
         abaixo do cinza em que a grade vermelha se transforma.
  4. Para cada bolha mede a fração de pixels com tinta no disco interno.
  5. QR Code -> código do cartão (com tentativas no canal vermelho e ampliado).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

LIMIAR_MARCADA = 0.40   # fração mínima de tinta para considerar marcada
LIMIAR_DUVIDA = 0.20    # entre DUVIDA e MARCADA -> marcação fraca/rasura


@dataclass
class Resultado:
    codigo_cartao: str | None
    caderno: str | None
    respostas: dict[int, str]          # questão -> "A".."D", "" (branco), "*" (múltipla), "?" (dúvida)
    escores: dict[int, list[float]]
    modo: str
    avisos: list[str]


def carregar_layouts(pasta: Path) -> dict[int, dict]:
    """layout_id -> mapa (qualquer caderno do layout serve: a geometria é a mesma)."""
    out = {}
    for arq in sorted(Path(pasta).glob("*.json")):
        m = json.loads(arq.read_text(encoding="utf-8"))
        out.setdefault(m["layout_id"], m)
    return out


def _ler_qr(img_bgr) -> str | None:
    det = cv2.QRCodeDetector()
    tentativas = [img_bgr, img_bgr[:, :, 2], cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)]
    for t in tentativas:
        for escala in (1.0, 0.5, 1.5):
            im = t if escala == 1.0 else cv2.resize(t, None, fx=escala, fy=escala)
            txt, _, _ = det.detectAndDecode(im)
            if txt:
                return txt
    return None


def _modo(img_bgr) -> str:
    """'cor' se a grade vermelha está presente (saturação), senão 'cinza'."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    vermelho = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170)) & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 120)
    return "cor" if vermelho.mean() > 0.002 else "cinza"


def ler(img_bgr: np.ndarray, layouts: dict[int, dict], codigo_por_qr: dict[str, str] | None = None) -> Resultado:
    avisos: list[str] = []
    cinza = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    cantos, ids, _ = cv2.aruco.ArucoDetector(dic, cv2.aruco.DetectorParameters()).detectMarkers(cinza)
    if ids is None:
        raise ValueError("nenhum marcador encontrado")
    ids = ids.ravel().tolist()
    votos = {}
    for i in ids:
        votos[i // 4] = votos.get(i // 4, 0) + 1
    layout_id = max(votos, key=votos.get)
    mapa = layouts[layout_id]
    achados = {i: c[0] for i, c in zip(ids, cantos) if i // 4 == layout_id}
    if len(achados) < 3:
        raise ValueError(f"apenas {len(achados)} marcadores do layout {layout_id} encontrados")
    if len(achados) == 3:
        avisos.append("um marcador não encontrado; alinhamento com 3 marcadores")

    # correspondência pelos 4 cantos de cada marcador (até 16 pontos) -> homografia robusta
    src, dst = [], []
    for i, c in achados.items():
        x0, y0, x1, y1 = mapa["marcadores"][str(i)]
        src += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        dst += c.tolist()
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 3.0)

    modo = _modo(img_bgr)
    if modo == "cor":
        tinta = img_bgr[:, :, 2] < 110          # canal vermelho: grade vermelha ~240, caneta < 110
    else:
        tinta = cinza < 95                       # grade vermelha em cinza ~135, caneta < 95
        avisos.append("modo cinza (sem cor na digitalização): leitura por limiar escuro")

    pts = np.float32([[b["cx"], b["cy"]] for b in mapa["bolhas"]]).reshape(-1, 1, 2)
    centros = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    # escala px/pt local (média) para o raio
    esc = np.sqrt(abs(np.linalg.det(H[:2, :2] / H[2, 2])))
    raio = mapa["raio"] * esc * 0.72
    yy, xx = np.mgrid[-int(raio) - 1:int(raio) + 2, -int(raio) - 1:int(raio) + 2]
    disco = (xx ** 2 + yy ** 2) <= raio ** 2

    escores: dict[int, list[float]] = {}
    for b, (cx, cy) in zip(mapa["bolhas"], centros):
        x, y = int(round(cx)), int(round(cy))
        r = disco.shape[0] // 2
        janela = tinta[y - r:y + r + 1, x - r:x + r + 1]
        f = float(janela[disco].mean()) if janela.shape == disco.shape else 0.0
        escores.setdefault(b["q"], []).append(round(f, 3))

    alts = mapa["alternativas"]
    respostas = {}
    for q, sc in escores.items():
        marcadas = [alts[i] for i, s in enumerate(sc) if s >= LIMIAR_MARCADA]
        if len(marcadas) == 1:
            respostas[q] = marcadas[0]
        elif len(marcadas) > 1:
            respostas[q] = "*"
        elif max(sc) >= LIMIAR_DUVIDA:
            respostas[q] = "?"
        else:
            respostas[q] = ""

    codigo = _ler_qr(img_bgr)
    if codigo is None:
        avisos.append("QR Code não lido: usar o código impresso (CÓDIGO DO CARTÃO) para identificar")
    caderno = (codigo_por_qr or {}).get(codigo) if codigo else None
    return Resultado(codigo, caderno, respostas, escores, modo, avisos)
