"""Rolagem de dados do T20: d20 + bônus nos testes e NdM+K no dano.

A aleatoriedade vem do gerador do NumPy, semeado pelo sistema operacional.
Todas as rolagens acontecem no servidor, então o resultado salvo no chat não
pode ser alterado pelo navegador.
"""
import re

import numpy as np

_rng = np.random.default_rng()
_DANO = re.compile(r"\s*(\d+)\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*", re.I)
_CRITICO = re.compile(r"\s*(\d+)?\s*(?:/?\s*x\s*(\d+))?\s*", re.I)


def d(faces, qtd=1):
    """Rola `qtd` dados de `faces` lados e devolve a lista de resultados."""
    return [int(x) for x in _rng.integers(1, faces + 1, size=qtd)]


def teste(bonus):
    natural = d(20)[0]
    return {"natural": natural, "bonus": bonus, "total": natural + bonus}


def critico(valor):
    """'19' -> (19, 2); '18/x3' -> (18, 3). Sem dado na ficha: margem 20 e x2."""
    m = _CRITICO.fullmatch(str(valor or ""))
    margem = int(m.group(1)) if m and m.group(1) else 20
    mult = int(m.group(2)) if m and m.group(2) else 2
    return margem, mult


def dano(expr):
    """'1d8' ou '2d6+3' -> dados rolados e total. Devolve None se a expressão não for NdM(+K)."""
    m = _DANO.fullmatch(str(expr or ""))
    if not m:
        return None
    qtd, faces, sinal, fixo = m.groups()
    qtd, faces = int(qtd), int(faces)
    if not (1 <= qtd <= 50 and 2 <= faces <= 100):
        return None
    mod = int(fixo or 0) * (-1 if sinal == "-" else 1)
    dados = d(faces, qtd)
    return {"expr": str(expr).strip(), "dados": dados, "mod": mod, "total": sum(dados) + mod}


def rolar_pericia(p):
    if not isinstance(p.get("total"), int):
        raise ValueError(f"{p['nome']} é uma perícia que só pode ser usada com treinamento.")
    return {"tipo": "pericia", "nome": p["nome"], **teste(p["total"])}


def rolar_ataque(a, chance_falha=0):
    if not isinstance(a.get("bonus"), int):
        raise ValueError(f"A ficha não tem bônus de ataque para {a['nome']}.")
    r = {"tipo": "ataque", "nome": a["nome"], **teste(a["bonus"])}
    r["margem"], r["mult"] = critico(a.get("critico"))
    r["critico"] = r["natural"] >= r["margem"]
    if chance_falha:
        resultado = d(100)[0]
        falhou = resultado <= chance_falha
        r["camuflagem"] = {"resultado": resultado, "chance": chance_falha, "falhou": falhou}
        if falhou:
            r["critico"] = False
            return r
    dn = dano(a.get("dano"))
    if dn:
        if r["critico"]:
            dn["total_critico"] = dn["total"] * r["mult"]
        r["dano"] = dn
    return r
