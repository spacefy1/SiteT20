"""Recálculo da ficha com o pycel: modelo compilado (cache .pkl) + entradas da ficha -> dict da ficha.

A compilação leva cerca de 30 s e acontece uma vez por versão do modelo (ou do
conjunto de células aquecidas). Cada recálculo carrega o cache de novo, num
objeto só seu: um override numa célula com fórmula não volta sozinho, então
reaproveitar o objeto vazaria estado de uma ficha para outra.
"""
import hashlib
import subprocess
import sys
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from openpyxl.utils import get_column_letter

import campos
import funcoes_excel
import modelo
from leitor import celulas_lidas, endereco, ler_ficha, normalizar

PASTA = modelo.CAMINHO.parent
PLUGINS = ("funcoes_excel",)   # CHAR (que o pycel 1.0b30 não tem) e um IF como o do Excel
_trava = threading.Lock()
_cache = None

# Escolhas raciais: a linha k (1 a 17) da aba Raça é a linha k + 10; em BD ESCOLHAS ela é a linha k + 1.
# K = rótulo, L = grupo ("~" = texto livre), R = quantidade de opções, e as opções ficam na coluna
# V + (k - 1), da linha 2 para baixo.
ESCOLHAS = range(1, 18)
LINHAS_OPCOES = range(2, 299)


def _coluna_opcoes(k):
    return get_column_letter(21 + k)  # V, W, ..., AL


def _celulas_opcoes():
    for k in ESCOLHAS:
        yield from (f"BD ESCOLHAS!K{k + 1}", f"BD ESCOLHAS!L{k + 1}", f"BD ESCOLHAS!R{k + 1}")
        yield from (f"BD ESCOLHAS!{_coluna_opcoes(k)}{n}" for n in LINHAS_OPCOES)


def celulas_aquecidas():
    """Células que precisam estar no cache: as que o site lê, todas as editáveis e as listas da aba Raça."""
    return sorted(set(celulas_lidas()) | set(campos.CAMPOS) | set(modelo.LIXO) | set(_celulas_opcoes()))


def caminho_cache():
    """O nome do arquivo muda quando o modelo ou a lista de células muda."""
    global _cache
    if _cache is None:
        h = hashlib.md5(modelo.CAMINHO.read_bytes())
        h.update("\n".join(celulas_aquecidas()).encode())
        h.update(funcoes_excel.VERSAO.encode())
        _cache = PASTA / f"cache_{modelo.CAMINHO.stem}_{h.hexdigest()[:12]}.pkl"
    return _cache


def preparar(forcar=False):
    """Garante o cache compilado e devolve o caminho dele."""
    destino = caminho_cache()
    with _trava:
        if forcar or not destino.exists():
            # Processo separado em modo UTF-8: o pycel grava um YAML intermediário com a codificação
            # padrão do sistema (cp1252 no Windows) e quebra nos acentos. A memória da compilação
            # também volta para o sistema ao fim do processo.
            subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__)), str(destino)],
                           check=True, cwd=Path(__file__).parent)
            for antigo in PASTA.glob("cache_*.pkl"):  # inclusive os de modelos anteriores
                if antigo != destino:
                    antigo.unlink(missing_ok=True)
    return destino


def _ajustar_pycel():
    """COUNTIF com critério numa célula vazia: o Excel trata a célula como 0; o pycel 1.0b30 levanta erro.

    Acontece em Poderes escolhidos!I5:I104: o IF do pycel avalia os dois ramos (o Excel não),
    então o COUNTIF roda até nas linhas sem poder.
    """
    from pycel import excelutil

    original = excelutil.criteria_parser
    if getattr(original, "vazio_vale_zero", False):
        return

    def criteria_parser(criteria):
        return original(0 if criteria is None else criteria)

    criteria_parser.vazio_vale_zero = True
    criteria_parser.original = original
    excelutil.criteria_parser = criteria_parser


def _compilar(destino):
    from pycel import ExcelCompiler

    _ajustar_pycel()
    xl = ExcelCompiler(filename=str(modelo.CAMINHO), plugins=PLUGINS)
    for celula in celulas_aquecidas():
        xl.evaluate(celula)
    provisorio = destino.with_name(destino.stem + ".tmp")
    xl.to_file(str(provisorio), file_types="pkl")
    Path(str(provisorio) + ".pkl").replace(destino)


@contextmanager
def _planilha(entradas, lidas):
    """Cache carregado num objeto novo, com as entradas da ficha gravadas.

    Roda com o coletor de lixo parado (a carga cria milhões de objetos) e, na saída, desmonta
    o objeto: células e fórmulas apontam de volta para o compilador, e esses ciclos só sairiam
    da memória numa coleta completa (cerca de 1,3 s e 400 MB a cada recálculo).
    """
    from pycel import ExcelCompiler

    _ajustar_pycel()
    with modelo.coletor_pausado():
        xl = ExcelCompiler.from_file(str(preparar()), plugins=PLUGINS)
        try:
            valores = {**dict.fromkeys(modelo.LIXO), **entradas}
            # Fora do cache o pycel trata a célula como vazia, sem avisar; melhor falhar alto.
            faltam = [a for a in (*valores, *lidas) if a not in xl.cell_map]
            if faltam:
                raise RuntimeError(f"Cache do modelo incompleto ({', '.join(faltam[:5])}). "
                                   "Rode: flask --app app preparar-modelo")
            for celula, valor in valores.items():
                xl.set_value(celula, valor)
            yield xl
        finally:
            xl.__dict__.clear()  # quebra os ciclos: a contagem de referências libera o resto


def calcular(entradas, guardar_opcoes=False):
    """Entradas da ficha -> dict no formato do leitor (o mesmo que `fichas.dados`).

    `guardar_opcoes`: aproveita a planilha já carregada para deixar prontas as listas das
    escolhas raciais (o editor salva assim quando a raça ou uma escolha muda e reabre na seção Raça).
    """
    with _planilha(entradas, celulas_lidas()) as xl:
        f = ler_ficha(lambda a1: normalizar(xl.evaluate(endereco(a1))))
        if guardar_opcoes:
            _guardar_opcoes(_chave_opcoes(entradas), _ler_opcoes(xl))
    return f


# As listas dependem só da raça, da herança e das escolhas (uma escolha pode liberar outra)
CHAVE_OPCOES = campos.CHAVE_RACA
_opcoes = OrderedDict()
_trava_opcoes = threading.Lock()


def _chave_opcoes(entradas):
    return tuple(entradas.get(c) for c in CHAVE_OPCOES)


def _guardar_opcoes(chave, escolhas):
    with _trava_opcoes:
        _opcoes[chave] = escolhas
        _opcoes.move_to_end(chave)
        while len(_opcoes) > 64:
            _opcoes.popitem(last=False)


def _ler_opcoes(xl):
    """Só as primeiras R células de cada coluna de opções: depois delas a fórmula dá ""."""
    escolhas = {}
    for k in ESCOLHAS:
        rotulo = normalizar(xl.evaluate(f"BD ESCOLHAS!K{k + 1}"))
        if rotulo in (None, 0):
            continue
        grupo = normalizar(xl.evaluate(f"BD ESCOLHAS!L{k + 1}"))
        total = normalizar(xl.evaluate(f"BD ESCOLHAS!R{k + 1}"))
        total = total if isinstance(total, int) else 0
        opcoes = (normalizar(xl.evaluate(f"BD ESCOLHAS!{_coluna_opcoes(k)}{n}")) for n in LINHAS_OPCOES[:total])
        escolhas[f"Raça!B{k + 10}"] = {"rotulo": rotulo, "livre": grupo == "~" or total == 0,
                                      "opcoes": tuple(dict.fromkeys(o for o in opcoes if o not in (None, 0)))}
    return escolhas


def opcoes_raciais(entradas):
    """Escolhas ativas da aba Raça -> {"Raça!B11": {"rotulo", "livre", "opcoes"}}.

    Caro (o pycel abre o cache, cerca de 1 s), então só o editor pede, e as últimas 64
    combinações de raça, herança e escolhas ficam guardadas na memória.
    """
    chave = _chave_opcoes(entradas)
    with _trava_opcoes:
        if chave in _opcoes:
            _opcoes.move_to_end(chave)
            return _opcoes[chave]
    with _planilha({c: entradas[c] for c in CHAVE_OPCOES if c in entradas}, ()) as xl:
        escolhas = _ler_opcoes(xl)
    _guardar_opcoes(chave, escolhas)
    return escolhas


if __name__ == "__main__":  # chamado por preparar(), num processo separado
    _compilar(Path(sys.argv[1]))
