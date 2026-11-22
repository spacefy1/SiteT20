"""Fixtures: cópia preenchida do modelo, cálculo direto com pycel, banco temporário e clientes logados."""
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import modelo  # noqa: E402
import motor  # noqa: E402
from leitor import endereco, ler_ficha, normalizar  # noqa: E402

# Anão Guerreiro 3 com espada longa, cota de malha e escudo pesado
CENARIO = {
    "PJ!B3": "Kael", "PJ!S2": "Anão", "PJ!S4": "Guerreiro", "PJ!Z4": 3,
    "PJ!C12": 3, "PJ!C17": 1, "PJ!C22": 2, "PJ!C32": 1,          # FOR, DES, CON, SAB base
    "PJ!G10": 1, "PJ!G17": 1, "PJ!G26": 1,                         # Atletismo, Fortitude, Luta treinadas
    "PJ!Q26": "Espada longa", "PJ!Q39": "Cota de malha", "PJ!Q41": "Escudo pesado",
    "PJ!U49": "Corda", "PJ!Q49": 1, "PJ!U50": "Mochila", "PJ!Q50": 1,
    "PJ!Q44": 50, "PJ!S14": 30,
}


def preencher(destino, valores, nome_modelo=modelo.ATUAL):
    """Salva uma cópia do modelo com `valores` escritos via openpyxl (como o jogador faria no Excel)."""
    wb = openpyxl.load_workbook(modelo.MODELOS[nome_modelo])
    for celula, valor in {**dict.fromkeys(modelo.LIXOS[nome_modelo]), **valores}.items():
        aba, a1 = modelo.separar(celula)
        wb[aba][a1] = valor
    wb.save(destino)
    return destino


@pytest.fixture(scope="session", autouse=True)
def cache_do_modelo():
    motor.preparar()  # cerca de 30 s na primeira vez; depois reaproveita modelo/cache_*.pkl


@pytest.fixture(scope="session")
def planilha_preenchida(tmp_path_factory):
    return preencher(tmp_path_factory.mktemp("planilhas") / "kael.xlsx", CENARIO)


@pytest.fixture(scope="session")
def dados_diretos(planilha_preenchida):
    """A ficha calculada direto da planilha preenchida, sem o fluxo modelo + entradas."""
    from pycel import ExcelCompiler
    motor._ajustar_pycel()
    xl = ExcelCompiler(filename=str(planilha_preenchida), plugins=motor.PLUGINS)
    return ler_ficha(lambda a1: normalizar(xl.evaluate(endereco(a1))))


@pytest.fixture(scope="session")
def dados_cenario():
    return motor.calcular(CENARIO)


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "CAMINHO", tmp_path / "teste.db")
    for login, nome, papel in (("mestre", "Mestre", "mestre"), ("ana", "Ana", "jogador"), ("bruno", "Bruno", "jogador")):
        db.criar_usuario(login, nome, papel, "sem-senha")
    return db


@pytest.fixture
def site(banco):
    from app import app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def entrar(site):
    """entrar("ana") -> cliente de teste já logado, com csrf "t" na sessão."""
    def cliente(login):
        c = site.test_client()
        with c.session_transaction() as s:
            s["uid"], s["csrf"] = db.usuario(login=login)["id"], "t"
        return c
    return cliente


@pytest.fixture
def ficha_da_ana(banco, dados_cenario):
    return db.salvar(dados_cenario, db.usuario(login="ana")["id"], dict(CENARIO))
