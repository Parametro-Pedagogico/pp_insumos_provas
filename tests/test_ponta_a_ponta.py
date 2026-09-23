"""Gera capas a partir da planilha fictícia de exemplos/, confere saídas e lê de volta as marcações."""
import csv
import json
import random
from pathlib import Path

import pandas as pd

from capas_prova import gerar
from capas_prova.leitor import carregar_layouts, ler
from capas_prova.simular import AZUL, PRETO, marcar, renderizar

RAIZ = Path(__file__).resolve().parent.parent
PLANILHA = RAIZ / "exemplos" / "planilha_exemplo.csv"


def test_gera_verifica_e_le(tmp_path):
    assert gerar.main(["--planilha", str(PLANILHA), "--saida", str(tmp_path)]) == 0

    base = pd.read_csv(PLANILHA, dtype=str)
    manifesto = list(csv.DictReader(open(tmp_path / "manifesto.csv", encoding="utf-8-sig")))
    assert len(manifesto) == len(base)                       # uma capa por aluno
    assert all((tmp_path / m["arquivo"]).exists() for m in manifesto)
    assert len({m["codigo_cartao"] for m in manifesto}) == len(manifesto)
    assert (tmp_path / "sorteio_cadernos.csv").exists()

    resumo = pd.read_excel(tmp_path / "verificacao.xlsx", sheet_name="Resumo por escola")
    total = resumo.iloc[-1]
    assert total["Capas faltando"] == 0 and total["Capas geradas"] == len(base)
    assert total["Diferença cadernos"] <= 1
    alunos = pd.read_excel(tmp_path / "verificacao.xlsx", sheet_name="Alunos", dtype=str)
    assert "Aluna Exemplo Um" in set(alunos["Nome na capa"])

    layouts = carregar_layouts(tmp_path / "layouts")
    rnd = random.Random(0)
    for m, cor in zip(manifesto[:3], (PRETO, AZUL, PRETO)):
        img = renderizar(tmp_path / m["arquivo"])
        mapa = json.loads((tmp_path / "layouts" / f"{m['caderno']}.json").read_text(encoding="utf-8"))
        esperado = marcar(img, mapa, rnd, cor)
        r = ler(img, layouts)
        assert r.codigo_cartao == m["codigo_cartao"]
        assert r.respostas == esperado


def test_validar_nao_gera_pdf(tmp_path):
    assert gerar.main(["--planilha", str(PLANILHA), "--saida", str(tmp_path), "--validar"]) == 0
    assert not (tmp_path / "capas").exists()
    assert (tmp_path / "verificacao_previa.xlsx").exists()
