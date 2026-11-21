"""Comandos de terminal para criar usuários e trocar senhas."""
import secrets

import click
from werkzeug.security import generate_password_hash

import db


def registrar(app):
    @app.cli.command("criar-usuarios")
    @click.option("--jogadores", default="", help='Nomes dos 5 jogadores, separados por vírgula. Ex.: "Ana,Bruno,Carla,Davi,Eva"')
    def criar_usuarios(jogadores):
        """Cria o mestre e os 5 jogadores. As senhas aparecem só agora; anote."""
        nomes = [n.strip() for n in jogadores.split(",") if n.strip()] or [f"Jogador {i}" for i in range(1, 6)]
        if len(nomes) != 5:
            raise click.UsageError("Informe exatamente 5 nomes de jogadores.")
        contas = [("mestre", "Mestre", "mestre")] + [(f"jogador{i}", n, "jogador") for i, n in enumerate(nomes, 1)]
        for login, nome, papel in contas:
            if db.usuario(login=login):
                click.echo(f"{login:10} já existe, mantido.")
                continue
            senha = secrets.token_urlsafe(8)
            db.criar_usuario(login, nome, papel, generate_password_hash(senha))
            click.echo(f"{login:10} senha: {senha}")

    @app.cli.command("definir-senha")
    @click.argument("login")
    def definir_senha(login):
        """Troca a senha de um usuário (ex.: flask --app app definir-senha jogador3)."""
        senha = click.prompt("Nova senha", hide_input=True, confirmation_prompt=True)
        if len(senha) < 6:
            raise click.UsageError("Use pelo menos 6 caracteres.")
        if not db.definir_senha(login.lower(), generate_password_hash(senha)):
            raise click.UsageError(f"Usuário '{login}' não existe.")
        click.echo("Senha alterada.")
