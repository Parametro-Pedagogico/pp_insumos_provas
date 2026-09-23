from capas_prova.codigos import RegistroCodigos, codigo_valido, normalizar


def test_codigo_deterministico_e_valido(tmp_path):
    r1 = RegistroCodigos(tmp_path / "a.csv")
    r2 = RegistroCodigos(tmp_path / "b.csv")
    c = r1.obter("2026-2", "900000001", "C0201")
    assert c == r2.obter("2026-2", "900000001", "C0201")
    assert len(c) == 9 and codigo_valido(c)
    assert c != r1.obter("2026-2", "900000001", "C0202")


def test_digito_verificador_detecta_erro_de_digitacao(tmp_path):
    c = RegistroCodigos(tmp_path / "r.csv").obter("2026-2", "900000001", "C0201")
    for i in range(len(c)):
        outro = "0" if c[i] != "0" else "1"
        assert not codigo_valido(c[:i] + outro + c[i + 1:])


def test_registro_persiste_e_garante_unicidade(tmp_path):
    arq = tmp_path / "r.csv"
    r = RegistroCodigos(arq)
    codigos = {r.obter("2026-2", f"{i:09d}", "C0501") for i in range(2000)}
    assert len(codigos) == 2000
    r.salvar()
    assert RegistroCodigos(arq).obter("2026-2", "000000007", "C0501") == r.obter("2026-2", "000000007", "C0501")


def test_normalizar_confusoes_comuns():
    assert normalizar(" o1l-i ") == "0111"
