"""Recálculo pelo modelo: valores-chave, upload -> entradas, troca de raça e overrides."""
import openpyxl
import pytest

import campos
import motor
from conftest import CENARIO, preencher
from leitor import normalizar


def _pericia(f, nome):
    return next(p["total"] for p in f["pericias"] if p["nome"] == nome)


def test_valores_chave_da_planilha_preenchida(dados_diretos):
    f = dados_diretos
    # Guerreiro: 20 + CON (4) no 1º nível e 5 + CON nos outros dois; Anão (Duro como Pedra) +3 e +1 por nível
    assert f["pv"] == {"atual": 30, "max": 47, "temporario": None}
    assert f["pm"]["max"] == 9
    assert f["defesa"] == 18                     # 10 + cota de malha 6 + escudo pesado 2 (pesada: sem DES)
    assert f["deslocamento"] == "6m"             # Anão não perde deslocamento com armadura pesada
    assert f["bonus_treinamento"] == 2
    assert {s: a["mod"] for s, a in f["atributos"].items()} == {"FOR": 3, "DES": 0, "CON": 4, "INT": 0, "SAB": 2, "CAR": 0}
    assert _pericia(f, "Luta") == 6              # 1 (metade do nível) + FOR 3 + treino 2
    assert _pericia(f, "Fortitude") == 7
    assert _pericia(f, "Atletismo") == 6
    assert _pericia(f, "Acrobacia") == -3        # 1 + DES 0 - penalidades da armadura (2) e do escudo (2)
    assert f["ataques"] == [{"nome": "Espada longa", "teste": "Luta+FOR", "bonus": 6, "dano": "1d8 + 3",
                             "critico": "19", "tipo": "Corte", "alcance": "—"}]
    assert f["carga"] == {"atual": 1, "max": 16}  # corda 1 espaço, mochila 0; limite 10 + 2 × FOR


def test_upload_entradas_recalculo_igual_ao_direto(planilha_preenchida, dados_diretos):
    entradas, avisos = campos.entradas_da_planilha(planilha_preenchida)
    assert entradas == CENARIO
    assert avisos == []
    assert motor.calcular(entradas) == dados_diretos


def test_trocar_raca_muda_deslocamento_e_modificadores():
    sem_armadura = {k: v for k, v in CENARIO.items() if k not in ("PJ!Q39", "PJ!Q41")}
    anao = motor.calcular(sem_armadura)
    elfo = motor.calcular({**sem_armadura, "PJ!S2": "Elfo"})
    assert (anao["deslocamento"], elfo["deslocamento"]) == ("6m", "12m")
    assert {s: a["mod"] for s, a in elfo["atributos"].items()} == {"FOR": 3, "DES": 2, "CON": 1, "INT": 2, "SAB": 1, "CAR": 0}
    assert elfo["pv"]["max"] < anao["pv"]["max"]
    # Traços da aba Raça
    r = anao["raca_detalhe"]
    assert [t["nome"] for t in r["tracos"]] == ["Conhecimento das Rochas", "Devagar e Sempre", "Duro como Pedra",
                                                "Tradição de Heredrimm"]
    assert all(t["texto"] for t in r["tracos"])
    assert r["resumo"] == "Humanoide | Médio | Base 6 m | Visão no escuro" and r["escolhas"] == []
    assert r["modificadores"].startswith("Força: 0  |  Destreza: -1  |  Constituição: +2")
    r = elfo["raca_detalhe"]
    assert len(r["tracos"]) == 3 and "Base 12 m" in r["resumo"] and r["fonte"].startswith("JdA")
    # Com cota de malha o Elfo perde 3 m; o Anão não
    assert motor.calcular({**CENARIO, "PJ!S2": "Elfo"})["deslocamento"] == "9m"


def test_override_e_volta_para_automatico(dados_cenario):
    assert dados_cenario["ataques"][0]["teste"] == "Luta+FOR"
    manual = campos.aplicar(CENARIO, {"PJ!Z26": campos.validar(campos.CAMPOS["PJ!Z26"], "Pont.+DES")})
    f = motor.calcular(manual)
    assert f["ataques"][0]["teste"] == "Pont.+DES"
    assert f["ataques"][0]["bonus"] == _pericia(f, "Pontaria") != 6
    automatico = campos.aplicar(manual, {"PJ!Z26": campos.validar(campos.CAMPOS["PJ!Z26"], campos.AUTOMATICO)})
    assert "PJ!Z26" not in automatico
    assert motor.calcular(automatico) == dados_cenario  # objeto novo a cada recálculo: nada vaza


def test_override_na_planilha_so_vale_no_catalogo(tmp_path):
    caminho = preencher(tmp_path / "override.xlsx", {**CENARIO, "PJ!Z26": "Pont.+DES", "PJ!V14": 99})
    entradas, avisos = campos.entradas_da_planilha(caminho)
    assert entradas["PJ!Z26"] == "Pont.+DES"
    assert "PJ!V14" not in entradas
    assert any("PJ!V14" in a for a in avisos)


def test_planilha_de_outro_modelo_e_recusada(tmp_path, planilha_preenchida):
    outra = tmp_path / "outra.xlsx"
    openpyxl.Workbook().save(outra)
    with pytest.raises(ValueError, match="não é do modelo"):
        campos.entradas_da_planilha(outra)

    wb = openpyxl.load_workbook(planilha_preenchida)
    wb["BD ARMAS"]["B3"] = "regra da casa"
    wb.save(tmp_path / "regras.xlsx")
    with pytest.raises(ValueError, match="BD ARMAS"):
        campos.entradas_da_planilha(tmp_path / "regras.xlsx")


PODERES = {"Poderes escolhidos!A5": 1, "Poderes escolhidos!B5": "Ataque Poderoso [JdA]",
           "Poderes escolhidos!B6": "Longevidade Alquímica [Mitos]",   # nível mínimo 5; Kael é nível 3
           "Poderes escolhidos!B7": "Vitalidade [JdA]", "Poderes escolhidos!B8": "Vitalidade [JdA]",
           "Poderes escolhidos!B9": "-"}


def test_poderes_lidos_da_aba_poderes_escolhidos():
    f = motor.calcular({**CENARIO, **PODERES})
    assert [p["linha"] for p in f["poderes"]] == [5, 6, 7, 8]  # "-" não conta
    ataque, longevidade, vitalidade, _ = f["poderes"]
    assert ataque == {
        "linha": 5, "ativo": True, "opcao": "Ataque Poderoso [JdA]", "nome": "Ataque Poderoso", "livro": "JdA",
        "pagina": 130, "nivel_minimo": 0, "pre_requisitos": "For 1.", "regra": ataque["regra"],
        "aviso": "Confira demais requisitos", "aplicacao": "Consulte a regra e aplique escolhas em Ajustes",
        "nivel_na_ficha": "N1"}
    assert ataque["regra"].startswith("Sempre que faz um ataque corpo a corpo")
    assert (longevidade["ativo"], longevidade["nivel_minimo"], longevidade["aviso"]) == (False, 5, "Nível insuficiente")
    assert vitalidade["aviso"] == "Repetido: confira se permitido"
    assert vitalidade["aplicacao"] == "Bônus calculado; confira condição"
    assert motor.calcular(CENARIO)["poderes"] == []


HUMANO = {**CENARIO, "PJ!S2": "Humano"}
ESCOLHAS_HUMANO = {"Raça!B11": "Força", "Raça!B12": "Destreza", "Raça!B13": "Constituição",
                   "Raça!B14": "Treino em Luta", "Raça!B15": "Treino em Percepção"}


def test_humano_com_e_sem_escolhas():
    sem = motor.calcular(HUMANO)
    assert [(e["rotulo"], e["situacao"]) for e in sem["raca_detalhe"]["escolhas"]] == [
        ("Atributo 1", "A escolher"), ("Atributo 2", "A escolher"), ("Atributo 3", "A escolher"),
        ("Versátil", "A escolher"), ("Versátil · segunda opção", "A escolher")]
    assert sem["raca_detalhe"]["aviso"].startswith("Há escolhas raciais pendentes")
    com = motor.calcular({**HUMANO, **ESCOLHAS_HUMANO})
    mods = {s: (sem["atributos"][s]["mod"], com["atributos"][s]["mod"]) for s in ("FOR", "DES", "CON", "INT")}
    assert mods == {"FOR": (3, 4), "DES": (1, 2), "CON": (2, 3), "INT": (0, 0)}
    assert [p["nome"] for p in com["pericias"] if p["treinada_pela_raca"]] == ["Luta", "Percepção"]
    luta = next(p for p in com["pericias"] if p["nome"] == "Luta")
    assert luta["treinada"] and luta["total"] == 1 + 4 + 2   # metade do nível + FOR + treino
    percepcao = next(p for p in com["pericias"] if p["nome"] == "Percepção")
    assert percepcao["treinada"] and not next(p for p in sem["pericias"] if p["nome"] == "Percepção")["treinada"]
    escolhas = com["raca_detalhe"]["escolhas"]
    assert {e["situacao"] for e in escolhas} == {"Escolhida"}
    # A descrição junta regra + CHAR(10) + orientação; orientação vazia não deixa o "0" no fim
    assert [e["descricao"] for e in escolhas[:2]] == ["+1 em Força.", "+1 em Destreza."]
    assert escolhas[3]["descricao"] == "Torna-se treinado em Luta."


def test_moreau_heranca_muda_os_tracos():
    sem = motor.calcular({**CENARIO, "PJ!S2": "Moreau"})
    assert (sem["raca_detalhe"]["nome"], sem["raca_detalhe"]["aviso"]) == ("Moreau", "Escolha a herança acima.")
    coruja = motor.calcular({**CENARIO, "PJ!S2": "Moreau", "Raça!B4": "Moreau Coruja"})
    leao = motor.calcular({**CENARIO, "PJ!S2": "Moreau", "Raça!B4": "Moreau Leão"})
    assert coruja["raca"] == "Moreau" and coruja["raca_detalhe"]["nome"] == "Moreau Coruja"
    assert [t["nome"] for t in coruja["raca_detalhe"]["tracos"]] == ["Espreitador", "Garras", "Sapiência"]
    assert [t["nome"] for t in leao["raca_detalhe"]["tracos"]] == ["Mordida", "Rugido Imponente",
                                                                  "Sentidos da Realeza", "Herança"]
    assert [e["rotulo"] for e in coruja["raca_detalhe"]["escolhas"]] == ["Atributo 1", "Atributo 2", "Sapiência"]


def test_opcoes_raciais_calculadas_para_a_raca():
    assert motor.opcoes_raciais(CENARIO) == {}  # Anão não tem escolhas
    humano = motor.opcoes_raciais(HUMANO)
    assert list(humano) == ["Raça!B11", "Raça!B12", "Raça!B13", "Raça!B14", "Raça!B15"]
    assert humano["Raça!B11"] == {"rotulo": "Atributo 1", "livre": False, "opcoes": (
        "Força", "Destreza", "Constituição", "Inteligência", "Sabedoria", "Carisma")}
    assert len(humano["Raça!B14"]["opcoes"]) == 29 and "Treino em Luta" in humano["Raça!B14"]["opcoes"]
    assert len(humano["Raça!B15"]["opcoes"]) == 131
    assert motor.opcoes_raciais({**HUMANO, "PJ!B3": "Outro"}) is humano  # o resto da ficha não entra na chave
    coruja = motor.opcoes_raciais({"PJ!S2": "Moreau", "Raça!B4": "Moreau Coruja"})
    assert coruja["Raça!B13"]["rotulo"] == "Sapiência" and len(coruja["Raça!B13"]["opcoes"]) == 10


def test_calcular_pode_deixar_as_opcoes_prontas():
    motor._opcoes.clear()
    motor.calcular(HUMANO, guardar_opcoes=True)
    assert motor._chave_opcoes(HUMANO) in motor._opcoes


def test_recalculo_nao_deixa_ciclos_na_memoria():
    import gc
    motor.calcular(CENARIO)
    gc.collect()
    motor.calcular(CENARIO)
    assert gc.collect() < 10_000  # sem desmontar o objeto do pycel sobram ~1,4 milhão (400 MB) por recálculo
    assert gc.isenabled()          # e o coletor volta a funcionar depois da pausa


def test_plugin_char_e_if_como_no_excel():
    import funcoes_excel
    if_do_pycel = funcoes_excel._if_do_pycel
    assert funcoes_excel.char(10) == "\n" and funcoes_excel.char("#N/A") == "#N/A"
    coluna = ((1,), (2,), (3,))
    assert if_do_pycel(True, 0, coluna) == ((0,), (0,), (0,))  # o pycel repete o IF para cada elemento
    assert funcoes_excel.if_(True, 0, coluna) == 0               # o Excel devolve o ramo escolhido
    assert funcoes_excel.if_(False, 0, coluna) == coluna
    assert funcoes_excel.if_(((True,), (False,)), 1, 2) == ((1,), (2,))  # condição em matriz: como antes
    assert funcoes_excel.if_("#VALUE!", 1, 2) == "#VALUE!"


def test_outros_e_poderes_entram_no_total_da_pericia():
    f = motor.calcular({**CENARIO, "Ajustes!B47": 2,
                        "Poderes escolhidos!A5": 1, "Poderes escolhidos!B5": "Vontade de Ferro [JdA]"})
    luta = next(p for p in f["pericias"] if p["nome"] == "Luta")
    assert (luta["linha"], luta["outros"], luta["poderes"], luta["total"]) == (26, 2, 0, 6 + 2)
    vontade = next(p for p in f["pericias"] if p["nome"] == "Vontade")
    sem = next(p for p in motor.calcular(CENARIO)["pericias"] if p["nome"] == "Vontade")
    assert (vontade["outros"], vontade["poderes"], vontade["total"]) == (0, 2, sem["total"] + 2)


def test_pv_temporario_vem_da_planilha():
    assert motor.calcular({**CENARIO, "PJ!X14": 5})["pv"] == {"atual": 30, "max": 47, "temporario": 5}


def _aviso_de_linha_vazia():
    """O caso real: o Aviso (coluna I) de uma linha sem poder, calculado do zero pelo pycel."""
    from pycel import ExcelCompiler
    return ExcelCompiler.from_file(str(motor.preparar())).evaluate("Poderes escolhidos!I30")


def test_countif_com_criterio_vazio_vale_zero_como_no_excel(monkeypatch):
    from pycel import excelutil
    from pycel.lib.stats import countif
    motor._ajustar_pycel()
    monkeypatch.setattr(excelutil, "criteria_parser", excelutil.criteria_parser.original)  # pycel sem o ajuste
    with pytest.raises(ValueError, match="Couldn't parse criteria: None"):
        countif(((0, None, "a"),), None)
    with pytest.raises(Exception, match="Couldn't parse criteria"):
        _aviso_de_linha_vazia()
    monkeypatch.undo()
    motor._ajustar_pycel()
    assert countif(((0, None, "a"),), None) == 1  # Excel: célula vazia como critério vale 0
    assert normalizar(_aviso_de_linha_vazia()) is None


def test_cache_tem_todas_as_celulas_usadas():
    from pycel import ExcelCompiler
    xl = ExcelCompiler.from_file(str(motor.preparar()))
    assert [a for a in motor.celulas_aquecidas() if a not in xl.cell_map] == []
