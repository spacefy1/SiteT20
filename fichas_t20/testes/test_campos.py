"""Catálogo (whitelist) contra o modelo e validação dos valores."""
import pytest
from werkzeug.datastructures import MultiDict

import campos
import modelo


def test_catalogo_cobre_as_validacoes_do_modelo():
    m = modelo.carregar()
    assert [e for e in m.validacoes if e not in campos.CAMPOS] == []
    listas = [c for c in campos.CAMPOS.values() if c.tipo == campos.LISTA]
    assert all(campos.opcoes(c) for c in listas)
    # Override só em célula com fórmula, e toda célula de fórmula no catálogo é override
    assert [c.endereco for c in campos.CAMPOS.values() if c.automatico != m.tem_formula(c.endereco)] == []


def test_opcoes_vem_das_validacoes():
    assert "Anão" in campos.opcoes(campos.CAMPOS["PJ!S2"])
    assert campos.opcoes(campos.CAMPOS["PJ!Z26"]) == ("-", "Luta+FOR", "Luta+DES", "Pont.+DES", "Ofício")
    assert len(campos.opcoes(campos.CAMPOS["Poderes escolhidos!B5"])) > 8000
    assert campos.modo(campos.CAMPOS["Poderes escolhidos!B5"]) == "busca"
    assert campos.buscar(campos.CAMPOS["Poderes escolhidos!B5"], "ataque poderoso") == ["Ataque Poderoso [JdA]"]


@pytest.mark.parametrize("endereco, bruto, esperado", [
    ("PJ!B3", "  Kael ", "Kael"),
    ("PJ!B3", "", None),
    ("PJ!C12", "−2", -2),
    ("PJ!S14", "-7", -7),             # PV negativo existe em T20
    ("PJ!AC49", "1,5", 1.5),
    ("PJ!G8", "1", 1),
    ("PJ!S2", "anão", "Anão"),        # a opção volta com a grafia da lista
    ("PJ!Z4", "3", 3),
    ("PJ!Z26", campos.AUTOMATICO, campos.AUTOMATICO),
])
def test_validar_aceita(endereco, bruto, esperado):
    assert campos.validar(campos.CAMPOS[endereco], bruto) == esperado


@pytest.mark.parametrize("endereco, bruto", [
    ("PJ!C12", "dois"), ("PJ!C12", "1,5"), ("PJ!AC49", "-1"), ("PJ!G8", "2"),
    ("PJ!S2", "Xablau"), ("PJ!Z4", "21"), ("PJ!S2", campos.AUTOMATICO), ("PJ!B3", "x" * 501),
])
def test_validar_recusa(endereco, bruto):
    with pytest.raises(campos.ErroCampo):
        campos.validar(campos.CAMPOS[endereco], bruto)


def test_formulario_recusa_celula_fora_do_catalogo():
    mudancas, erros = campos.ler_formulario(MultiDict([("csrf", "t"), ("PJ!V14", "99"), ("PJ!S2", "Elfo")]))
    assert erros == {"PJ!V14": "Esta célula não é editável."}
    assert mudancas == {"PJ!S2": "Elfo"}


def test_caixa_de_marcar_usa_o_ultimo_valor():
    mudancas, _ = campos.ler_formulario(MultiDict([("PJ!G8", "0"), ("PJ!G8", "1")]))
    assert mudancas == {"PJ!G8": 1}


def test_aplicar_apaga_o_que_volta_ao_modelo():
    entradas = {"PJ!B3": "Kael", "PJ!G8": 1, "PJ!Z26": "Luta+DES"}
    novas = campos.aplicar(entradas, {"PJ!G8": 0, "PJ!Z26": campos.AUTOMATICO, "PJ!C12": 2})
    assert novas == {"PJ!B3": "Kael", "PJ!C12": 2}


def _enderecos(titulo):
    blocos = dict(campos.SECOES)[titulo]
    return {c.endereco for b in blocos for _, linha in b.linhas for c in linha if c}


def test_atributos_pedem_so_a_base_e_os_ajustes_raciais_ficam_em_ajustes():
    assert _enderecos("Atributos") == set(campos.BASES)
    ajustes = {b.titulo: {c.endereco for c in b.linhas[0][1] if c} for b in dict(campos.SECOES)["Ajustes"]}
    assert ajustes["Ajuste racial manual"] == set(campos.AJUSTE_RACIAL)
    assert ajustes["Aumentos de atributo (poderes e outros)"] == set(campos.AUMENTOS)


def test_secao_raca_tem_raca_heranca_e_escolhas_com_lista_calculada():
    assert _enderecos("Raça") == {"PJ!S2", "Raça!B4", *campos.ESCOLHAS_RACIAIS}
    assert all(campos.CAMPOS[e].dinamica for e in campos.ESCOLHAS_RACIAIS)
    assert campos.opcoes(campos.CAMPOS["Raça!B4"])[:2] == ("Moreau Coruja", "Moreau Hiena")
    assert len(campos.opcoes(campos.CAMPOS["PJ!S2"])) == 115


def test_trocar_raca_limpa_heranca_e_escolhas_mas_nao_o_ajuste_manual():
    humano = {"PJ!S2": "Humano", "Raça!B11": "Força", "Raça!B14": "Treino em Luta", "Ajustes!B6": 1}
    assert campos.aplicar(humano, {"PJ!S2": "Anão", "Raça!B11": "Força"}) == {"PJ!S2": "Anão", "Ajustes!B6": 1}
    assert campos.aplicar(humano, {"PJ!S2": "Humano", "Raça!B11": "Destreza"}) == {**humano, "Raça!B11": "Destreza"}
    moreau = {"PJ!S2": "Moreau", "Raça!B4": "Moreau Coruja", "Raça!B11": "Força"}
    assert campos.aplicar(moreau, {"Raça!B4": "Moreau Leão"}) == {"PJ!S2": "Moreau", "Raça!B4": "Moreau Leão"}
    assert campos.aplicar(moreau, {"PJ!S2": "Elfo"}) == {"PJ!S2": "Elfo"}


def test_validar_escolha_contra_a_lista_calculada():
    c = campos.CAMPOS["Raça!B11"]
    lista = {"rotulo": "Atributo 1", "livre": False, "opcoes": ("Força", "Destreza")}
    assert campos.validar_escolha(c, "destreza", lista) == "Destreza"
    assert campos.validar_escolha(c, None, None) is None
    assert campos.validar_escolha(c, "Tabu do sol", {"rotulo": "Tabu", "livre": True, "opcoes": ()}) == "Tabu do sol"
    for valor, escolha in (("Carisma", lista), ("Força", None)):
        with pytest.raises(campos.ErroCampo):
            campos.validar_escolha(c, valor, escolha)


def test_recursos_fora_do_editor_mas_na_whitelist():
    no_editor = {c.endereco for _, blocos in campos.SECOES for b in blocos for _, l in b.linhas for c in l if c}
    for endereco in ("PJ!S14", "PJ!X14", "PJ!AA14", "PJ!AF14"):
        assert endereco in campos.CAMPOS and endereco not in no_editor
    mudancas, erros = campos.ler_formulario(MultiDict([("PJ!S14", "12"), ("PJ!X14", "-1")]))
    assert mudancas == {"PJ!S14": 12} and "PJ!X14" in erros  # PV temporário não fica negativo


def test_outros_das_pericias_e_o_ajuste_manual_da_planilha():
    pericias = dict(campos.SECOES)["Perícias"][0]
    assert pericias.colunas == ("Treinada", "Atributo", "Outros")
    assert [linha[2].endereco for _, linha in pericias.linhas] == [f"Ajustes!B{l}" for l in range(29, 58)]
    assert not [e for e in campos.CAMPOS if e.startswith("PJ!O")]  # PJ!O8:O36 não entra em fórmula nenhuma
    mudancas, erros = campos.ler_formulario(MultiDict([("PJ!O26", "2"), ("Ajustes!B47", "2"), ("Ajustes!B29", "-99")]))
    assert mudancas == {"Ajustes!B47": 2, "Ajustes!B29": -99} and list(erros) == ["PJ!O26"]
    for bruto in ("100", "-100", "1,5", "dois"):
        with pytest.raises(campos.ErroCampo):
            campos.validar(campos.CAMPOS["Ajustes!B47"], bruto)


def test_editor_nao_repete_campos():
    enderecos = [c.endereco for _, blocos in campos.SECOES for b in blocos for _, linha in b.linhas for c in linha if c]
    assert len(enderecos) == len(set(enderecos))


def test_aplicar_descarta_entradas_que_sairam_do_catalogo():
    assert campos.aplicar({"PJ!B3": "Kael", "PJ!O26": 3}, {"Ajustes!B47": 2}) == {"PJ!B3": "Kael", "Ajustes!B47": 2}
