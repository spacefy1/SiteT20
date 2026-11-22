"""Rotas do site: editor, permissões, PV rápido, turno, planilha (enviar, substituir, baixar), poderes, exclusão."""
import copy
from pathlib import Path
from io import BytesIO

import openpyxl
import pytest

import campos
import condicoes
import db
import efeitos
import motor
from conftest import CENARIO

CSRF = {"csrf": "t"}


def test_dono_edita_e_os_numeros_sao_recalculados(entrar, ficha_da_ana):
    c = entrar("ana")
    assert c.get(f"/ficha/{ficha_da_ana}/editar").status_code == 200
    r = c.post(f"/ficha/{ficha_da_ana}/editar", data={**CSRF, "PJ!S2": "Elfo", "PJ!Q39": "-", "PJ!Q41": "-"})
    assert r.status_code == 303
    f, _ = db.obter(ficha_da_ana)
    assert (f["raca"], f["deslocamento"]) == ("Elfo", "12m")
    assert db.entradas(ficha_da_ana)["PJ!S2"] == "Elfo"
    with db._conectar() as con:
        linha = con.execute("SELECT raca, atualizada_em FROM fichas WHERE id = ?", (ficha_da_ana,)).fetchone()
    assert linha["raca"] == "Elfo" and linha["atualizada_em"]


def test_jogador_nao_edita_ficha_de_outro(entrar, ficha_da_ana):
    c = entrar("bruno")
    assert c.get(f"/ficha/{ficha_da_ana}/editar").status_code == 404
    assert c.post(f"/ficha/{ficha_da_ana}/editar", data={**CSRF, "PJ!S2": "Elfo"}).status_code == 404
    assert c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv", "delta": "-1"}).status_code == 404
    assert c.get(f"/ficha/{ficha_da_ana}/planilha.xlsx").status_code == 404
    assert db.entradas(ficha_da_ana)["PJ!S2"] == "Anão"


def test_mestre_edita_qualquer_ficha(entrar, ficha_da_ana):
    c = entrar("mestre")
    r = c.post(f"/ficha/{ficha_da_ana}/editar", data={**CSRF, "PJ!G36": "1"})
    assert r.status_code == 303
    assert db.entradas(ficha_da_ana)["PJ!G36"] == 1


def test_whitelist_recusa_celula_fora_do_catalogo_e_opcao_fora_da_lista(entrar, ficha_da_ana):
    c = entrar("ana")
    antes = db.entradas(ficha_da_ana)
    for dados in ({"PJ!V14": "99"}, {"PJ!F15": "3"}, {"BD ARMAS!B3": "x"}, {"PJ!S2": "Xablau"}, {"PJ!C12": "muito"}):
        r = c.post(f"/ficha/{ficha_da_ana}/editar", data={**CSRF, **dados, "PJ!B3": "Outro nome"})
        assert r.status_code == 400, dados
    assert db.entradas(ficha_da_ana) == antes


def test_sem_csrf_nao_grava(entrar, ficha_da_ana):
    r = entrar("ana").post(f"/ficha/{ficha_da_ana}/editar", data={"PJ!S2": "Elfo"})
    assert r.status_code == 400
    assert db.entradas(ficha_da_ana)["PJ!S2"] == "Anão"


def test_pv_rapido_nao_recalcula(entrar, ficha_da_ana, monkeypatch):
    def proibido(_):
        raise AssertionError("PV rápido não deveria recalcular a planilha")
    monkeypatch.setattr(motor, "calcular", proibido)
    c = entrar("ana")
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv", "delta": "-1"})
    assert db.obter(ficha_da_ana)[0]["pv"]["atual"] == 29
    assert db.entradas(ficha_da_ana)["PJ!S14"] == 29
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv", "valor": "-4"})
    assert db.obter(ficha_da_ana)[0]["pv"]["atual"] == -4
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pm", "valor": "5"})
    assert (db.obter(ficha_da_ana)[0]["pm"]["atual"], db.entradas(ficha_da_ana)["PJ!AA14"]) == (5, 5)


def test_resolver_turno_atualiza_entradas(ficha_da_ana):
    db.resolver_turno(ficha_da_ana, 0, lambda f, ativas: efeitos.InicioTurno(mensagens=["Sangrando"], pv=12))
    assert db.obter(ficha_da_ana)[0]["pv"]["atual"] == 12
    assert db.entradas(ficha_da_ana)["PJ!S14"] == 12
    f = db.atualizar_ficha(ficha_da_ana, lambda e: campos.aplicar(e, {"PJ!AO2": "Ana"}), motor.calcular)
    assert f["pv"]["atual"] == 12  # o recálculo seguinte não desfaz o dano


def test_baixar_planilha_e_reenviar_da_o_mesmo(entrar, ficha_da_ana):
    r = entrar("ana").get(f"/ficha/{ficha_da_ana}/planilha.xlsx")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(r.data))
    assert wb.calculation.fullCalcOnLoad
    assert (wb["PJ"]["B3"].value, wb["PJ"]["S2"].value, wb["PJ"]["AO2"].value) == ("Kael", "Anão", None)
    assert "_xlfn.IFNA(" in wb["PJ"]["AH49"].value  # sem o prefixo o Excel mostra #NAME? na carga
    duplicados = [f"{ws.title}!{c.coordinate}" for ws in wb.worksheets for linha in ws.iter_rows() for c in linha
                  if c.data_type == "f" and "_xlfn._xlfn." in str(c.value)]
    assert duplicados == []  # o modelo novo já traz o prefixo
    ocultas = {ws.title for ws in wb.worksheets if ws.sheet_state == "hidden"}
    assert {a for a in wb.sheetnames if a.startswith(("BD", "BR "))} == ocultas
    assert wb.active.title == "PJ" and "Raça" not in ocultas
    assert campos.entradas_da_planilha(BytesIO(r.data)) == (CENARIO, [])


def test_upload_da_planilha_v2_e_convertido(entrar, banco, tmp_path):
    from conftest import preencher
    v2 = preencher(tmp_path / "v2.xlsx", {**CENARIO, "Ajustes!B6": 1, "PJ!AP41": "Escrito à mão"}, "V2")
    entradas, avisos = campos.entradas_da_planilha(v2)
    assert entradas == {**CENARIO, "Ajustes!B6": 1}
    assert avisos[0] == "Planilha do modelo anterior convertida. Revise as escolhas raciais."
    assert "PJ!AP41" in avisos[1]  # no modelo novo AP41 é fórmula (nome do traço)
    with open(v2, "rb") as arq:
        r = entrar("ana").post("/enviar", data={**CSRF, "arquivo": (arq, "v2.xlsx")}, follow_redirects=True)
    assert "Planilha do modelo anterior convertida. Revise as escolhas raciais." in r.get_data(as_text=True)
    f = db.obter(int(r.request.path.rsplit("/", 1)[1]))[0]
    assert f["raca_detalhe"]["nome"] == "Anão" and f["atributos"]["FOR"]["mod"] == 4  # o ajuste manual vale


def test_texto_do_jogador_nao_vira_formula(ficha_da_ana):
    import modelo
    wb = openpyxl.load_workbook(modelo.gerar_planilha({**CENARIO, "PJ!C6": "=1+1"}))
    assert wb["PJ"]["C6"].data_type == "s"


def test_substituir_planilha_mantem_id_e_condicoes(entrar, ficha_da_ana, planilha_preenchida, tmp_path):
    from conftest import preencher
    db.marcar_condicao(ficha_da_ana, "abalado", True)
    nova = preencher(tmp_path / "nova.xlsx", {**CENARIO, "PJ!B3": "Kael Segundo"})
    with open(nova, "rb") as arq:
        r = entrar("ana").post(f"/ficha/{ficha_da_ana}/planilha", data={**CSRF, "arquivo": (arq, "nova.xlsx")})
    assert r.status_code == 303
    assert db.obter(ficha_da_ana)[0]["nome"] == "Kael Segundo"
    assert "abalado" in db.condicoes_da_ficha(ficha_da_ana)[0]
    with db._conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fichas").fetchone()[0] == 1


def test_enviar_e_criar_ficha_em_branco(entrar, planilha_preenchida):
    c = entrar("ana")
    with open(planilha_preenchida, "rb") as arq:
        r = c.post("/enviar", data={**CSRF, "arquivo": (arq, "kael.xlsx")})
    assert r.status_code == 302
    enviada = int(r.headers["Location"].rsplit("/", 1)[1])
    assert db.entradas(enviada) == CENARIO

    r = c.post("/criar", data={**CSRF, "nome": "Lyra"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/editar")
    nova = int(r.headers["Location"].split("/")[-2])
    assert db.entradas(nova) == {"PJ!B3": "Lyra"}
    assert db.obter(nova)[0]["nome"] == "Lyra"
    assert c.get(f"/ficha/{nova}").status_code == 200


def test_ficha_antiga_aparece_com_aviso(entrar, banco, dados_cenario):
    antiga = db.salvar(dados_cenario, db.usuario(login="ana")["id"])  # sem entradas
    c = entrar("ana")
    pagina = c.get(f"/ficha/{antiga}").get_data(as_text=True)
    assert "reenvie a planilha uma vez" in pagina.lower()
    assert c.get(f"/ficha/{antiga}/editar").status_code == 302
    # Sem editor, o botão de excluir fica na própria ficha; a confirmação não oferece planilha para baixar
    assert f'href="/ficha/{antiga}/excluir">Excluir ficha</a>' in pagina
    confirmar = c.get(f"/ficha/{antiga}/excluir").get_data(as_text=True)
    assert "Baixe a planilha antes" not in confirmar
    c.post(f"/ficha/{antiga}/excluir", data={**CSRF, "nome": "Kael"})
    assert db.obter(antiga) == (None, None)


def test_um_botao_de_excluir_por_pagina(entrar, ficha_da_ana, dados_cenario):
    antiga = db.salvar(dados_cenario, db.usuario(login="ana")["id"])
    c = entrar("ana")
    for url, ficha_id in ((f"/ficha/{ficha_da_ana}", ficha_da_ana), (f"/ficha/{ficha_da_ana}/editar", ficha_da_ana),
                          (f"/ficha/{antiga}", antiga)):
        pagina = c.get(url).get_data(as_text=True)
        assert pagina.count(f'href="/ficha/{ficha_id}/excluir"') == 1, url


def test_recalcular_fichas_mantem_pv_e_pm_atuais(site, ficha_da_ana, dados_cenario):
    antiga = db.salvar(dados_cenario, db.usuario(login="ana")["id"])  # sem entradas: fica de fora
    db.definir_recurso(ficha_da_ana, "pv", valor=12)
    db.definir_recurso(ficha_da_ana, "pv_temp", valor=3)
    db.definir_recurso(ficha_da_ana, "pm", valor=4)
    with db._conectar() as con:  # dados de antes da rodada 1: sem poderes nem PV temporário
        velho = {k: v for k, v in db.obter(ficha_da_ana)[0].items() if k != "poderes"}
        velho["pv"] = {"atual": 12, "max": 47}
        con.execute("UPDATE fichas SET dados = ? WHERE id = ?", (db._json(velho), ficha_da_ana))
        con.execute("UPDATE fichas SET dados = json_set(dados, '$.nivel', 99) WHERE id = ?", (antiga,))

    saida = site.test_cli_runner().invoke(args=["recalcular-fichas"]).output
    assert "1 ficha(s) recalculada(s), 1 com dados diferentes" in saida
    f = db.obter(ficha_da_ana)[0]
    assert (f["pv"], f["pm"]["atual"], f["poderes"]) == ({"atual": 12, "max": 47, "temporario": 3}, 4, [])
    assert db.obter(antiga)[0]["nivel"] == 99
    assert "0 com dados diferentes" in site.test_cli_runner().invoke(args=["recalcular-fichas"]).output


def test_recalcular_fichas_faz_backup_mostra_os_numeros_e_lista_ajustes_a_revisar(site, ficha_da_ana):
    with db._conectar() as con:  # ficha migrada da V2: Humano com o +1 em Ajustes!B6 e uma célula que saiu
        entradas = {**db.entradas(ficha_da_ana), "PJ!S2": "Humano", "Ajustes!B6": 1, "PJ!AP41": "à mão"}
        con.execute("UPDATE fichas SET entradas = ? WHERE id = ?", (db._json(entradas), ficha_da_ana))
    saida = site.test_cli_runner().invoke(args=["recalcular-fichas"]).output
    backup = saida.split("Backup do banco: ")[1].splitlines()[0]
    assert Path(backup).exists() and Path(backup).parent.name == "backups"
    assert "#%d Kael (Humano): " % ficha_da_ana in saida and "PV máx: 47 → 36" in saida
    assert "entradas fora do catálogo removidas: PJ!AP41" in saida
    assert "PJ!AP41" not in db.entradas(ficha_da_ana)
    assert f"  #{ficha_da_ana} Kael (Humano): Ajustes!B6=1" in saida.split("Revise à mão")[1]


def test_busca_de_opcoes(entrar, banco):
    lista = campos.lista_id(campos.CAMPOS["Poderes escolhidos!B5"])
    r = entrar("ana").get(f"/opcoes/{lista}?q=ataque poderoso")
    assert r.get_json() == ["Ataque Poderoso [JdA]"]


# ---- raça e atributos no editor
def _secao(pagina, nome):
    return pagina.split(f'id="secao-{nome}"')[1].split('<section class="secao"')[0]


def test_editor_atributos_so_com_a_base_e_ajustes_raciais_em_ajustes(entrar, ficha_da_ana):
    pagina = entrar("ana").get(f"/ficha/{ficha_da_ana}/editar").get_data(as_text=True)
    atributos = _secao(pagina, "atributos")
    assert 'name="PJ!C12"' in atributos and "Ajustes!" not in atributos and "data-escolha" not in atributos
    assert "<output>+4</output>" in atributos and "entram depois de salvar" in atributos  # CON 2 + 2 do Anão
    ajustes = _secao(pagina, "ajustes")
    assert 'name="Ajustes!B6"' in ajustes.split("Ajuste racial manual")[1].split("Aumentos de atributo")[0]
    assert 'name="Ajustes!B7"' in ajustes.split("Aumentos de atributo (poderes e outros)")[1]


def test_editor_secao_raca_do_anao(entrar, ficha_da_ana):
    raca = _secao(entrar("ana").get(f"/ficha/{ficha_da_ana}/editar").get_data(as_text=True), "raca")
    assert 'name="PJ!S2"' in raca and '<label for="c-Ra-a-B4" class="heranca" hidden>' in raca
    assert "Humanoide | Médio | Base 6 m | Visão no escuro" in raca and "JdA · PDF 26" in raca
    assert "escolhas-raca" not in raca


def test_trocar_de_raca_reabre_o_editor_com_as_escolhas_e_valida_a_lista(entrar, ficha_da_ana):
    from test_motor import ESCOLHAS_HUMANO
    c, editar = entrar("ana"), f"/ficha/{ficha_da_ana}/editar"
    r = c.post(editar, data={**CSRF, "PJ!S2": "Humano"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/editar#secao-raca")
    raca = _secao(c.get(editar).get_data(as_text=True), "raca")
    assert raca.count('<span class="situacao pendente">A escolher</span>') == 5
    assert "<option>Treino em Luta</option>" in raca and '<p class="aviso-poder problema">Há escolhas' in raca

    r = c.post(editar, data={**CSRF, "Raça!B11": "Treino em Luta"})  # opção de outra linha
    assert r.status_code == 400 and "Escolha uma opção da lista." in r.get_data(as_text=True)
    assert "Raça!B11" not in db.entradas(ficha_da_ana)

    c.post(editar, data={**CSRF, **ESCOLHAS_HUMANO, "Ajustes!B6": "1"})
    f = db.obter(ficha_da_ana)[0]
    assert f["atributos"]["FOR"]["mod"] == 5  # base 3 + escolha 1 + ajuste manual 1
    assert [p["nome"] for p in f["pericias"] if p["treinada_pela_raca"]] == ["Luta", "Percepção"]

    r = c.post(editar, data={**CSRF, "PJ!S2": "Anão", "Raça!B11": "Força"})
    assert r.headers["Location"].endswith("/editar#secao-raca")
    entradas = db.entradas(ficha_da_ana)
    assert not [e for e in entradas if e.startswith("Raça!")] and entradas["Ajustes!B6"] == 1  # o manual fica
    assert db.obter(ficha_da_ana)[0]["raca_detalhe"]["escolhas"] == []


def test_moreau_mostra_a_heranca_no_editor(entrar, ficha_da_ana):
    c, editar = entrar("ana"), f"/ficha/{ficha_da_ana}/editar"
    c.post(editar, data={**CSRF, "PJ!S2": "Moreau"})
    raca = _secao(c.get(editar).get_data(as_text=True), "raca")
    assert '<label for="c-Ra-a-B4" class="heranca">' in raca and "Escolha a herança acima." in raca
    c.post(editar, data={**CSRF, "Raça!B4": "Moreau Coruja"})
    assert db.obter(ficha_da_ana)[0]["raca_detalhe"]["nome"] == "Moreau Coruja"
    assert "Sapiência" in _secao(c.get(editar).get_data(as_text=True), "raca")


def test_ficha_mostra_os_cartoes_da_raca(entrar, ficha_da_ana):
    db.atualizar_ficha(ficha_da_ana, lambda e: {**e, "PJ!S2": "Humano", "Raça!B11": "Força",
                                               "Raça!B15": "Treino em Percepção"}, motor.calcular)
    pagina = entrar("ana").get(f"/ficha/{ficha_da_ana}").get_data(as_text=True)
    secao = pagina.split('id="habilidades"')[1].split("</section>")[0]
    cab = secao.split('<article class="poder raca-cab">')[1].split("</article>")[0]
    assert "<strong>Humano</strong>" in cab and "JdA · PDF 25" in cab and "Base 9 m" in cab and "Força: +1" in cab
    assert '<p class="aviso-poder problema">Há escolhas raciais pendentes' in cab
    assert '<article class="poder traco">' in secao and "<strong>Versátil</strong>" in secao
    assert '<article class="poder escolha-raca ok">' in secao and "<strong>Atributo 1:</strong> Força" in secao
    assert "+1 em Força." in secao
    assert "<strong>Atributo 2:</strong> (a escolher)" in secao and '<p class="aviso-poder problema">A escolher</p>' in secao
    assert "aba Raça" not in secao and "None" not in secao      # AP41:AP45 não aparecem mais
    assert "<strong>Percepção</strong>" not in secao
    assert 'class="treinada pela-raca"' in pagina and " · raça</small>" in pagina


# ---- PV e PM fora do editor
def test_recursos_sairam_do_editor(entrar, ficha_da_ana):
    pagina = entrar("ana").get(f"/ficha/{ficha_da_ana}/editar").get_data(as_text=True)
    for endereco in ("PJ!S14", "PJ!X14", "PJ!AA14", "PJ!AF14"):
        assert f'name="{endereco}"' not in pagina


def test_ficha_criada_comeca_com_pv_e_pm_cheios(entrar, banco, dados_cenario, monkeypatch):
    monkeypatch.setattr(motor, "calcular", lambda e: {**copy.deepcopy(dados_cenario), "nome": e["PJ!B3"]})
    r = entrar("ana").post("/criar", data={**CSRF, "nome": "Lyra"})
    nova = int(r.headers["Location"].split("/")[-2])
    f = db.obter(nova)[0]
    assert (f["pv"]["atual"], f["pm"]["atual"]) == (47, 9)
    assert db.entradas(nova) == {"PJ!B3": "Lyra", "PJ!S14": 47, "PJ!AA14": 9}


def test_pv_e_pm_acompanham_o_maximo_ao_salvar(entrar, ficha_da_ana):
    c = entrar("ana")

    def nivel(n):
        c.post(f"/ficha/{ficha_da_ana}/editar", data={**CSRF, "PJ!Z4": str(n)})
        f = db.obter(ficha_da_ana)[0]
        return f["pv"], f["pm"]

    db.definir_recurso(ficha_da_ana, "pv", valor=47)   # cheio
    pv, pm = nivel(4)                                  # o PM estava em 0 de 9: fica
    assert pv["atual"] == pv["max"] > 47 and pm["atual"] == 0
    assert db.entradas(ficha_da_ana)["PJ!S14"] == pv["max"]
    db.definir_recurso(ficha_da_ana, "pv", valor=10)   # abaixo do máximo: fica como está
    assert nivel(3)[0] == {"atual": 10, "max": 47, "temporario": None}
    db.definir_recurso(ficha_da_ana, "pv", valor=40)
    pv, _ = nivel(1)                                   # passou do novo máximo: desce até ele
    assert pv["atual"] == pv["max"] < 40


def test_substituir_planilha_mantem_o_pv_que_veio_nela(entrar, ficha_da_ana, tmp_path):
    from conftest import preencher
    db.definir_recurso(ficha_da_ana, "pv", valor=47)
    nova = preencher(tmp_path / "pv.xlsx", {**CENARIO, "PJ!Z4": 4, "PJ!S14": 12})
    with open(nova, "rb") as arq:
        entrar("ana").post(f"/ficha/{ficha_da_ana}/planilha", data={**CSRF, "arquivo": (arq, "pv.xlsx")})
    assert db.obter(ficha_da_ana)[0]["pv"]["atual"] == 12


def test_pv_temporario_pelo_caminho_rapido(entrar, ficha_da_ana, monkeypatch):
    def proibido(_):
        raise AssertionError("PV temporário não deveria recalcular a planilha")
    monkeypatch.setattr(motor, "calcular", proibido)
    c = entrar("ana")
    pagina = c.get(f"/ficha/{ficha_da_ana}").get_data(as_text=True)
    assert "<summary>+ PV temporário</summary>" in pagina and 'class="recurso pv-temp"' not in pagina
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv_temp", "delta": "1"})
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv_temp", "valor": "5"})
    assert db.obter(ficha_da_ana)[0]["pv"] == {"atual": 30, "max": 47, "temporario": 5}
    assert db.entradas(ficha_da_ana)["PJ!X14"] == 5
    pagina = c.get(f"/ficha/{ficha_da_ana}").get_data(as_text=True)
    assert 'class="recurso pv-temp"' in pagina and "width:11%" in pagina  # 5 de 47 PV máximos
    r = c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv_temp", "valor": "-3"},
               follow_redirects=True)
    assert "PV temporário: O mínimo é 0." in r.get_data(as_text=True)
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv_temp", "valor": "0"})
    c.post(f"/ficha/{ficha_da_ana}/recurso", data={**CSRF, "recurso": "pv_temp", "delta": "-1"})
    assert db.obter(ficha_da_ana)[0]["pv"]["temporario"] == 0 and "PJ!X14" not in db.entradas(ficha_da_ana)


# ---- poderes
@pytest.fixture
def ficha_com_poderes(ficha_da_ana):
    from test_motor import PODERES
    origem = next(o for o in campos.opcoes(campos.CAMPOS["PJ!Z2"]) if o != "-")
    db.atualizar_ficha(ficha_da_ana, lambda e: {**e, **PODERES, "PJ!Z2": origem}, motor.calcular)
    return ficha_da_ana, origem


def test_ficha_mostra_os_poderes_em_cartoes(entrar, ficha_com_poderes):
    ficha_id, origem = ficha_com_poderes
    pagina = entrar("ana").get(f"/ficha/{ficha_id}").get_data(as_text=True)
    habilidades = pagina.split('id="habilidades"')[1].split("</section>")[0]
    assert '<span class="fonte">Raça</span>' not in habilidades  # a raça agora vem em cartões
    assert f'<span class="fonte">Origem</span>{origem}' in habilidades
    assert "None" not in habilidades
    secao = habilidades.split('<h3 class="sub">Poderes</h3>')[1]
    assert secao.count('<article class="poder') == 4
    assert "<strong>Ataque Poderoso</strong>" in secao and "JdA, p. 130 · nível mínimo 0" in secao
    assert "<b>Pré-requisitos:</b> For 1." in secao
    assert '<p class="aviso-poder problema">Nível insuficiente</p>' in secao
    assert '<p class="aviso-poder">Confira demais requisitos</p>' in secao
    assert secao.count('<article class="poder inativo">') == 3 and '<span class="selo">Inativo</span>' in secao


def test_editor_mostra_a_descricao_dos_poderes(entrar, ficha_com_poderes):
    ficha_id, _ = ficha_com_poderes
    c = entrar("ana")
    pagina = c.get(f"/ficha/{ficha_id}/editar").get_data(as_text=True)
    assert "Sempre que faz um ataque corpo a corpo" in pagina
    assert '<p class="aviso-poder problema">Nível insuficiente</p>' in pagina
    r = c.get("/poderes/descricao?opcao=Ataque Poderoso [JdA]")
    texto = r.get_data(as_text=True)
    assert r.status_code == 200 and "<strong>Ataque Poderoso</strong>" in texto and "nível mínimo 0" in texto
    assert "<summary>Regra</summary>" in texto and "aparece depois de salvar" in texto
    assert c.get("/poderes/descricao?opcao=Não existe").status_code == 204


# ---- excluir
def test_jogador_nao_exclui_ficha_de_outro(entrar, ficha_da_ana):
    c = entrar("bruno")
    assert c.get(f"/ficha/{ficha_da_ana}/excluir").status_code == 404
    assert c.post(f"/ficha/{ficha_da_ana}/excluir", data={**CSRF, "nome": "Kael"}).status_code == 404
    assert db.obter(ficha_da_ana)[0] is not None


def test_nome_errado_nao_exclui(entrar, ficha_da_ana):
    c = entrar("ana")
    assert "Excluir ficha" in c.get(f"/ficha/{ficha_da_ana}/editar").get_data(as_text=True)
    pagina = c.get(f"/ficha/{ficha_da_ana}/excluir").get_data(as_text=True)
    assert "Baixe a planilha antes" in pagina and "confirm(" not in pagina
    r = c.post(f"/ficha/{ficha_da_ana}/excluir", data={**CSRF, "nome": "Kae"})
    assert r.status_code == 400 and "não confere" in r.get_data(as_text=True)
    assert c.post(f"/ficha/{ficha_da_ana}/excluir", data={"nome": "Kael"}).status_code == 400  # sem csrf
    assert db.obter(ficha_da_ana)[0] is not None


def test_mestre_exclui_e_as_rolagens_ficam(entrar, ficha_da_ana):
    import analytics
    entrar("ana").post(f"/ficha/{ficha_da_ana}/rolar", data={**CSRF, "alvo": "ataque:0"})
    db.marcar_condicao(ficha_da_ana, "abalado", True)
    db.definir_condicao_item(ficha_da_ana, "Espada longa", next(iter(condicoes.ITENS)))
    db.resolver_turno(ficha_da_ana, 0, lambda f, ativas: efeitos.InicioTurno(mensagens=["Sangrando"], pv=12))

    r = entrar("mestre").post(f"/ficha/{ficha_da_ana}/excluir", data={**CSRF, "nome": " kael "})
    assert r.status_code == 303 and r.headers["Location"].endswith("/")
    assert db.obter(ficha_da_ana) == (None, None)
    with db._conectar() as con:
        for tabela in ("ficha_condicoes", "ficha_condicoes_itens", "ficha_turnos"):
            assert con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] == 0, tabela
    # chat do jogador, histórico e painel do mestre continuam com a rolagem e o nome do personagem
    assert [m["ficha"] for m in db.mensagens(db.usuario(login="ana"))] == ["Kael"]
    assert [m["ficha"] for m in db.historico()] == ["Kael"]
    assert list(analytics.df_danos()["origem"]) == ["Kael"]
    assert "Kael" in entrar("mestre").get("/historico").get_data(as_text=True)


def test_dono_exclui_e_volta_para_o_inicio_com_aviso(entrar, ficha_da_ana):
    r = entrar("ana").post(f"/ficha/{ficha_da_ana}/excluir", data={**CSRF, "nome": "Kael"}, follow_redirects=True)
    assert r.status_code == 200 and "Ficha Kael excluída." in r.get_data(as_text=True)
    assert db.obter(ficha_da_ana) == (None, None)


# ---- Outros (buffs) nas perícias da página da ficha
def _outros(cliente, ficha_id, valor, linha=26):  # linha 26 da PJ = Luta
    return cliente.post(f"/ficha/{ficha_id}/pericia/{linha}/outros", data={**CSRF, "valor": valor},
                        headers={"X-Requested-With": "fetch"})


def _luta(pericias):
    return next(p for p in pericias if p["linha"] == 26)


def test_ficha_mostra_o_campo_outros(entrar, ficha_da_ana):
    pagina = entrar("ana").get(f"/ficha/{ficha_da_ana}").get_data(as_text=True)
    luta = pagina.split('data-linha="26"')[1].split("</li>")[0]
    assert f'data-url="/ficha/{ficha_da_ana}/pericia/26/outros"' in luta and 'class="outros"' in luta
    assert 'max="99"' in luta and 'value=""' in luta and "data-total>+6</button>" in luta
    assert "Outros: bônus adicionais (buffs), já somados ao total." in pagina
    assert 'src="/static/pericias.js"' in pagina


def test_outros_mais_2_em_luta_e_a_rolagem_usa_o_total_novo(entrar, ficha_da_ana):
    c = entrar("ana")
    r = _outros(c, ficha_da_ana, "2")
    assert r.status_code == 200
    assert _luta(r.get_json()["pericias"]) == {"linha": 26, "total": 8, "texto": "+8", "outros": 2, "poderes": 0,
                                               "poderes_texto": ""}
    assert db.entradas(ficha_da_ana)["Ajustes!B47"] == 2
    f = db.obter(ficha_da_ana)[0]
    indice = next(i for i, p in enumerate(f["pericias"]) if p["nome"] == "Luta")
    c.post(f"/ficha/{ficha_da_ana}/rolar", data={**CSRF, "alvo": f"pericia:{indice}"})
    rolagem = db.historico()[0]
    assert (rolagem["nome"], rolagem["bonus"], rolagem["total"]) == ("Luta", 8, rolagem["natural"] + 8)  # +2 uma vez só
    pagina = c.get(f"/ficha/{ficha_da_ana}").get_data(as_text=True).split('data-linha="26"')[1].split("</li>")[0]
    assert 'value="2"' in pagina and "data-total>+8</button>" in pagina

    r = _outros(c, ficha_da_ana, "")  # vazio vale 0
    assert _luta(r.get_json()["pericias"])["total"] == 6 and "Ajustes!B47" not in db.entradas(ficha_da_ana)


def test_outros_com_condicao_devolve_o_total_da_pagina(entrar, ficha_da_ana):
    db.marcar_condicao(ficha_da_ana, "abalado", True)  # -2 nos testes de perícia
    pagina = entrar("ana").get(f"/ficha/{ficha_da_ana}").get_data(as_text=True)
    na_pagina = pagina.split('data-linha="26"')[1].split("data-total>")[1].split("<")[0]
    assert _luta(_outros(entrar("ana"), ficha_da_ana, "1").get_json()["pericias"])["texto"] == sinal_mais(na_pagina, 1)


def sinal_mais(texto, n):
    valor = int(texto.replace("−", "-")) + n
    return f"{valor:+d}".replace("-", "−")


def test_outros_so_para_o_dono_e_o_mestre(entrar, ficha_da_ana):
    assert _outros(entrar("bruno"), ficha_da_ana, "2").status_code == 404
    assert "Ajustes!B47" not in db.entradas(ficha_da_ana)
    assert _outros(entrar("mestre"), ficha_da_ana, "3").status_code == 200
    assert db.entradas(ficha_da_ana)["Ajustes!B47"] == 3


@pytest.mark.parametrize("valor", ["abc", "100", "-100", "1,5"])
def test_outros_invalido_nao_mexe_na_ficha(entrar, ficha_da_ana, valor):
    antes = (db.entradas(ficha_da_ana), db.obter(ficha_da_ana)[0])
    r = _outros(entrar("ana"), ficha_da_ana, valor)
    assert r.status_code == 400 and r.get_json()["erro"]
    assert (db.entradas(ficha_da_ana), db.obter(ficha_da_ana)[0]) == antes


def test_outros_linha_fora_das_pericias_e_ficha_antiga(entrar, banco, ficha_da_ana, dados_cenario):
    c = entrar("ana")
    assert _outros(c, ficha_da_ana, "1", linha=7).status_code == 404
    antiga = db.salvar(dados_cenario, db.usuario(login="ana")["id"])
    r = _outros(c, antiga, "1")
    assert r.status_code == 400 and "Reenvie a planilha" in r.get_json()["erro"]
    assert 'class="outros"' not in c.get(f"/ficha/{antiga}").get_data(as_text=True)  # só texto
