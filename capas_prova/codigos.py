"""Geração do código único do cartão (conteúdo do QR Code).

Formato: 8 caracteres de dados + 1 dígito verificador, alfabeto Crockford Base32
(sem I, L, O, U, para evitar confusão na digitação manual). Ex.: "7K3QZM8P2".

O código é determinístico: a mesma combinação (edição, RA, caderno) sempre gera o mesmo código,
então regerar as capas (p.ex. após corrigir um nome) não invalida cartões já impressos.
Um registro em CSV guarda todos os códigos emitidos e garante unicidade entre lotes.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
N = len(ALFABETO)
TAM_DADOS = 8


def _digito_verificador(dados: str) -> str:
    """Luhn mod N: detecta qualquer erro de um caractere e a maioria das trocas de vizinhos."""
    fator, soma = 2, 0
    for ch in reversed(dados):
        v = fator * ALFABETO.index(ch)
        soma += v // N + v % N
        fator = 1 if fator == 2 else 2
    return ALFABETO[(N - soma % N) % N]


def codigo_valido(codigo: str) -> bool:
    codigo = normalizar(codigo)
    if len(codigo) != TAM_DADOS + 1 or any(c not in ALFABETO for c in codigo):
        return False
    return _digito_verificador(codigo[:-1]) == codigo[-1]


def normalizar(codigo: str) -> str:
    """Corrige confusões comuns de leitura humana (O->0, I/L->1) e remove separadores."""
    c = codigo.strip().upper().replace("-", "").replace(" ", "")
    return c.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _derivar(chave: str, sal: int) -> str:
    h = hashlib.sha256(f"{chave}|{sal}".encode()).digest()
    n = int.from_bytes(h[:5], "big")  # 40 bits = 8 caracteres base32
    dados = "".join(ALFABETO[(n >> (5 * i)) & 31] for i in reversed(range(TAM_DADOS)))
    return dados + _digito_verificador(dados)


class RegistroCodigos:
    """Mantém chave -> código de forma persistente e garante que não haja colisões."""

    CAMPOS = ["codigo_cartao", "chave"]

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.por_chave: dict[str, str] = {}
        self.por_codigo: dict[str, str] = {}
        if self.caminho.exists():
            with open(self.caminho, newline="", encoding="utf-8") as f:
                for linha in csv.DictReader(f):
                    self._registrar(linha["chave"], linha["codigo_cartao"])

    def _registrar(self, chave: str, codigo: str) -> None:
        dono = self.por_codigo.get(codigo)
        if dono is not None and dono != chave:
            raise ValueError(f"Código {codigo} já pertence a '{dono}', não pode ser usado por '{chave}'")
        self.por_chave[chave] = codigo
        self.por_codigo[codigo] = chave

    def obter(self, edicao: str, ra: str, caderno: str, informado: str | None = None) -> str:
        chave = f"{edicao}|{ra}|{caderno}"
        if informado:
            self._registrar(chave, informado.strip().upper())
            return self.por_chave[chave]
        if chave in self.por_chave:
            return self.por_chave[chave]
        sal = 0
        while (codigo := _derivar(chave, sal)) in self.por_codigo:
            sal += 1
        self._registrar(chave, codigo)
        return codigo

    def salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(self.caminho, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.CAMPOS)
            for chave, codigo in sorted(self.por_chave.items()):
                w.writerow([codigo, chave])
