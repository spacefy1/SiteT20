"""Site de fichas T20.  Rodar:  flask --app app run --debug"""
import re
import time
import unicodedata
from io import BytesIO

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

import analytics
import auth
import campos
import cli
import condicoes
import dados
import db
import efeitos
import modelo
import motor
from auth import login_obrigatorio, mestre_obrigatorio, usuario_atual

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # a planilha modelo tem cerca de 6 MB
app.config["PERMANENT_SESSION_LIFETIME"] = 7 * 24 * 3600
auth.init_app(app)
cli.registrar(app)

ORIGEM_HABILIDADE = {"R": "Raça", "D": "Divindade", "O": "Origem"}
FONTE_POR_LINHA = {39: "Raça", 40: "Origem"}  # PJ!AP39 mostra a raça e AP40 a origem
REGRA_LONGA = 320                             # na ficha, regras maiores ficam recolhidas


@app.template_filter("sinal")
def sinal(v):
    return f"{v:+d}".replace("-", "−") if isinstance(v, int) else "—"


@app.template_filter("pct")
def pct(atual, maximo):
    return round(min(max(atual / maximo, 0), 1) * 100) if atual is not None and maximo else 0


@app.template_filter("habilidade")
def habilidade(texto):
    """Separa 'Nome: descrição' (ou 'Nome. descrição') em (nome, descrição)."""
    for sep in (":", "."):
        ini, achou, resto = texto.partition(sep)
        if achou and len(ini) <= 50:
            return ini.strip(), resto.strip()
    return None, texto


@app.template_filter("fonte_habilidade")
def fonte_habilidade(h):
    """Rótulo de uma linha de RACIAL & ORIGEM; None quando não há (o modelo não imprime nada)."""
    return FONTE_POR_LINHA.get(h.get("linha")) or ORIGEM_HABILIDADE.get(h.get("marcador"), h.get("marcador"))


@app.template_test("aviso_problema")
def aviso_problema(aviso):
    """Avisos da aba Poderes escolhidos que pedem atenção ("Confira demais requisitos" é só lembrete)."""
    return isinstance(aviso, str) and aviso.startswith(("Nível insuficiente", "Repetido"))


SITUACAO = {"Escolhida": "ok", "A escolher": "pendente"}  # Raça!C11:C27; o resto (Revise, Repetida, Ignorada) é problema


@app.template_filter("situacao_classe")
def situacao_classe(situacao):
    return SITUACAO.get(situacao, "problema") if situacao else ""


@app.template_test("aviso_raca_problema")
def aviso_raca_problema(aviso):
    """Raça!A7 pede atenção quando há escolha pendente ou falta a raça/herança."""
    return isinstance(aviso, str) and aviso.startswith(("Há escolhas", "Escolha a herança", "Escolha uma raça"))


@app.template_filter("ancora")
def ancora(titulo):
    """'Perícias' -> 'pericias' (id das seções do editor)."""
    sem_acento = unicodedata.normalize("NFKD", titulo.casefold()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento).strip("-")


app.jinja_env.globals.update(REGRA_LONGA=REGRA_LONGA)


FALHAS = {}  # login -> (erros seguidos, hora do primeiro erro); bloqueia 5 min após 5 erros


def _bloqueado(login):
    n, desde = FALHAS.get(login, (0, 0))
    if time.time() - desde > 300:
        FALHAS.pop(login, None)
        return False
    return n >= 5


@app.route("/login", methods=["GET", "POST"])
def login():
    if usuario_atual():
        return redirect(url_for("index"))
    erro = None
    if request.method == "POST":
        nome = request.form.get("usuario", "").strip().lower()
        u = db.usuario(login=nome)
        if _bloqueado(nome):
            erro = "Muitas tentativas. Aguarde alguns minutos e tente de novo."
        elif u and check_password_hash(u["senha_hash"], request.form.get("senha", "")):
            FALHAS.pop(nome, None)
            session.clear()
            session["uid"], session.permanent = u["id"], True
            proximo = request.args.get("proximo", "")
            return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else url_for("index"))
        else:
            n, desde = FALHAS.get(nome, (0, time.time()))
            FALHAS[nome] = (n + 1, desde)
            erro = "Usuário ou senha incorretos."
    return render_template("login.html", erro=erro), (401 if erro else 200)


@app.post("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


def _inicio(erro=None):
    u = usuario_atual()
    pagina = render_template("index.html", fichas=db.listar(u), erro=erro,
                             donos=db.usuarios() if u["papel"] == "mestre" else [])
    return pagina, (400 if erro else 200)


@app.get("/")
@login_obrigatorio
def index():
    return _inicio()


def _dono_escolhido(u):
    """O mestre pode criar ou enviar uma ficha em nome de um jogador."""
    if u["papel"] == "mestre" and request.form.get("dono", type=int):
        escolhido = db.usuario(id=request.form.get("dono", type=int))
        if escolhido:
            return escolhido["id"]
    return u["id"]


def _ler_upload():
    """Arquivo do formulário -> (entradas, avisos). ValueError traz a mensagem para quem enviou."""
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename.lower().endswith(".xlsx"):
        raise ValueError("Escolha um arquivo .xlsx exportado da planilha de ficha.")
    return campos.entradas_da_planilha(BytesIO(arquivo.read()))


def _avisar(avisos):
    for aviso in avisos:
        flash(aviso, "aviso")


@app.post("/enviar")
@login_obrigatorio
def enviar():
    u = usuario_atual()
    try:
        entradas, avisos = _ler_upload()
        ficha = motor.calcular(entradas)
    except ValueError as e:  # outro modelo, sem nome, arquivo corrompido...
        return _inicio(str(e))
    _avisar(avisos)
    return redirect(url_for("ficha", id=db.salvar(ficha, _dono_escolhido(u), entradas)))


@app.post("/criar")
@login_obrigatorio
def criar_ficha():
    """Ficha em branco: só o nome, o resto se preenche no editor."""
    try:
        nome = campos.validar(campos.CAMPOS["PJ!B3"], request.form.get("nome", ""))
    except campos.ErroCampo as e:
        return _inicio(str(e))
    if not nome:
        return _inicio("Dê um nome ao personagem para criar a ficha.")
    entradas = {"PJ!B3": nome}
    f = motor.calcular(entradas)
    db.encher_recursos(f, entradas)  # PV e PM atuais não entram na conta: dispensa recalcular
    id = db.salvar(f, _dono_escolhido(usuario_atual()), entradas)
    return redirect(url_for("editar", id=id))


@app.errorhandler(413)
def grande_demais(_):
    if not usuario_atual():
        return "Arquivo grande demais.", 413
    return _inicio("O arquivo passa de 10 MB. A planilha modelo tem cerca de 6 MB.")


def _ficha_visivel(id):
    """Mestre vê qualquer ficha; jogador só a sua (as dos outros aparecem como inexistentes)."""
    f, dono = db.obter(id)
    u = usuario_atual()
    if not f or (u["papel"] != "mestre" and dono != u["id"]):
        abort(404)
    return f, dono, u


def _nomes_equipamento(f):
    registros = [*f["itens"], *f["ataques"], f["armadura"], f["escudo"]]
    nomes = (registro.get("nome") for registro in registros)
    return list(dict.fromkeys(nome.strip() for nome in nomes if isinstance(nome, str) and nome.strip()))


def _ficha_com_efeitos(id):
    f, ativas, itens, turno = db.estado_da_ficha(id)
    return efeitos.calcular(f, ativas, itens, turno["confusao"]), ativas, itens, turno


@app.get("/ficha/<int:id>")
@login_obrigatorio
def ficha(id):
    _, dono, u = _ficha_visivel(id)
    msgs = db.mensagens(u)
    f, ativas, itens, turno = _ficha_com_efeitos(id)
    return render_template("ficha.html", f=f, ficha_id=id, pode_rolar=(dono == u["id"]),
                           mensagens=msgs, ultimo=msgs[-1]["id"] if msgs else 0,
                           catalogo=condicoes.PERSONAGEM, ativas=ativas, turno=turno,
                           efeitos=f["efeitos_condicoes"], progressao=efeitos.PROGRESSAO,
                           catalogo_itens=condicoes.ITENS, condicoes_itens=itens,
                           equipamentos=_nomes_equipamento(f), editavel=db.entradas(id) is not None)


@app.get("/ficha/<int:id>.json")
@login_obrigatorio
def ficha_json(id):
    _ficha_visivel(id)
    f, ativas, itens, _ = _ficha_com_efeitos(id)
    return jsonify({**f, "condicoes": sorted(ativas), "condicoes_itens": itens})


# ---- edição da ficha (dono ou mestre; _ficha_visivel já esconde a ficha dos outros jogadores)
FICHA_ANTIGA = "Esta ficha veio de um envio antigo. Reenvie a planilha uma vez para liberar a edição."
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _valor_exibido(v):
    """Valor de uma célula como aparece no formulário."""
    if v is None:
        return ""
    if isinstance(v, float):
        return str(v).replace(".", ",")
    return str(v)


app.jinja_env.filters["campo_valor"] = _valor_exibido


def _contexto_editor(f, entradas, enviados):
    def valor(c):
        return _valor_exibido(campos.valor_atual(entradas, c.endereco))

    def mostrado(c):
        return enviados[c.endereco] if c.endereco in enviados else valor(c)

    def preenchida(linha):
        vazios = ("", "0", "-", campos.AUTOMATICO)
        return any(c and mostrado(c) not in vazios for c in linha)

    salvos = {p["linha"]: p for p in f.get("poderes", [])}

    def poder(c):
        """Descrição da opção na linha (do BD CATÁLOGO). O aviso depende do recálculo: só o da opção salva."""
        opcao = mostrado(c)
        info = modelo.carregar().catalogo.get(opcao)
        if not info:
            return None
        salvo = salvos.get(int(modelo.separar(c.endereco)[1][1:]), {})
        return {**info, "aviso": salvo.get("aviso") if salvo.get("opcao") == opcao else None}

    datalists = {campos.lista_id(c): campos.opcoes(c) for c in campos.CAMPOS.values()
                 if c.tipo == campos.LISTA and campos.modo(c) == "datalist"}
    return dict(campos=campos, secoes=campos.SECOES, valor=valor, mostrado=mostrado, preenchida=preenchida,
                poder=poder, datalists=datalists, AUTOMATICO=campos.AUTOMATICO, siglas=campos.SIGLAS,
                raca=_contexto_raca(f, entradas, mostrado))


def _contexto_raca(f, entradas, mostrado):
    """Seção Raça do editor: o resumo vem do último recálculo; as listas, do motor (com cache por raça)."""
    detalhe = f.get("raca_detalhe") or {}
    salvas = {e["linha"]: e for e in detalhe.get("escolhas", [])}
    opcoes = motor.opcoes_raciais(entradas)
    linhas = []
    for endereco in campos.ESCOLHAS_RACIAIS:
        l = int(modelo.separar(endereco)[1][1:])
        info, salva, atual = opcoes.get(endereco), salvas.get(l, {}), mostrado(campos.CAMPOS[endereco])
        if info is None and not atual:
            continue  # linha sem escolha nesta raça
        linhas.append({"campo": campos.CAMPOS[endereco], "info": info, "situacao": salva.get("situacao"),
                       "descricao": salva.get("descricao"), "rotulo": (info or {}).get("rotulo") or salva.get("rotulo")})
    return {"detalhe": detalhe, "linhas": linhas, "moreau": mostrado(campos.CAMPOS["PJ!S2"]) == "Moreau"}


def _validar_escolhas(entradas, mudancas, erros):
    """Escolhas raciais alteradas precisam estar na lista calculada para a raça (a que o editor mostrou)."""
    alteradas = {e: v for e, v in mudancas.items()
                 if e in campos.ESCOLHAS_RACIAIS and not campos.mesmo(v, entradas.get(e))}
    if not alteradas or campos.raca_mudou(entradas, mudancas):
        return  # raça nova: as escolhas antigas são apagadas ao salvar
    opcoes = motor.opcoes_raciais(entradas)
    for endereco, valor in alteradas.items():
        try:
            mudancas[endereco] = campos.validar_escolha(campos.CAMPOS[endereco], valor, opcoes.get(endereco))
        except campos.ErroCampo as e:
            erros[endereco] = str(e)


def _linhas_de_escolha(f):
    return [e["linha"] for e in (f.get("raca_detalhe") or {}).get("escolhas", []) if e.get("rotulo")]


@app.route("/ficha/<int:id>/editar", methods=["GET", "POST"])
@login_obrigatorio
def editar(id):
    f, _, _ = _ficha_visivel(id)
    entradas = db.entradas(id)
    if entradas is None:
        flash(FICHA_ANTIGA, "aviso")
        return redirect(url_for("ficha", id=id))
    erros, enviados = {}, {}
    if request.method == "POST":
        mudancas, erros = campos.ler_formulario(request.form)
        if "PJ!B3" in mudancas and not mudancas["PJ!B3"]:
            erros["PJ!B3"] = "O personagem precisa de um nome."
        _validar_escolhas(entradas, mudancas, erros)
        if not erros:
            try:
                novo = f
                # Mexeu na raça: o editor reabre na seção Raça, então as listas já saem deste recálculo
                mexeu_na_raca = any(e in campos.CHAVE_RACA for e in mudancas)
                if mudancas:
                    novo = db.atualizar_ficha(id, lambda atuais: campos.aplicar(atuais, mudancas),
                                              lambda e: motor.calcular(e, guardar_opcoes=mexeu_na_raca),
                                              acompanhar_recursos=True)
            except (ValueError, RuntimeError) as e:
                erros["geral"] = str(e)
            else:
                if campos.raca_mudou(entradas, mudancas) or _linhas_de_escolha(novo) != _linhas_de_escolha(f):
                    # Raça nova (ou escolha que libera outra): volta ao editor com as escolhas dela
                    flash("Ficha salva e recalculada. Confira as escolhas da raça.", "ok")
                    return redirect(url_for("editar", id=id) + "#secao-raca", code=303)
                flash("Ficha salva e recalculada." if mudancas else "Nada mudou na ficha.", "ok")
                return redirect(url_for("ficha", id=id), code=303)
        enviados = {k: request.form.getlist(k)[-1] for k in request.form if k != "csrf"}
    return render_template("editar.html", f=f, ficha_id=id, erros=erros,
                           **_contexto_editor(f, entradas, enviados)), (400 if erros else 200)


@app.get("/poderes/descricao")
@login_obrigatorio
def descricao_poder():
    """Descrição de uma opção do BD CATÁLOGO, para o editor mostrar assim que o jogador escolhe."""
    info = modelo.carregar().catalogo.get(request.args.get("opcao", "").strip())
    if not info:
        return "", 204
    return render_template("_poder.html", p=info)


@app.route("/ficha/<int:id>/excluir", methods=["GET", "POST"])
@login_obrigatorio
def excluir(id):
    """Confirmação numa página própria: é preciso digitar o nome do personagem."""
    f, _, _ = _ficha_visivel(id)
    erro = None
    if request.method == "POST":
        nome = f.get("nome") or ""
        if request.form.get("nome", "").strip().casefold() == nome.strip().casefold():
            db.excluir_ficha(id)
            flash(f"Ficha {nome} excluída.", "ok")
            return redirect(url_for("index"), code=303)
        erro = "O nome digitado não confere com o do personagem. Nada foi excluído."
    return render_template("excluir.html", f=f, ficha_id=id, erro=erro,
                           editavel=db.entradas(id) is not None), (400 if erro else 200)


@app.get("/opcoes/<lista>")
@login_obrigatorio
def buscar_opcoes(lista):
    """Sugestões para listas enormes (o catálogo de poderes tem 8,6 mil opções)."""
    c = campos.campo_da_lista(lista)
    if c is None:
        abort(404)
    return jsonify(campos.buscar(c, request.args.get("q", "")))


@app.post("/ficha/<int:id>/recurso")
@login_obrigatorio
def alterar_recurso(id):
    """PV, PM ou PV temporário pelos botões da ficha. Não recalcula a planilha."""
    _ficha_visivel(id)
    recurso = request.form.get("recurso")
    if recurso not in db.RECURSOS:
        abort(400, "Recurso inválido.")
    r = db.RECURSOS[recurso]
    try:
        if request.form.get("delta"):
            delta = campos.validar(campos.Campo("delta", "Variação", campos.INTEIRO), request.form["delta"])
            db.definir_recurso(id, recurso, delta=delta or 0)
        else:
            valor = campos.validar(campos.CAMPOS[r.celula], request.form.get("valor", ""))
            if valor is None:
                raise campos.ErroCampo("Digite um número.")
            db.definir_recurso(id, recurso, valor=valor)
    except campos.ErroCampo as e:
        flash(f"{r.nome}: {e}", "erro")
    return redirect(url_for("ficha", id=id) + "#recursos", code=303)


def _pericia_json(p):
    """Uma perícia como a página mostra (o total já vem com as condições aplicadas)."""
    texto = "Falha automática" if p.get("bloqueio") else "—" if p.get("total") is None else sinal(p["total"])
    return {"linha": p.get("linha"), "total": p.get("total"), "texto": texto, "outros": p.get("outros") or 0,
            "poderes": p.get("poderes") or 0, "poderes_texto": f"{sinal(p['poderes'])} de poderes" if p.get("poderes") else ""}


@app.post("/ficha/<int:id>/pericia/<int:linha>/outros")
@login_obrigatorio
def alterar_outros(id, linha):
    """Campo Outros (buffs) de uma perícia na página da ficha: grava Ajustes!B{linha+21} e recalcula.

    Responde JSON: {"pericias": [...]} com os totais novos, ou {"erro": "..."} e a ficha intacta.
    """
    _ficha_visivel(id)
    c = campos.CAMPOS.get(campos.outros_da_pericia(linha)) if 8 <= linha <= 36 else None
    if c is None:
        abort(404)
    if db.entradas(id) is None:
        return jsonify(erro=FICHA_ANTIGA), 400
    try:
        valor = campos.validar(c, request.form.get("valor", "")) or 0  # vazio vale 0
        db.atualizar_ficha(id, lambda atuais: campos.aplicar(atuais, {c.endereco: valor}), motor.calcular)
    except campos.ErroCampo as e:
        return jsonify(erro=str(e)), 400
    except (ValueError, RuntimeError) as e:
        return jsonify(erro=str(e)), 409
    f, _, _, _ = _ficha_com_efeitos(id)
    return jsonify(pericias=[_pericia_json(p) for p in f["pericias"]])


@app.post("/ficha/<int:id>/planilha")
@login_obrigatorio
def substituir_planilha(id):
    """Troca a planilha da ficha mantendo o id (condições, turno e rolagens continuam)."""
    _ficha_visivel(id)
    try:
        entradas, avisos = _ler_upload()
        db.atualizar_ficha(id, lambda _: entradas, motor.calcular)
    except (ValueError, RuntimeError) as e:
        flash(str(e), "erro")
    else:
        _avisar(avisos)
        flash("Planilha substituída.", "ok")
    return redirect(url_for("ficha", id=id), code=303)


@app.get("/ficha/<int:id>/planilha.xlsx")
@login_obrigatorio
def baixar_planilha(id):
    f, _, _ = _ficha_visivel(id)
    entradas = db.entradas(id)
    if entradas is None:
        flash(FICHA_ANTIGA, "aviso")
        return redirect(url_for("ficha", id=id))
    nome = re.sub(r"[^\w\- ]+", "", f.get("nome") or "").strip() or "ficha"
    return send_file(modelo.gerar_planilha(entradas), as_attachment=True, download_name=f"{nome}.xlsx", mimetype=XLSX)


@app.post("/ficha/<int:id>/condicoes")
@login_obrigatorio
def alterar_condicao(id):
    _ficha_visivel(id)
    codigo = request.form.get("condicao")
    acao = request.form.get("acao")
    if codigo not in condicoes.PERSONAGEM or acao not in ("marcar", "remover", "agravar"):
        abort(400, "Condição de personagem inválida.")
    agravamento = efeitos.PROGRESSAO.get(codigo) if acao == "agravar" else None
    if acao == "agravar" and not agravamento:
        abort(400, "Esta condição não pode ser agravada.")
    db.marcar_condicao(id, codigo, acao == "marcar", agravamento)
    return redirect(url_for("ficha", id=id) + "#condicoes", code=303)


@app.post("/ficha/<int:id>/condicoes-itens")
@login_obrigatorio
def alterar_condicao_item(id):
    f, _, _ = _ficha_visivel(id)
    item = request.form.get("item")
    codigo = request.form.get("condicao")
    if item not in _nomes_equipamento(f) or (codigo != "" and codigo not in condicoes.ITENS):
        abort(400, "Item ou condição inválida.")
    db.definir_condicao_item(id, item, codigo)
    return redirect(url_for("ficha", id=id) + "#condicoes-itens", code=303)


@app.post("/ficha/<int:id>/inicio-turno")
@login_obrigatorio
def inicio_turno(id):
    _ficha_visivel(id)
    anterior = request.form.get("turno", type=int)
    rd_fogo = request.form.get("rd_fogo", 0, type=int)
    if anterior is None or anterior < 0 or rd_fogo < 0:
        abort(400, "Dados do início do turno inválidos.")
    db.resolver_turno(id, anterior, lambda f, ativas: efeitos.iniciar_turno(f, ativas, rd_fogo))
    return redirect(url_for("ficha", id=id) + "#condicoes", code=303)


def _rolar_pericia(p, ferramenta, itens):
    if p.get("bloqueio"):
        raise ValueError(p["bloqueio"])
    estado = itens.get(ferramenta)
    if estado == "destruido":
        raise ValueError("A ferramenta está destruída.")
    if estado == "avariado" and isinstance(p.get("total"), int):
        p = {**p, "total": p["total"] - 5}
    return dados.rolar_pericia(p)


def _rolar_ataque(a, opcoes, chance_falha):
    if a.get("bloqueio"):
        raise ValueError(a["bloqueio"])
    if a.get("confirmar_arma_leve") and "leve" not in opcoes:
        raise ValueError("Agarrado permite apenas armas leves.")
    if a.get("redutor_se_corpo_a_corpo") and isinstance(a.get("bonus"), int):
        tipo = opcoes & {"corpo-a-corpo", "distancia"}
        if len(tipo) != 1:
            raise ValueError("Selecione se o ataque é corpo a corpo ou à distância.")
        if "corpo-a-corpo" in tipo:
            a = {**a, "bonus": a["bonus"] - a["redutor_se_corpo_a_corpo"]}
    return dados.rolar_ataque(a, chance_falha)


@app.post("/ficha/<int:id>/rolar")
@login_obrigatorio
def rolar(id):
    """Rola uma perícia ou ataque da própria ficha. alvo = 'pericia:3' ou 'ataque:0'."""
    _, dono, u = _ficha_visivel(id)
    if dono != u["id"]:
        abort(403)  # o mestre vê as fichas dos jogadores, mas não rola por eles
    f, _, itens, _ = _ficha_com_efeitos(id)
    tipo, _, resto = request.form.get("alvo", "").partition(":")
    indice, *opcoes = resto.split(":")
    lista = {"pericia": f["pericias"], "ataque": f["ataques"]}.get(tipo)
    try:
        i = int(indice)
        if lista is None or not 0 <= i < len(lista):
            raise ValueError("Rolagem inválida.")
        if tipo == "pericia":
            r = _rolar_pericia(lista[i], request.form.get("ferramenta"), itens)
        else:
            r = _rolar_ataque(lista[i], set(opcoes), f["efeitos_condicoes"]["chance_falha_ataque"])
    except ValueError as e:
        return str(e) or "Rolagem inválida.", 400
    msg_id = db.registrar_rolagem(u["id"], id, f["nome"], r)
    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("_mensagens.html", mensagens=db.mensagens(u, depois=msg_id - 1))
    return redirect(url_for("ficha", id=id) + "#chat")


@app.get("/chat/mensagens")
@login_obrigatorio
def chat_mensagens():
    return render_template("_mensagens.html",
                           mensagens=db.mensagens(usuario_atual(), depois=request.args.get("depois", 0, type=int)))


@app.get("/historico")
@mestre_obrigatorio
def historico():
    jogador = request.args.get("jogador", type=int)
    tipo = request.args.get("tipo") if request.args.get("tipo") in ("pericia", "ataque") else None
    msgs = db.historico(jogador, tipo, request.args.get("antes", type=int))
    return render_template("historico.html", msgs=msgs, contas=db.usuarios(), jogador=jogador, tipo=tipo,
                           proxima=msgs[-1]["id"] if len(msgs) == 100 else None)


# ---- Painel do mestre: tudo lido ao vivo do banco, só para o mestre
@app.get("/mestre/dashboard")
@mestre_obrigatorio
def dashboard_mestre():
    return render_template("dashboard_mestre.html")


@app.get("/mestre/ao-vivo")
@mestre_obrigatorio
def dashboard_ao_vivo():
    """Dados do painel. O navegador pergunta a cada poucos segundos; sem rolagem nova a resposta é mínima."""
    ultimo = db.ultimo_id_rolagem()
    if request.args.get("desde", type=int) == ultimo:
        return jsonify(mudou=False, ultimo=ultimo)
    df = analytics.df_danos()
    return jsonify(mudou=True, ultimo=ultimo, **analytics.resumo(df), recentes=analytics.recentes(df))


@app.get("/mestre/exportar-excel")
@mestre_obrigatorio
def exportar_excel_mestre():
    buffer = BytesIO()
    analytics.df_danos().to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="danos_t20.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/mestre/exportar-csv")
@mestre_obrigatorio
def exportar_csv_mestre():
    buffer = BytesIO(analytics.df_danos().to_csv(index=False).encode("utf-8-sig"))  # utf-8-sig: o Excel abre os acentos certos
    return send_file(buffer, as_attachment=True, download_name="danos_t20.csv", mimetype="text/csv")
