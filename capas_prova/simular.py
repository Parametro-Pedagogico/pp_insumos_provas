"""Teste de ponta a ponta: marca respostas aleatórias nas capas geradas, simula impressão/digitalização
em vários cenários e confere se o leitor recupera o código do cartão e as respostas.

    python -m capas_prova.simular --saida saida_teste/ [--repeticoes 3] [--imagens pasta/]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .leitor import carregar_layouts, ler

DPI = 200
PRETO = (25, 25, 25)
AZUL = (120, 45, 20)     # BGR de caneta esferográfica azul


def renderizar(pdf: Path, dpi=DPI) -> np.ndarray:
    pix = pymupdf.open(pdf)[0].get_pixmap(dpi=dpi)
    im = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    return cv2.cvtColor(im, cv2.COLOR_RGB2BGR)


def marcar(img, mapa, rnd: random.Random, cor):
    """Preenche bolhas como um aluno faria (tamanho/posição imperfeitos). Retorna o gabarito esperado."""
    esc = DPI / 72
    alts = mapa["alternativas"]
    por_q: dict[int, dict] = {}
    for b in mapa["bolhas"]:
        por_q.setdefault(b["q"], {})[b["alt"]] = b
    esperado = {}
    for q, bs in por_q.items():
        s = rnd.random()
        if s < 0.05:
            escolha = []
        elif s < 0.08:
            escolha = rnd.sample(alts, 2)
        else:
            escolha = [rnd.choice(alts)]
        esperado[q] = "" if not escolha else (escolha[0] if len(escolha) == 1 else "*")
        for a in escolha:
            b = bs[a]
            cx = (b["cx"] + rnd.uniform(-1.0, 1.0)) * esc
            cy = (b["cy"] + rnd.uniform(-1.0, 1.0)) * esc
            r = mapa["raio"] * rnd.uniform(0.8, 1.1) * esc
            cv2.ellipse(img, (int(cx), int(cy)), (int(r), int(r * rnd.uniform(0.8, 1.0))),
                        rnd.uniform(0, 180), 0, 360, cor, -1, lineType=cv2.LINE_AA)
    return esperado


def cenario(img, nome, rnd: random.Random):
    if nome == "scanner_cor":
        return img
    if nome == "scanner_cinza":
        return cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    if nome == "foto_celular":
        h, w = img.shape[:2]
        d = 0.03 * w
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32([[rnd.uniform(0, d), rnd.uniform(0, d)], [w - rnd.uniform(0, d), rnd.uniform(0, d)],
                          [w - rnd.uniform(0, d), h - rnd.uniform(0, d)], [rnd.uniform(0, d), h - rnd.uniform(0, d)]])
        out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(200, 200, 200))
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rnd.uniform(-5, 5), 0.9)
        out = cv2.warpAffine(out, M, (w, h), borderValue=(180, 180, 180))
        grad = np.linspace(0.75, 1.0, w, dtype=np.float32)[None, :, None]   # iluminação desigual
        out = np.clip(out.astype(np.float32) * grad, 0, 255).astype(np.uint8)
        out = cv2.GaussianBlur(out, (3, 3), 0)
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if nome == "de_cabeca_para_baixo":
        return cv2.rotate(img, cv2.ROTATE_180)
    raise ValueError(nome)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", type=Path, required=True)
    ap.add_argument("--repeticoes", type=int, default=2)
    ap.add_argument("--imagens", type=Path, help="salva as imagens simuladas nesta pasta")
    args = ap.parse_args(argv)

    layouts = carregar_layouts(args.saida / "layouts")
    manifesto = list(csv.DictReader(open(args.saida / "manifesto.csv", encoding="utf-8-sig")))
    rnd = random.Random(42)
    cenarios = [  # (nome, impressão P&B?, cor da caneta)
        ("scanner_cor", False, PRETO), ("scanner_cor", False, AZUL),
        ("scanner_cinza", False, PRETO), ("scanner_cinza", False, AZUL),
        ("scanner_cor", True, PRETO),            # escola imprimiu em P&B
        ("foto_celular", False, PRETO), ("foto_celular", True, AZUL),
        ("de_cabeca_para_baixo", False, PRETO),
    ]
    totais = {}
    for linha in manifesto:
        base = renderizar(args.saida / linha["arquivo"])
        for nome, pb, cor in cenarios:
            for rep in range(args.repeticoes):
                img = base.copy()
                if pb:  # impressora P&B: a grade vermelha vira cinza
                    img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
                mapa = json.loads((args.saida / "layouts" / f"{linha['caderno']}.json").read_text(encoding="utf-8"))
                esperado = marcar(img, mapa, rnd, cor)
                img = cenario(img, nome, rnd)
                rotulo = f"{nome}{'+impressao_pb' if pb else ''}+{'azul' if cor == AZUL else 'preta'}"
                if args.imagens and rep == 0:
                    args.imagens.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(args.imagens / f"{linha['caderno']}_{rotulo}.jpg"), img)
                t = totais.setdefault(rotulo, {"folhas": 0, "qr_ok": 0, "questoes": 0, "acertos": 0, "falhas": 0})
                t["folhas"] += 1
                try:
                    r = ler(img, layouts)
                except Exception as e:  # noqa: BLE001
                    t["falhas"] += 1
                    print("FALHA", rotulo, linha["arquivo"], e)
                    continue
                t["qr_ok"] += r.codigo_cartao == linha["codigo_cartao"]
                t["questoes"] += len(esperado)
                t["acertos"] += sum(r.respostas.get(q) == v for q, v in esperado.items())
    print(f"{'cenário':42s} {'folhas':>6s} {'QR lido':>8s} {'respostas corretas':>20s}")
    for k, t in totais.items():
        pct = 100 * t["acertos"] / t["questoes"] if t["questoes"] else 0
        print(f"{k:42s} {t['folhas']:6d} {t['qr_ok']:5d}/{t['folhas']:<3d} {t['acertos']:8d}/{t['questoes']:<6d} ({pct:.2f}%)"
              + (f"  falhas={t['falhas']}" if t["falhas"] else ""))


if __name__ == "__main__":
    main()
