"""Funções do Excel que faltam (ou destoam) no pycel 1.0b30.

Entram como plugin: ExcelCompiler(..., plugins=["funcoes_excel"]). O pycel procura cada
função primeiro nos plugins, então estas substituem as dele.
"""
from pycel.lib.function_helpers import FUNC_META, apply_meta
from pycel.lib.logical import if_ as _if_cru

# O IF do pycel como o compilador o usa: com os invólucros de erro e de matriz (excel_helper)
_if_do_pycel = apply_meta(_if_cru, meta=dict(getattr(_if_cru, FUNC_META)), name_space={})[0]

# Mude ao mexer aqui: entra no nome do cache, que guarda valores calculados com estas funções
VERSAO = "3"


def char(numero):
    """CHAR(n): o caractere de código n (a aba Raça usa CHAR(10) para quebrar a linha)."""
    if isinstance(numero, str) and numero.startswith("#"):
        return numero  # erro do Excel vindo de outra célula segue adiante
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return "#VALUE!"
    return chr(n) if 1 <= n <= 255 else "#VALUE!"


def if_(test, true_value, false_value=0):
    """IF com condição única devolve o ramo escolhido, como no Excel.

    O do pycel repete o IF para cada elemento quando qualquer argumento é uma matriz. Em
    BD ESCOLHAS, IF(AO2=0, 0, INDEX('BD ESCOLHAS'!$AX$2:$AX$1722, AO2)) com AO2 = 0 vira
    INDEX(intervalo, 0) (a coluna inteira) e o resultado, uma matriz de 1.721 zeros: milhões
    de chamadas por recálculo. Com condição em matriz, fica o comportamento do pycel.
    """
    if isinstance(test, tuple):
        return _if_do_pycel(test, true_value, false_value)
    escolha = _if_do_pycel(test, True, False)
    if isinstance(escolha, str):
        return escolha  # a condição era um erro do Excel
    return true_value if escolha else false_value
