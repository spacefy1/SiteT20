"""Site de fichas T20.  Rodar:  flask --app app run --debug"""
import time
from io import BytesIO

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

import analytics
import auth
import cli
import condicoes
import dados
import db
import efeitos
from auth import login_obrigatorio, mestre_obrigatorio, usuario_atual
from leitor import ler_ficha

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # fichas têm ~300 KB
app.config["PERMANENT_SESSION_LIFETIME"] = 7 * 24 * 3600
auth.init_app(app)
cli.registrar(app)

ORIGEM_HABILIDADE = {"R": "Raça", "D": "Divindade", "O": "Origem"}


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


app.jinja_env.globals["ORIGEM_HABILIDADE"] = ORIGEM_HABILIDADE


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


@app.post("/enviar")
@login_obrigatorio
def enviar():
    u = usuario_atual()
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename.lower().endswith(".xlsx"):
        return _inicio("Escolha um arquivo .xlsx exportado da planilha de ficha.")
    try:
        ficha = ler_ficha(BytesIO(arquivo.read()))
    except Exception as e:  # planilha sem a aba PJ, sem nome, corrompida...
        return _inicio(str(e))
    dono = u["id"]
    if u["papel"] == "mestre" and request.form.get("dono", type=int):  # o mestre envia em nome de um jogador
        escolhido = db.usuario(id=request.form.get("dono", type=int))
        dono = escolhido["id"] if escolhido else dono
    return redirect(url_for("ficha", id=db.salvar(ficha, dono)))


@app.errorhandler(413)
def grande_demais(_):
    if not usuario_atual():
        return "Arquivo grande demais.", 413
    return _inicio("O arquivo passa de 5 MB. Fichas normais têm cerca de 300 KB.")


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
                           equipamentos=_nomes_equipamento(f))


@app.get("/ficha/<int:id>.json")
@login_obrigatorio
def ficha_json(id):
    _ficha_visivel(id)
    f, ativas, itens, _ = _ficha_com_efeitos(id)
    return jsonify({**f, "condicoes": sorted(ativas), "condicoes_itens": itens})


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
