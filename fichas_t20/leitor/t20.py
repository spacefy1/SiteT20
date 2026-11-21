"""Leitor da ficha T20 (modelo "Ficha Altoe"): aba PJ -> dict pronto para JSON.

A aba PJ é um formulário (células espalhadas, muitas mescladas), não uma tabela.
Por isso lemos a grade inteira com header=None e buscamos cada campo pela
coordenada da célula (ex.: "S2" = raça). O pandas lê o valor já calculado
pelo Excel/Sheets, então os totais de perícia, defesa, PV etc. vêm prontos.
"""
import re

import numpy as np
import pandas as pd

ABA = "PJ"

# Atributo -> (célula do modificador final, célula do valor base antes da raça)
ATRIBUTOS = {
    "FOR": ("C10", "C12"), "DES": ("C15", "C17"), "CON": ("C20", "C22"),
    "INT": ("C25", "C27"), "SAB": ("C30", "C32"), "CAR": ("C35", "C37"),
}
LINHAS_PERICIAS = range(8, 37)    # I=nome, G=treinada(1/0), M=atributo, H=total, O=outros
LINHAS_ATAQUES = range(26, 32)    # Q=nome, Z=teste, AD=bônus, AG=dano, AK=crítico, AO=tipo, AT=alcance
LINHAS_ITENS = range(49, 73)      # Q=quantidade, U=nome, AH=peso
LINHAS_RACIAL = range(39, 46)     # AM=marcador (R/D/O), AP=texto
LINHAS_CLASSE = range(47, 67)     # AM=nível (N1..N20), AP=texto


def _indices(a1):
    """'AO3' -> (linha, coluna) base 0."""
    letras, linha = re.fullmatch(r"([A-Z]+)(\d+)", a1).groups()
    col = 0
    for ch in letras:
        col = col * 26 + ord(ch) - 64
    return int(linha) - 1, col - 1


class Grade:
    """Acesso por coordenada A1 sobre o DataFrame da aba."""

    def __init__(self, df):
        self.df = df

    def __call__(self, a1):
        lin, col = _indices(a1)
        if lin >= self.df.shape[0] or col >= self.df.shape[1]:
            return None
        v = self.df.iat[lin, col]
        if isinstance(v, np.generic):
            v = v.item()
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, str):
            v = v.replace("\xad", "").strip()
            return v or None
        return v


def _limpa_pericia(nome):
    """Remove os símbolos do nome e devolve (nome, só_treinada, penalidade_armadura)."""
    base = re.sub(r"[✯✠\s]+$", "", nome.replace("\n", " ")).strip()
    return base, "✯" in nome, "✠" in nome


def ler_ficha(arquivo):
    """`arquivo` pode ser caminho ou objeto de arquivo (BytesIO, upload do Flask)."""
    try:
        df = pd.read_excel(arquivo, sheet_name=ABA, header=None, engine="openpyxl")
    except ValueError as e:
        raise ValueError(f"Aba '{ABA}' não encontrada. Esta planilha não parece ser uma ficha.") from e
    g = Grade(df)

    if not g("B3"):
        raise ValueError("Ficha sem nome do personagem (célula B3 vazia).")

    classes = [{"classe": g("S4"), "nivel": g("Z4")}]
    for cls, niv in (("AD4", "AK4"), ("AD5", "AK5")):
        if g(cls) not in (None, "-") and g(niv):
            classes.append({"classe": g(cls), "nivel": g(niv)})

    pericias = []
    for r in LINHAS_PERICIAS:
        if not g(f"I{r}"):
            continue
        nome, so_treinada, pen_armadura = _limpa_pericia(g(f"I{r}"))
        total = g(f"H{r}")
        pericias.append({
            "nome": nome,
            "atributo": g(f"M{r}"),
            "treinada": g(f"G{r}") == 1,
            "so_treinada": so_treinada,
            "penalidade_armadura": pen_armadura,
            "outros": g(f"O{r}") or 0,
            "total": total if isinstance(total, (int, float)) else None,  # "-" = não pode usar
        })

    ataques = [
        {"nome": g(f"Q{r}"), "teste": g(f"Z{r}"), "bonus": g(f"AD{r}"), "dano": g(f"AG{r}"),
         "critico": g(f"AK{r}"), "tipo": g(f"AO{r}"), "alcance": g(f"AT{r}")}
        for r in LINHAS_ATAQUES if g(f"Q{r}")
    ]
    itens = [
        {"nome": g(f"U{r}"), "quantidade": g(f"Q{r}"), "peso": g(f"AH{r}")}
        for r in LINHAS_ITENS if g(f"U{r}")
    ]

    return {
        "nome": g("B3"), "jogador": g("AO2"), "raca": g("S2"), "tipo": g("G2"),
        "origem": g("Z2"), "deus": g("C6"), "tamanho": g("AH33"),
        "nivel": g("AW3"), "classes": classes, "xp": g("AO4"), "xp_proximo_nivel": g("AR4"),
        "atributos": {a: {"mod": g(m), "base": g(b)} for a, (m, b) in ATRIBUTOS.items()},
        "pv": {"atual": g("S14"), "max": g("V14")},
        "pm": {"atual": g("AA14"), "max": g("AD14")},
        "defesa": g("R9"), "deslocamento": g("Z9"), "cd_magia": g("W33"),
        "bonus_treinamento": g("L37"),
        "armadura": {"nome": g("Q39"), "defesa": g("X39"), "penalidade": g("AB39"), "proficiencia": g("AF39")},
        "escudo": {"nome": g("Q41"), "defesa": g("X41"), "penalidade": g("AB41")},
        "pericias": pericias, "ataques": ataques, "itens": itens,
        "dinheiro_t": g("Q44"), "carga": {"atual": g("AE44"), "max": g("AH44")},
        "racial_origem": [{"marcador": g(f"AM{r}"), "texto": g(f"AP{r}")} for r in LINHAS_RACIAL if g(f"AP{r}")],
        "classe_geral": [{"nivel": g(f"AM{r}"), "texto": g(f"AP{r}")} for r in LINHAS_CLASSE if g(f"AP{r}")],
    }
