import pytest

from capas_prova.nomes import padronizar_nome


@pytest.mark.parametrize("entrada, esperado", [
    ("MARIA JOSÉ DA SILVA", "Maria José da Silva"),
    ("joão pedro dos santos", "João Pedro dos Santos"),
    ("PEDRO COSTA E SILVA", "Pedro Costa e Silva"),
    ("ANA COSTA-LIMA", "Ana Costa-Lima"),
    ("CARLOS D' ÁVILA", "Carlos D'Ávila"),
    ("LUCAS SOUZA NETO II", "Lucas Souza Neto II"),
    ("  BEATRIZ   ALVES.  ", "Beatriz Alves"),
    ("Ana Souza", "Ana Souza"),
    ("MaRiA dE sOuZa", "Maria de Souza"),
])
def test_padroniza(entrada, esperado):
    assert padronizar_nome(entrada)[0] == esperado


def test_parenteses_removidos_e_sinalizados():
    nome, _, conferir = padronizar_nome("João Silva (apelido)")
    assert nome == "João Silva"
    assert any("parênteses" in c for c in conferir)


def test_letra_isolada_sugere_apostrofo():
    _, _, conferir = padronizar_nome("RAFAEL D AVILA")
    assert any("D'Avila" in c for c in conferir)


def test_particulas_configuraveis():
    assert padronizar_nome("MARIA DA SILVA", particulas=())[0] == "Maria Da Silva"
