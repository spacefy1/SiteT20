"""As planilhas modelo (fichas em branco): leitura em memória, conferência de uploads e geração do .xlsx.

O modelo atual é o motor de regras. Cada ficha guarda só as células que diferem
dele (as "entradas"); este módulo sabe o valor padrão de cada célula, as opções
das validações de dados e a impressão digital das abas de regras (BD*).
Modelos anteriores continuam aqui só para converter planilhas antigas enviadas.
"""
import gc
import hashlib
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.workbook.properties import CalcProperties

from leitor import normalizar

PASTA = Path(__file__).with_name("modelo")
MODELOS = {
    "Integrados": PASTA / "Ficha_T20_Livros_Integrados.xlsx",   # atual: traz a aba Raça
    "V2": PASTA / "Ficha_T20_Livros_V2.xlsx",                   # anterior: só para converter uploads
}
ATUAL = "Integrados"
CAMINHO = MODELOS[ATUAL]
ABAS_EDITAVEIS = ("PJ", "Raça", "Ajustes", "Poderes escolhidos", "Grimório")
# Textos de teste que vieram nos modelos: valem como vazio
LIXOS = {"Integrados": {"PJ!AO2": "TDAH RPGs"}, "V2": {"PJ!AO2": "oi smoke"}}
LIXO = LIXOS[ATUAL]


_pausas = 0
_trava_coletor = threading.Lock()


@contextmanager
def coletor_pausado():
    """Desliga o coletor de lixo do Python enquanto o bloco roda (vale entre threads: volta na última saída).

    Carregar o cache do pycel ou o modelo no openpyxl cria milhões de objetos, e cada passada
    completa do coletor percorre todos de novo: com ele ligado, um recálculo vai de ~1 s para ~3 s.
    """
    global _pausas
    with _trava_coletor:
        _pausas += 1
        gc.disable()
    try:
        yield
    finally:
        with _trava_coletor:
            _pausas -= 1
            if _pausas == 0:
                gc.enable()


def _aba_de_regras(nome):
    return nome.startswith("BD") or nome.startswith("BR ")


def separar(endereco):
    """'Poderes escolhidos!A5' -> ('Poderes escolhidos', 'A5')."""
    aba, a1 = endereco.rsplit("!", 1)
    return aba.strip("'"), a1


@dataclass
class Modelo:
    nome: str              # "Integrados", "V2"
    abas: tuple            # nomes de todas as abas, na ordem
    celulas: dict          # "PJ!B3" -> valor literal normalizado ou "=fórmula" (só abas editáveis)
    digitais: dict         # aba de regras -> hash dos valores literais
    validacoes: dict       # "PJ!S2" -> (fonte, opções); listas calculadas por fórmula ficam sem opções
    catalogo: dict         # opção de poder -> colunas do BD CATÁLOGO que a aba Poderes escolhidos mostra

    def padrao(self, endereco):
        """Valor da célula no modelo (fórmulas voltam como texto começando com '=')."""
        return self.celulas.get(endereco)

    def tem_formula(self, endereco):
        v = self.celulas.get(endereco)
        return isinstance(v, str) and v.startswith("=")


def _valor_bruto(c):
    v = c.value
    if c.data_type == "f":
        return v if isinstance(v, str) else "=" + str(getattr(v, "text", v))
    return normalizar(v)


def _ler_abas(wb, editaveis):
    """Lê, de um workbook aberto em read_only, as células das abas editáveis e as digitais das abas BD."""
    celulas, digitais = {}, {}
    for ws in wb.worksheets:
        editavel, regras = ws.title in editaveis, _aba_de_regras(ws.title)
        if not (editavel or regras):
            continue
        ws.reset_dimensions()  # arquivos do Google Planilhas às vezes trazem a dimensão errada
        h = hashlib.sha1()
        for linha in ws.iter_rows():
            for c in linha:
                if getattr(c, "value", None) is None:
                    continue
                v = _valor_bruto(c)
                if editavel and v is not None:
                    celulas[f"{ws.title}!{c.coordinate}"] = v
                if regras and c.data_type != "f" and v is not None:
                    h.update(f"{c.coordinate}={round(v, 9) if isinstance(v, float) else v!r}\n".encode())
        if regras:
            digitais[ws.title] = h.hexdigest()
    return celulas, digitais


def _opcoes(wb, formula):
    """Opções de uma validação de lista: '"a,b,c"' ou um intervalo como 'BD ARMAS'!$A$2:$A$138.

    Células com fórmula ficam de fora: são listas que dependem da ficha (as escolhas da aba Raça).
    """
    formula = formula.strip()
    if formula.startswith('"'):
        return tuple(o.strip() for o in formula.strip('"').split(",") if o.strip())
    aba, faixa = separar(formula)
    c1, l1, c2, l2 = range_boundaries(faixa.replace("$", ""))
    valores = (normalizar(v) for linha in wb[aba].iter_rows(min_row=l1, max_row=l2, min_col=c1, max_col=c2,
                                                             values_only=True) for v in linha)
    return tuple(dict.fromkeys(v for v in valores if v is not None and not (isinstance(v, str) and v.startswith("="))))


def _ler_validacoes(wb, editaveis):
    validacoes = {}
    for nome in editaveis:
        for dv in wb[nome].data_validations.dataValidation:
            if dv.type != "list" or not dv.formula1:
                continue
            fonte = dv.formula1
            opcoes = _opcoes(wb, fonte)
            for faixa in dv.sqref.ranges:
                for lin, col in faixa.cells:
                    validacoes[f"{nome}!{get_column_letter(col)}{lin}"] = (fonte, opcoes)
    return validacoes


# Poderes escolhidos!C:H = VLOOKUP no BD CATÁLOGO, colunas B, D, E, I, H e J
COLUNAS_CATALOGO = {"nome": 1, "livro": 3, "pagina": 4, "pre_requisitos": 7, "nivel_minimo": 8, "regra": 9}


def _ler_catalogo(wb):
    catalogo = {}
    for linha in wb["BD CATÁLOGO"].iter_rows(min_row=2, max_col=10, values_only=True):
        opcao = normalizar(linha[0])
        if opcao is not None and opcao not in catalogo:  # o VLOOKUP fica com a primeira
            catalogo[opcao] = {chave: normalizar(linha[i]) for chave, i in COLUNAS_CATALOGO.items()}
    return catalogo


_trava = threading.Lock()
_modelos = {}


def carregar(nome=ATUAL):
    """Um modelo em memória (lido uma vez por processo, cerca de 4 s)."""
    with _trava, coletor_pausado():
        if nome not in _modelos:
            caminho = MODELOS[nome]
            wb = openpyxl.load_workbook(caminho, read_only=True)
            try:
                abas = tuple(wb.sheetnames)
                editaveis = [a for a in ABAS_EDITAVEIS if a in abas]
                celulas, digitais = _ler_abas(wb, editaveis)
            finally:
                wb.close()
            for endereco in LIXOS[nome]:
                celulas.pop(endereco, None)
            completo = openpyxl.load_workbook(caminho)  # read_only não traz as validações
            _modelos[nome] = Modelo(nome, abas, celulas, digitais, _ler_validacoes(completo, editaveis),
                                    _ler_catalogo(completo))
    return _modelos[nome]


def _qual_modelo(abas):
    """Nome do modelo com exatamente estas abas, ou None."""
    return next((nome for nome, caminho in MODELOS.items() if set(abas) == set(_abas_do_arquivo(caminho))), None)


_abas_cache = {}


def _abas_do_arquivo(caminho):
    if caminho not in _abas_cache:
        wb = openpyxl.load_workbook(caminho, read_only=True)
        _abas_cache[caminho] = tuple(wb.sheetnames)
        wb.close()
    return _abas_cache[caminho]


def abrir_planilha(arquivo):
    """Confere de qual modelo é o .xlsx e devolve (modelo, células das abas editáveis)."""
    try:
        wb = openpyxl.load_workbook(arquivo, read_only=True)
    except Exception as e:
        raise ValueError("Não consegui abrir o arquivo. Envie o .xlsx salvo pelo Excel ou baixado do Google Planilhas.") from e
    try:
        nome = _qual_modelo(wb.sheetnames)
        if nome is None:
            atuais = _abas_do_arquivo(CAMINHO)
            faltam = [a for a in atuais if a not in wb.sheetnames]
            sobram = [a for a in wb.sheetnames if a not in atuais]
            detalhes = "; ".join(filter(None, [faltam and "faltam " + ", ".join(faltam),
                                               sobram and "sobram " + ", ".join(sobram)]))
            raise ValueError(f"Esta planilha não é do modelo {CAMINHO.stem} ({detalhes}). "
                             "Use a ficha baixada deste site ou a planilha modelo.")
        m = carregar(nome)
        celulas, digitais = _ler_abas(wb, [a for a in ABAS_EDITAVEIS if a in m.abas])
    finally:
        wb.close()
    diferentes = [a for a, d in m.digitais.items() if digitais.get(a) != d]
    if diferentes:
        raise ValueError(f"As abas de regras desta planilha são de outra versão ({', '.join(diferentes)}). "
                         f"Use a planilha modelo {CAMINHO.stem}.")
    for endereco, lixo in LIXOS[nome].items():
        if celulas.get(endereco) == lixo:
            celulas.pop(endereco)
    return m, celulas


# Funções do Excel 2010+ precisam do prefixo _xlfn. dentro do arquivo. O modelo atual já grava assim;
# a V2 gravava IFNA sem ele (o Excel mostrava #NAME? na carga). O (?<![\w.]) não duplica o prefixo.
_FUNCOES_NOVAS = re.compile(r"(?<![\w.])(IFNA)\(")


def _corrigir_funcoes_novas(wb):
    for ws in wb.worksheets:
        for linha in ws.iter_rows():
            for c in linha:
                if c.data_type == "f" and isinstance(c.value, str) and "IFNA(" in c.value:
                    c.value = _FUNCOES_NOVAS.sub(r"_xlfn.\1(", c.value)


def gerar_planilha(entradas):
    """Modelo + entradas da ficha num .xlsx que o Excel recalcula ao abrir, com as abas BD ocultas."""
    with coletor_pausado():
        return _gerar_planilha(entradas)


def _gerar_planilha(entradas):
    wb = openpyxl.load_workbook(CAMINHO)
    _corrigir_funcoes_novas(wb)
    for endereco, valor in {**dict.fromkeys(LIXO), **entradas}.items():
        aba, a1 = separar(endereco)
        c = wb[aba][a1]
        c.value = valor
        if isinstance(valor, str):
            c.data_type = "s"  # texto do jogador nunca vira fórmula
    wb.active = wb.sheetnames.index("PJ")
    for ws in wb.worksheets:
        ws.sheet_view.tabSelected = ws.title == "PJ"
        if _aba_de_regras(ws.title):
            ws.sheet_state = "hidden"
    if wb.calculation is None:  # o modelo novo não traz <calcPr>
        wb.calculation = CalcProperties()
    wb.calculation.fullCalcOnLoad = True
    saida = BytesIO()
    wb.save(saida)
    saida.seek(0)
    return saida
