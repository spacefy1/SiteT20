"""Leitor da ficha T20 (modelo "Ficha Altoe"): abas PJ, Raça e Poderes escolhidos -> dict pronto para JSON.

A aba PJ é um formulário (células espalhadas, muitas mescladas), não uma tabela.
`ler_ficha` recebe uma função `celula("S2") -> valor` e busca cada campo pela
coordenada ("S2" é da PJ; outras abas vêm com o nome, "Poderes escolhidos!B5").
A função pode vir do pycel (recálculo no servidor) ou da grade lida pelo pandas
(valores já calculados pelo Excel/Sheets); as duas normalizam do mesmo jeito
com `normalizar`.
"""
import re

import numpy as np
import pandas as pd

ABA = "PJ"
ABA_PODERES = "Poderes escolhidos"
ABA_RACA = "Raça"

# Atributo -> (célula do modificador final, célula do valor base antes da raça)
ATRIBUTOS = {
    "FOR": ("C10", "C12"), "DES": ("C15", "C17"), "CON": ("C20", "C22"),
    "INT": ("C25", "C27"), "SAB": ("C30", "C32"), "CAR": ("C35", "C37"),
}
LINHAS_PERICIAS = range(8, 37)    # I=nome, G=treinada(1/0), M=atributo, H=total
AJUSTES_PERICIA = 21              # Ajustes!B{linha + 21} = outros (manual), C = poderes ativos (fórmula)
LINHAS_ATAQUES = range(26, 32)    # Q=nome, Z=teste, AD=bônus, AG=dano, AK=crítico, AO=tipo, AT=alcance
LINHAS_ITENS = range(49, 73)      # Q=quantidade, U=nome, AH=peso
LINHA_ORIGEM = 40                 # PJ!AP40; AP39 (raça) e AP41:AP45 (nomes dos traços) dão lugar à aba Raça
LINHAS_ESCOLHAS = range(11, 28)   # aba Raça: A=escolha prevista, B=do jogador, C=situação, D=regra
LINHAS_TRACOS = range(32, 69)     # aba Raça: um traço por linha, "Nome. Descrição"
LINHAS_PODERES = range(5, 105)    # aba Poderes escolhidos
COLUNAS_PODER = {"ativo": "A", "opcao": "B", "nome": "C", "livro": "D", "pagina": "E", "nivel_minimo": "F",
                 "pre_requisitos": "G", "regra": "H", "aviso": "I", "aplicacao": "J"}
NIVEL_NA_FICHA = 42               # PJ!AM47:AM66 anota o nível dos 20 primeiros poderes (linha do poder + 42)


def endereco(a1):
    """'S2' -> 'PJ!S2'; endereços com aba ficam como estão."""
    return a1 if "!" in a1 else f"{ABA}!{a1}"


def _indices(a1):
    """'AO3' -> (linha, coluna) base 0."""
    letras, linha = re.fullmatch(r"([A-Z]+)(\d+)", a1).groups()
    col = 0
    for ch in letras:
        col = col * 26 + ord(ch) - 64
    return int(linha) - 1, col - 1


def normalizar(v):
    """Float inteiro vira int, string vazia vira None, tira o hífen invisível (\xad)."""
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


class Grade:
    """Acesso por coordenada A1 sobre o DataFrame da aba."""

    def __init__(self, df):
        self.df = df

    def __call__(self, a1):
        lin, col = _indices(a1)
        if lin >= self.df.shape[0] or col >= self.df.shape[1]:
            return None
        return normalizar(self.df.iat[lin, col])


def _poderes(g):
    """Linhas da aba Poderes escolhidos com uma opção escolhida (C a J são fórmulas sobre o BD CATÁLOGO)."""
    poderes = []
    for r in LINHAS_PODERES:
        if g(f"{ABA_PODERES}!B{r}") in (None, "-"):
            continue
        p = {"linha": r, **{chave: g(f"{ABA_PODERES}!{col}{r}") for chave, col in COLUNAS_PODER.items()}}
        p["ativo"] = p["ativo"] == 1
        p["nivel_na_ficha"] = g(f"AM{r + NIVEL_NA_FICHA}") if r + NIVEL_NA_FICHA <= 66 else None
        poderes.append(p)
    return poderes


def _treinada_pela_raca(r):
    """'BD TRAÇOS'!A12 corresponde à perícia da PJ linha 8, B12 à linha 9, ..., AC12 à linha 36."""
    letras, n = "", r - 7
    while n:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return f"BD TRAÇOS!{letras}12"


def _sem_zero_final(texto):
    """Raça!D11:D27 junta descrição + CHAR(10) + orientação; orientação vazia vira um "0" na última linha."""
    if not isinstance(texto, str):
        return None if texto in (None, 0) else texto
    linhas = texto.split("\n")
    while linhas and linhas[-1].strip() in ("", "0"):
        linhas.pop()
    return "\n".join(linhas).strip() or None


def _traco(texto):
    """'Duro como Pedra. Você recebe...' -> nome até o primeiro ponto (como PJ!AP41) e o resto."""
    nome, ponto, resto = str(texto).partition(".")
    return {"nome": nome.strip(), "texto": resto.strip() or None} if ponto else {"nome": str(texto).strip(), "texto": None}


def _raca(g):
    def r(a1):
        return g(f"{ABA_RACA}!{a1}")

    escolhas = []
    for l in LINHAS_ESCOLHAS:
        rotulo, escolha = r(f"A{l}"), r(f"B{l}")
        if rotulo in (None, 0) and escolha is None:
            continue
        escolhas.append({"linha": l, "rotulo": rotulo if rotulo != 0 else None, "escolha": escolha,
                         "situacao": r(f"C{l}"), "descricao": _sem_zero_final(r(f"D{l}"))})
    nome = r("B3")
    return {
        "nome": None if nome in (None, "-") else nome, "fonte": r("D3"), "resumo": r("B5"),
        "modificadores": r("B6"), "aviso": r("A7"),
        "tracos": [_traco(r(f"A{l}")) for l in LINHAS_TRACOS if r(f"A{l}") not in (None, 0)],
        "escolhas": escolhas,
    }


def _limpa_pericia(nome):
    """Remove os símbolos do nome e devolve (nome, só_treinada, penalidade_armadura)."""
    base = re.sub(r"[✯✠\s]+$", "", nome.replace("\n", " ")).strip()
    return base, "✯" in nome, "✠" in nome


def ler_ficha(g):
    """Monta o dict da ficha a partir de `g("B3") -> valor normalizado`."""
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
        pela_raca = g(_treinada_pela_raca(r)) is True
        pericias.append({
            "linha": r,
            "nome": nome,
            "atributo": g(f"M{r}"),
            "treinada": g(f"G{r}") == 1 or pela_raca,
            "treinada_pela_raca": pela_raca,
            "so_treinada": so_treinada,
            "penalidade_armadura": pen_armadura,
            "outros": g(f"Ajustes!B{r + AJUSTES_PERICIA}") or 0,     # já somado ao total
            "poderes": g(f"Ajustes!C{r + AJUSTES_PERICIA}") or 0,    # idem; só para explicar o total
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
        "pv": {"atual": g("S14"), "max": g("V14"), "temporario": g("X14")},
        "pm": {"atual": g("AA14"), "max": g("AD14")},
        "defesa": g("R9"), "deslocamento": g("Z9"), "cd_magia": g("W33"),
        "bonus_treinamento": g("L37"),
        "armadura": {"nome": g("Q39"), "defesa": g("X39"), "penalidade": g("AB39"), "proficiencia": g("AF39")},
        "escudo": {"nome": g("Q41"), "defesa": g("X41"), "penalidade": g("AB41")},
        "pericias": pericias, "ataques": ataques, "itens": itens,
        "dinheiro_t": g("Q44"), "carga": {"atual": g("AE44"), "max": g("AH44")},
        "raca_detalhe": _raca(g),
        "racial_origem": [{"linha": LINHA_ORIGEM, "marcador": g(f"AM{LINHA_ORIGEM}"), "texto": g(f"AP{LINHA_ORIGEM}")}]
        if g(f"AP{LINHA_ORIGEM}") else [],
        # AP47:AP66 só repete os nomes dos primeiros poderes: a ficha mostra os poderes completos
        "poderes": _poderes(g),
    }


def celulas_lidas():
    """Endereços completos ("PJ!B3") de todas as células que `ler_ficha` pode ler (para o cache do pycel)."""
    lidas = []

    def gravar(a1):
        lidas.append(endereco(a1))
        return "x"  # valor "preenchido" faz o leitor passar por todos os ramos

    ler_ficha(gravar)
    return list(dict.fromkeys(lidas))


def ler_planilha(arquivo):
    """Lê os valores já calculados de um .xlsx salvo pelo Excel/Sheets (caminho ou BytesIO)."""
    try:
        dfs = pd.read_excel(arquivo, sheet_name=[ABA, ABA_PODERES, ABA_RACA, "BD TRAÇOS"], header=None,
                            engine="openpyxl")
    except ValueError as e:
        raise ValueError("Faltam abas da ficha. Esta planilha não parece ser do modelo atual.") from e
    grades = {aba: Grade(df) for aba, df in dfs.items()}

    def celula(a1):
        aba, a1 = endereco(a1).split("!")
        return grades[aba](a1)

    return ler_ficha(celula)
