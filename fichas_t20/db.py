"""Acesso ao SQLite: usuários, fichas e rolagens (o chat). Fichas e rolagens guardam o dono."""
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import NamedTuple

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
        for tabela, coluna, tipo in (("fichas", "dono_id", "INTEGER"), ("rolagens", "usuario_id", "INTEGER"),  # usuários
                                     ("fichas", "entradas", "TEXT"), ("fichas", "atualizada_em", "TEXT")):  # editor
            if coluna not in {c["name"] for c in con.execute(f"PRAGMA table_info({tabela})")}:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
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


def _json(valor):
    return json.dumps(valor, ensure_ascii=False)


def salvar(f, dono_id, entradas=None):
    with _conectar() as con:
        return con.execute(
            "INSERT INTO fichas (nome, raca, classe, nivel, dados, dono_id, entradas, atualizada_em) "
            "VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (f["nome"], f["raca"], f["classes"][0]["classe"], f["nivel"], _json(f), dono_id,
             None if entradas is None else _json(entradas))).lastrowid


def obter(id):
    """Devolve (ficha, id_do_dono), ou (None, None) se não existir."""
    with _conectar() as con:
        l = con.execute("SELECT dados, dono_id FROM fichas WHERE id = ?", (id,)).fetchone()
    return (json.loads(l["dados"]), l["dono_id"]) if l else (None, None)


def _entradas(con, ficha_id):
    l = con.execute("SELECT entradas FROM fichas WHERE id = ?", (ficha_id,)).fetchone()
    return json.loads(l["entradas"]) if l and l["entradas"] is not None else None


def backup():
    """Cópia consistente do banco em backups/ (ao lado do banco); devolve o caminho."""
    from datetime import datetime
    pasta = Path(CAMINHO).parent / "backups"
    pasta.mkdir(exist_ok=True)
    destino = pasta / f"{Path(CAMINHO).stem}_{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(CAMINHO)) as origem, closing(sqlite3.connect(destino)) as copia:
        origem.backup(copia)
    return destino


def fichas_editaveis():
    """Ids das fichas com edição liberada (as que guardam entradas)."""
    with _conectar() as con:
        return [l["id"] for l in con.execute("SELECT id FROM fichas WHERE entradas IS NOT NULL ORDER BY id")]


def entradas(ficha_id):
    """Células editadas da ficha ({"PJ!B3": "Kael", ...}), ou None em fichas antigas (só `dados`)."""
    with _conectar() as con:
        return _entradas(con, ficha_id)


class Recurso(NamedTuple):
    nome: str
    celula: str
    grupo: str           # dados[grupo][chave]
    chave: str
    minimo: int | None


# Só alimentam a aba BD.LIVE da planilha: mudam direto em `dados`, sem recálculo
RECURSOS = {
    "pv": Recurso("PV", "PJ!S14", "pv", "atual", None),            # PV negativo existe em T20
    "pm": Recurso("PM", "PJ!AA14", "pm", "atual", None),
    "pv_temp": Recurso("PV temporário", "PJ!X14", "pv", "temporario", 0),
}
ACOMPANHAM_MAXIMO = ("pv", "pm")


def _numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _gravar_recurso(f, ent, r, valor):
    f[r.grupo][r.chave] = valor
    if ent is not None:
        if valor == 0:
            ent.pop(r.celula, None)  # 0 (ou vazio) é o valor do modelo
        else:
            ent[r.celula] = valor


def encher_recursos(f, ent):
    """Ficha nova: PV e PM atuais começam no máximo."""
    for chave in ACOMPANHAM_MAXIMO:
        r = RECURSOS[chave]
        if _numero(f[r.grupo].get("max")):
            _gravar_recurso(f, ent, r, f[r.grupo]["max"])


def _acompanhar_maximo(antes, lidas, f, novas):
    """Depois de recalcular: quem estava cheio fica cheio; acima do novo máximo desce até ele."""
    for chave in ACOMPANHAM_MAXIMO:
        r = RECURSOS[chave]
        if novas.get(r.celula, 0) != lidas.get(r.celula, 0):
            continue  # o próprio envio mudou o valor atual
        atual, maximo, novo_max = antes[r.grupo].get("atual"), antes[r.grupo].get("max"), f[r.grupo].get("max")
        if _numero(atual) and _numero(maximo) and _numero(novo_max):
            _gravar_recurso(f, novas, r, novo_max if atual == maximo else min(atual, novo_max))


def atualizar_ficha(ficha_id, alterar, calcular, acompanhar_recursos=False, tentativas=3):
    """Lê as entradas, aplica `alterar(entradas) -> novas`, recalcula e grava entradas + dados.

    Com `acompanhar_recursos` (o editor), PV e PM atuais seguem o novo máximo.
    O recálculo (cerca de 2 s) roda fora da trava de escrita; dentro do BEGIN IMMEDIATE a
    ficha é lida de novo e, se alguém mudou as entradas nesse meio tempo (PV rápido,
    início de turno), a alteração é refeita sobre a versão nova.
    """
    for _ in range(tentativas):
        with _conectar() as con:
            l = con.execute("SELECT dados, entradas FROM fichas WHERE id = ?", (ficha_id,)).fetchone()
        antes, lidas = json.loads(l["dados"]), json.loads(l["entradas"] or "{}")
        novas = alterar(dict(lidas))
        f = calcular(novas)
        if acompanhar_recursos:
            _acompanhar_maximo(antes, lidas, f, novas)
        with _conectar() as con:
            con.execute("BEGIN IMMEDIATE")
            if (_entradas(con, ficha_id) or {}) != lidas:
                continue
            con.execute("UPDATE fichas SET nome = ?, raca = ?, classe = ?, nivel = ?, dados = ?, entradas = ?, "
                        "atualizada_em = CURRENT_TIMESTAMP WHERE id = ?",
                        (f["nome"], f["raca"], f["classes"][0]["classe"], f["nivel"], _json(f), _json(novas),
                         ficha_id))
            return f
    raise RuntimeError("A ficha foi alterada ao mesmo tempo por outra pessoa. Tente salvar de novo.")


def definir_recurso(ficha_id, recurso, valor=None, delta=0):
    """PV, PM ou PV temporário: grava a entrada e `dados` direto, sem recalcular a planilha."""
    r = RECURSOS[recurso]
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        l = con.execute("SELECT dados, entradas FROM fichas WHERE id = ?", (ficha_id,)).fetchone()
        f = json.loads(l["dados"])
        atual = f[r.grupo].get(r.chave)
        novo = valor if valor is not None else (atual if _numero(atual) else 0) + delta
        if r.minimo is not None:
            novo = max(novo, r.minimo)
        ent = json.loads(l["entradas"]) if l["entradas"] is not None else None
        _gravar_recurso(f, ent, r, novo)
        con.execute("UPDATE fichas SET dados = ?, entradas = ?, atualizada_em = CURRENT_TIMESTAMP WHERE id = ?",
                    (_json(f), None if ent is None else _json(ent), ficha_id))
    return novo


def excluir_ficha(ficha_id):
    """Apaga a ficha e o estado dela numa transação. As rolagens ficam: guardam o nome do personagem."""
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        for tabela in ("ficha_condicoes", "ficha_condicoes_itens", "ficha_turnos"):
            con.execute(f"DELETE FROM {tabela} WHERE ficha_id = ?", (ficha_id,))
        return con.execute("DELETE FROM fichas WHERE id = ?", (ficha_id,)).rowcount


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
        if r.pv is not None:  # entradas também, senão o próximo recálculo desfaz o dano
            con.execute("UPDATE fichas SET dados = json_set(dados, '$.pv.atual', ?), "
                        "entradas = CASE WHEN entradas IS NULL THEN NULL "
                        "ELSE json_set(entradas, '$.\"PJ!S14\"', ?) END WHERE id = ?", (r.pv, r.pv, ficha_id))
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
