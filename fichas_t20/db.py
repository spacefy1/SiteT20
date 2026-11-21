"""Acesso ao SQLite: usuários, fichas e rolagens (o chat). Fichas e rolagens guardam o dono."""
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

CAMINHO = Path(__file__).with_name("fichas.db")


@contextmanager
def _conectar():
    with closing(sqlite3.connect(CAMINHO)) as con, con:
        con.row_factory = sqlite3.Row
        con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY, usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
            nome TEXT NOT NULL, papel TEXT NOT NULL CHECK (papel IN ('jogador', 'mestre')),
            senha_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fichas (
            id INTEGER PRIMARY KEY, nome TEXT, raca TEXT, classe TEXT, nivel INTEGER,
            dados TEXT NOT NULL, criada_em TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS rolagens (
            id INTEGER PRIMARY KEY, ficha_id INTEGER NOT NULL, dados TEXT NOT NULL,
            criada_em TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS ficha_condicoes (
            ficha_id INTEGER NOT NULL REFERENCES fichas(id), codigo TEXT NOT NULL,
            PRIMARY KEY (ficha_id, codigo));
        CREATE TABLE IF NOT EXISTS ficha_condicoes_itens (
            ficha_id INTEGER NOT NULL REFERENCES fichas(id), item TEXT NOT NULL, codigo TEXT NOT NULL,
            PRIMARY KEY (ficha_id, item));
        CREATE TABLE IF NOT EXISTS ficha_turnos (
            ficha_id INTEGER PRIMARY KEY REFERENCES fichas(id), numero INTEGER NOT NULL,
            mensagens TEXT NOT NULL, confusao INTEGER);""")
        for tabela, coluna in (("fichas", "dono_id"), ("rolagens", "usuario_id")):  # bancos anteriores aos usuários
            if coluna not in {c["name"] for c in con.execute(f"PRAGMA table_info({tabela})")}:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} INTEGER")
        yield con


def _um(linha):
    return dict(linha) if linha else None


# ---- usuários
def usuario(id=None, login=None):
    campo, valor = ("id", id) if id is not None else ("usuario", login)
    with _conectar() as con:
        return _um(con.execute(f"SELECT * FROM usuarios WHERE {campo} = ?", (valor,)).fetchone())


def usuarios():
    with _conectar() as con:
        return [dict(l) for l in con.execute(
            "SELECT id, usuario, nome, papel FROM usuarios ORDER BY papel = 'mestre', id")]


def criar_usuario(login, nome, papel, senha_hash):
    with _conectar() as con:
        con.execute("INSERT INTO usuarios (usuario, nome, papel, senha_hash) VALUES (?,?,?,?)",
                    (login, nome, papel, senha_hash))


def definir_senha(login, senha_hash):
    with _conectar() as con:
        return con.execute("UPDATE usuarios SET senha_hash = ? WHERE usuario = ?", (senha_hash, login)).rowcount


# ---- fichas
def listar(u):
    """Mestre vê todas as fichas; jogador vê só as suas."""
    sql, args = "SELECT f.id, f.dados, d.nome AS dono FROM fichas f LEFT JOIN usuarios d ON d.id = f.dono_id", ()
    if u["papel"] != "mestre":
        sql, args = sql + " WHERE f.dono_id = ?", (u["id"],)
    with _conectar() as con:
        linhas = con.execute(sql + " ORDER BY f.id DESC", args).fetchall()
    fichas = []
    for l in linhas:
        f = json.loads(l["dados"])
        fichas.append({"id": l["id"], "dono": l["dono"], "nome": f["nome"], "raca": f["raca"],
                       "jogador": f["jogador"], "classe": f["classes"][0]["classe"], "nivel": f["nivel"]})
    return fichas


def salvar(f, dono_id):
    with _conectar() as con:
        return con.execute(
            "INSERT INTO fichas (nome, raca, classe, nivel, dados, dono_id) VALUES (?,?,?,?,?,?)",
            (f["nome"], f["raca"], f["classes"][0]["classe"], f["nivel"],
             json.dumps(f, ensure_ascii=False), dono_id)).lastrowid


def obter(id):
    """Devolve (ficha, id_do_dono), ou (None, None) se não existir."""
    with _conectar() as con:
        l = con.execute("SELECT dados, dono_id FROM fichas WHERE id = ?", (id,)).fetchone()
    return (json.loads(l["dados"]), l["dono_id"]) if l else (None, None)


def _condicoes_da_ficha(con, ficha_id):
    condicoes = {l["codigo"] for l in con.execute(
        "SELECT codigo FROM ficha_condicoes WHERE ficha_id = ?", (ficha_id,))}
    itens = {l["item"]: l["codigo"] for l in con.execute(
        "SELECT item, codigo FROM ficha_condicoes_itens WHERE ficha_id = ?", (ficha_id,))}
    return condicoes, itens


def condicoes_da_ficha(ficha_id):
    with _conectar() as con:
        return _condicoes_da_ficha(con, ficha_id)


def _turno_da_ficha(con, ficha_id):
    l = con.execute("SELECT numero, mensagens, confusao FROM ficha_turnos WHERE ficha_id = ?", (ficha_id,)).fetchone()
    return {"numero": l["numero"], "mensagens": json.loads(l["mensagens"]), "confusao": l["confusao"]} if l else {
        "numero": 0, "mensagens": [], "confusao": None,
    }


def estado_da_ficha(ficha_id):
    with _conectar() as con:
        con.execute("BEGIN")
        dados = con.execute("SELECT dados FROM fichas WHERE id = ?", (ficha_id,)).fetchone()["dados"]
        ativas, itens = _condicoes_da_ficha(con, ficha_id)
        turno = _turno_da_ficha(con, ficha_id)
    return json.loads(dados), ativas, itens, turno


def _inserir_condicao(con, ficha_id, codigo):
    return con.execute("INSERT INTO ficha_condicoes (ficha_id, codigo) VALUES (?,?) "
                       "ON CONFLICT (ficha_id, codigo) DO NOTHING", (ficha_id, codigo)).rowcount


def _remover_condicao(con, ficha_id, codigo):
    return con.execute("DELETE FROM ficha_condicoes WHERE ficha_id = ? AND codigo = ?", (ficha_id, codigo)).rowcount


def marcar_condicao(ficha_id, codigo, ativa, agravar_para=None):
    with _conectar() as con:
        if agravar_para:
            if _remover_condicao(con, ficha_id, codigo):
                _inserir_condicao(con, ficha_id, agravar_para)
            return
        if ativa:
            alterada = _inserir_condicao(con, ficha_id, codigo)
        else:
            alterada = _remover_condicao(con, ficha_id, codigo)
        if alterada and codigo == "confuso":
            con.execute("UPDATE ficha_turnos SET confusao = NULL WHERE ficha_id = ?", (ficha_id,))


def definir_condicao_item(ficha_id, item, codigo):
    with _conectar() as con:
        if codigo:
            con.execute("INSERT INTO ficha_condicoes_itens (ficha_id, item, codigo) VALUES (?,?,?) "
                        "ON CONFLICT (ficha_id, item) DO UPDATE SET codigo = excluded.codigo",
                        (ficha_id, item, codigo))
        else:
            con.execute("DELETE FROM ficha_condicoes_itens WHERE ficha_id = ? AND item = ?", (ficha_id, item))


def resolver_turno(ficha_id, numero_anterior, calcular):
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        if _turno_da_ficha(con, ficha_id)["numero"] != numero_anterior:
            return
        f = json.loads(con.execute("SELECT dados FROM fichas WHERE id = ?", (ficha_id,)).fetchone()["dados"])
        ativas, _ = _condicoes_da_ficha(con, ficha_id)
        r = calcular(f, ativas)
        if not r.mensagens:
            return
        if r.pv is not None:
            con.execute("UPDATE fichas SET dados = json_set(dados, '$.pv.atual', ?) WHERE id = ?", (r.pv, ficha_id))
        for codigo in r.remover:
            _remover_condicao(con, ficha_id, codigo)
        for codigo in r.adicionar:
            _inserir_condicao(con, ficha_id, codigo)
        con.execute("INSERT INTO ficha_turnos (ficha_id, numero, mensagens, confusao) VALUES (?,?,?,?) "
                    "ON CONFLICT (ficha_id) DO UPDATE SET numero = excluded.numero, "
                    "mensagens = excluded.mensagens, confusao = excluded.confusao",
                    (ficha_id, numero_anterior + 1, json.dumps(r.mensagens, ensure_ascii=False), r.confusao))


# ---- rolagens (chat e histórico)
def registrar_rolagem(usuario_id, ficha_id, ficha_nome, rolagem):
    dados = {**rolagem, "ficha_id": ficha_id, "ficha": ficha_nome}
    with _conectar() as con:
        return con.execute("INSERT INTO rolagens (ficha_id, usuario_id, dados) VALUES (?,?,?)",
                           (ficha_id, usuario_id, json.dumps(dados, ensure_ascii=False))).lastrowid


def _rolagens(where, args, ordem, limite):
    sql = ("SELECT r.id, r.criada_em, r.dados, u.nome AS jogador FROM rolagens r "
           "LEFT JOIN usuarios u ON u.id = r.usuario_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _conectar() as con:
        linhas = con.execute(f"{sql} ORDER BY r.id {ordem} LIMIT ?", (*args, limite)).fetchall()
    return [{"id": l["id"], "hora": l["criada_em"].replace(" ", "T") + "Z", "jogador": l["jogador"],
             **json.loads(l["dados"])} for l in linhas]


def mensagens(u, depois=None, limite=50):
    """Chat em ordem cronológica. Mestre vê tudo; jogador vê só as próprias rolagens."""
    where, args = [], []
    if u["papel"] != "mestre":
        where.append("r.usuario_id = ?"); args.append(u["id"])
    if depois is None:
        return _rolagens(where, args, "DESC", limite)[::-1]
    return _rolagens(where + ["r.id > ?"], args + [depois], "ASC", 100)


def historico(jogador_id=None, tipo=None, antes=None, limite=100):
    """Só para o mestre. Mais recentes primeiro; `antes` é o cursor da página seguinte."""
    where, args = [], []
    if jogador_id:
        where.append("r.usuario_id = ?"); args.append(jogador_id)
    if tipo:
        where.append("json_extract(r.dados, '$.tipo') = ?"); args.append(tipo)
    if antes:
        where.append("r.id < ?"); args.append(antes)
    return _rolagens(where, args, "DESC", limite)


def ultimo_id_rolagem():
    """Id da rolagem mais recente (0 se não houver). Serve para saber, sem custo, se algo mudou."""
    with _conectar() as con:
        return con.execute("SELECT COALESCE(MAX(id), 0) FROM rolagens").fetchone()[0]


def todos_ataques():
    """Todas as rolagens de ataque, da mais antiga para a mais nova, com o jogador de cada uma."""
    return _rolagens(["json_extract(r.dados, '$.tipo') = ?"], ["ataque"], "ASC", -1)
