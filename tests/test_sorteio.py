import random
from collections import Counter

import pandas as pd
import pytest

from capas_prova.sorteio import sortear

CFG = {"cadernos_por_serie": {2: ["C0201", "C0202"], 5: ["C0501", "C0502"], 9: ["C0901", "C0902"]},
       "sorteio": {"semente": "teste"}}


def base_sintetica(seed=1):
    rnd = random.Random(seed)
    linhas, ra = [], 100000000
    for e in range(12):
        for serie in ("2º ANO", "5º ANO", "9º ANO"):
            for t in range(rnd.randint(1, 3)):
                for _ in range(rnd.randint(5, 33)):
                    ra += 1
                    linhas.append({"escola": f"ESCOLA {e}", "serie": serie, "turma": f"{serie} T{t}",
                                   "ra": str(ra), "estudante": f"Aluno {ra}"})
    return pd.DataFrame(linhas)


@pytest.fixture
def df():
    return base_sintetica()


def _dif(contagem: Counter) -> int:
    return abs(contagem[0] - contagem[1])


def test_equilibrio_turma_escola_total(df, tmp_path):
    mapa, _ = sortear(df, CFG, tmp_path)
    df["cad"] = df["ra"].map(mapa)
    df["n"] = df["cad"].str[-1].astype(int) - 1
    assert df["cad"].notna().all()
    for _, g in df.groupby(["escola", "turma"]):
        assert _dif(Counter(g["n"])) <= 1
    for _, g in df.groupby(["escola", "serie"]):
        assert _dif(Counter(g["n"])) <= 1
    for _, g in df.groupby("escola"):
        assert _dif(Counter(g["n"])) <= 1
    assert _dif(Counter(df["n"])) <= 1
    # cada aluno recebe um caderno da própria série
    assert (df["cad"].str[1:3] == df["serie"].str[0].str.zfill(2)).all()


def test_reprodutivel_independente_da_ordem(df, tmp_path):
    a, _ = sortear(df, CFG, tmp_path / "a")
    b, _ = sortear(df.sample(frac=1, random_state=3), CFG, tmp_path / "b")
    assert a == b


def test_estavel_com_arquivo_salvo_e_aluno_novo(df, tmp_path):
    antes, _ = sortear(df, CFG, tmp_path)
    novo = df.iloc[[0]].assign(ra="999999999")
    depois, _ = sortear(pd.concat([df.iloc[5:], novo]), CFG, tmp_path)
    assert all(depois[ra] == antes[ra] for ra in df["ra"].iloc[5:])
    assert depois["999999999"] in ("C0201", "C0202", "C0501", "C0502", "C0901", "C0902")


def test_coluna_caderno_da_planilha_e_respeitada(df, tmp_path):
    df = df.assign(caderno="")
    df.loc[0, "caderno"] = f"C0{df.loc[0, 'serie'][0]}02"   # 2º caderno da série do aluno
    mapa, _ = sortear(df, CFG, tmp_path)
    assert mapa[df.loc[0, "ra"]] == df.loc[0, "caderno"]
