"""Comandos de terminal: usuários e senhas, compilação do modelo e recálculo das fichas."""
import secrets

import click
from werkzeug.security import generate_password_hash

import db


def _numeros(f):
    """Os números da ficha que valem conferir numa troca de modelo, com um rótulo legível."""
    n = {"nível": f.get("nivel"), "PV máx": f["pv"].get("max"), "PM máx": f["pm"].get("max"),
         "Defesa": f.get("defesa"), "Deslocamento": f.get("deslocamento"), "CD de magia": f.get("cd_magia"),
         "Treinamento": f.get("bonus_treinamento"), "Carga máx": (f.get("carga") or {}).get("max"),
         "Carga": (f.get("carga") or {}).get("atual")}
    n.update({s: a.get("mod") for s, a in f["atributos"].items()})
    n.update({p["nome"]: f"{p.get('total')}{' (treinada)' if p.get('treinada') else ''}" for p in f["pericias"]})
    n.update({f"Ataque {a['nome']}": f"{a.get('bonus')} / {a.get('dano')}" for a in f["ataques"]})
    return n


def _diferencas(antes, depois):
    a, d = _numeros(antes), _numeros(depois)
    return [f"{k}: {a.get(k, '—')} → {d.get(k, '—')}" for k in dict.fromkeys([*a, *d]) if a.get(k) != d.get(k)]


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

    @app.cli.command("preparar-modelo")
    @click.option("--forcar", is_flag=True, help="Recompila mesmo que o cache já exista.")
    def preparar_modelo(forcar):
        """Compila a planilha modelo para o recálculo das fichas (cerca de 30 s, uma vez só)."""
        import motor
        click.echo("Compilando o modelo, aguarde...")
        click.echo(f"Cache pronto: {motor.preparar(forcar)}")

    @app.cli.command("recalcular-fichas")
    def recalcular_fichas():
        """Recalcula `dados` de todas as fichas com edição liberada, a partir das entradas.

        Serve depois de mudar o leitor ou o modelo. Faz um backup do banco antes, mostra o que
        mudou nos números de cada ficha e tira entradas que saíram do catálogo. PV, PM e PV
        temporário atuais ficam como estão.
        """
        import campos
        import motor
        click.echo(f"Backup do banco: {db.backup()}")
        ids = db.fichas_editaveis()
        mudaram, revisar = 0, []
        for ficha_id in ids:
            antes, removidas = db.obter(ficha_id)[0], []

            def calcular(entradas, antes=antes):
                f = motor.calcular(entradas)
                for r in db.RECURSOS.values():
                    if r.chave in antes[r.grupo]:
                        f[r.grupo][r.chave] = antes[r.grupo][r.chave]
                return f

            def no_catalogo(entradas, removidas=removidas):
                removidas[:] = [e for e in entradas if e not in campos.CAMPOS]
                return {e: v for e, v in entradas.items() if e in campos.CAMPOS}

            depois = db.atualizar_ficha(ficha_id, no_catalogo, calcular)
            mudaram += depois != antes
            diferencas = _diferencas(antes, depois)
            click.echo(f"#{ficha_id} {depois['nome']} ({depois['raca'] or 'sem raça'}): "
                       + ("; ".join(diferencas) if diferencas else "números iguais")
                       + (f" | entradas fora do catálogo removidas: {', '.join(removidas)}" if removidas else ""))
            ajuste = {e: v for e, v in (db.entradas(ficha_id) or {}).items() if e in campos.AJUSTE_RACIAL and v}
            if ajuste and any(e.get("rotulo") for e in depois["raca_detalhe"]["escolhas"]):
                revisar.append(f"#{ficha_id} {depois['nome']} ({depois['raca']}): "
                               + ", ".join(f"{e}={v}" for e, v in ajuste.items()))
        click.echo(f"{len(ids)} ficha(s) recalculada(s), {mudaram} com dados diferentes. "
                   "Fichas antigas (sem edição liberada) ficaram de fora.")
        if revisar:
            click.echo("Revise à mão (Ajustes!B6:G6 preenchido numa raça com escolhas na aba Raça):")
            for linha in revisar:
                click.echo(f"  {linha}")

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
