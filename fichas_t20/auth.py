"""Login por sessão, papéis (jogador/mestre) e proteção CSRF."""
import hmac
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import abort, g, redirect, request, session, url_for

import db


def _chave_secreta():
    """Usa SECRET_KEY do ambiente ou cria um secret.key local na primeira execução."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    arq = Path(__file__).with_name("secret.key")
    if not arq.exists():
        arq.write_text(secrets.token_hex(32))
        try:
            arq.chmod(0o600)
        except OSError:
            pass
    return arq.read_text().strip()


def usuario_atual():
    if "usuario" not in g:
        g.usuario = db.usuario(id=session["uid"]) if session.get("uid") else None
    return g.usuario


def login_obrigatorio(f):
    @wraps(f)
    def interno(*a, **k):
        if not usuario_atual():
            if request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return redirect(url_for("login", proximo=request.path))
        return f(*a, **k)
    return interno


def mestre_obrigatorio(f):
    @login_obrigatorio
    @wraps(f)
    def interno(*a, **k):
        if usuario_atual()["papel"] != "mestre":
            abort(403)
        return f(*a, **k)
    return interno


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def init_app(app):
    app.secret_key = _chave_secreta()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=bool(os.environ.get("COOKIE_SEGURO")))
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def checar_csrf():
        if request.method == "POST":
            token = session.get("csrf")
            if not token or not hmac.compare_digest(request.form.get("csrf", ""), token):
                return "Sessão expirada. Recarregue a página e tente de novo.", 400

    @app.context_processor
    def contexto():
        return {"usuario": usuario_atual()}
