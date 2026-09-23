"""Padronização dos nomes dos estudantes.

Regra: primeira letra de cada palavra maiúscula e as demais minúsculas
("MARIA JOSÉ DA SILVA" -> "Maria José da Silva"), com os ajustes usuais em nomes:
  * partículas em minúsculas: de, da, do, das, dos, e  ("Costa E Silva" -> "Costa e Silva")
  * nomes compostos com hífen/apóstrofo: "COSTA-LIMA" -> "Costa-Lima", "D' ÁVILA" -> "D'Ávila"
  * algarismos romanos mantidos: "II", "III", "IV"
  * limpeza: espaços extras, espaço especial (nbsp), pontuação no fim, texto entre parênteses

Retorna também as observações (o que foi alterado e o que precisa ser conferido por uma pessoa).
"""
from __future__ import annotations

import re
import unicodedata

PARTICULAS_PADRAO = ("de", "da", "do", "das", "dos", "e")
ROMANOS = {"II", "III", "IV", "VI", "VII", "VIII"}


def _capitalizar(palavra: str) -> str:
    # hífen e apóstrofo: capitaliza cada parte ("abi-acl" -> "Abi-Acl", "d'avila" -> "D'Avila")
    partes = re.split(r"(['’\-])", palavra)
    return "".join(p[:1].upper() + p[1:].lower() if p not in ("'", "’", "-") else p for p in partes)


def padronizar_nome(nome: str, particulas=PARTICULAS_PADRAO, remover_parenteses=True) -> tuple[str, list[str], list[str]]:
    """Retorna (nome_padronizado, alteracoes, conferir)."""
    alteracoes: list[str] = []
    conferir: list[str] = []
    s = unicodedata.normalize("NFC", str(nome))

    if re.search(r"[  -​\t]", s):
        s = re.sub(r"[  -​\t]", " ", s)
        alteracoes.append("espaço especial trocado")
    entre_parenteses = re.findall(r"\(.*?\)", s)
    if remover_parenteses and entre_parenteses:
        conferir.append("texto entre parênteses removido: " + ", ".join(entre_parenteses))
        s = re.sub(r"\(.*?\)", " ", s)
    s2 = re.sub(r"\s*(['’])\s*", r"\1", s)            # "D' ÁVILA" -> "D'ÁVILA"
    if s2 != s:
        alteracoes.append("espaço junto ao apóstrofo removido")
        s = s2
    s2 = re.sub(r"\s*-\s*", "-", s)
    if s2 != s:
        alteracoes.append("espaço junto ao hífen removido")
        s = s2
    s2 = re.sub(r"[\s.,;:_\-]+$", "", s.strip())
    s2 = re.sub(r"^[\s.,;:_\-]+", "", s2)
    if s2 != s.strip():
        alteracoes.append("pontuação no início/fim removida")
    s = s2
    if re.search(r"\s{2,}", s):
        alteracoes.append("espaços duplicados removidos")
    s = re.sub(r"\s+", " ", s).strip()

    estranhos = sorted(set(re.findall(r"[^\w\s'’.\-]|\d", s)))
    if estranhos:
        conferir.append(f"caracteres incomuns: {' '.join(estranhos)}")

    palavras = []
    brutas = s.split(" ")
    for i, p in enumerate(brutas):
        base = p.rstrip(".")
        if p.upper() in ROMANOS and i > 0:
            palavras.append(p.upper())
        elif i > 0 and p.lower() in particulas:
            palavras.append(p.lower())
        else:
            palavras.append(_capitalizar(p))
        if len(base) == 1 and base.lower() not in particulas:
            dica = ""
            if base.upper() == "D" and i + 1 < len(brutas):   # "D AVILA" provavelmente é "D'Avila"
                dica = f" (seria D'{_capitalizar(brutas[i + 1])}?)"
            conferir.append(f"abreviação/letra isolada: '{p}'{dica}")
    final = " ".join(palavras)

    if len(palavras) < 2:
        conferir.append("nome com uma palavra só")
    if re.search(r"(.)\1\1", sem_acento(final).lower()):
        conferir.append("letra repetida 3 vezes")
    return final, alteracoes, conferir


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
