"""Dados de dano para o painel do mestre e para o Tableau, lidos direto do banco (sem scraping)."""
import json

import pandas as pd

import db

# Mesmas colunas e tipos do pipeline anterior, para o workbook do Tableau continuar funcionando.
COLUNAS = ["data_hora", "jogador", "origem", "alvo", "d20", "rolagem_ataque",
           "dano", "critico", "dano_critico", "mensagem_bruta"]


def df_danos():
    """Um registro por ataque que causou dano. Ataque que falhou por camuflagem não tem dano e fica de fora."""
    linhas = []
    for m in db.todos_ataques():
        dn = m.get("dano")
        if not dn:
            continue
        crit = bool(m.get("critico"))
        texto = f"{dn['total']} (crítico: {dn['total_critico']})" if crit else str(dn["total"])
        linhas.append({
            "data_hora": m["hora"], "jogador": m.get("jogador") or "", "origem": m["ficha"], "alvo": m["nome"],
            "d20": m["natural"], "rolagem_ataque": m["total"], "dano": dn["total"], "critico": crit,
            "dano_critico": dn.get("total_critico") if crit else None,
            "mensagem_bruta": f"Ataque com {m['nome']} | Dano: {texto}",
        })
    df = pd.DataFrame(linhas, columns=COLUNAS)
    df["dano_critico"] = df["dano_critico"].astype("Int64")  # inteiro que aceita vazio
    return df


def resumo(df):
    """Números do painel. Os rankings são listas [nome, dano] para manter a ordem do maior ao menor."""
    if df.empty:
        return {"total_dano": 0, "ataques": 0, "criticos": 0, "media": 0, "por_origem": [], "por_alvo": []}

    def ranking(coluna):
        return [[nome, int(v)] for nome, v in df.groupby(coluna)["dano"].sum().sort_values(ascending=False).items()]

    return {"total_dano": int(df["dano"].sum()), "ataques": len(df), "criticos": int(df["critico"].sum()),
            "media": round(float(df["dano"].mean()), 1), "por_origem": ranking("origem"), "por_alvo": ranking("alvo")}


def recentes(df, n=8):
    """Os n ataques mais recentes, do mais novo para o mais antigo."""
    sub = df.tail(n).iloc[::-1][["data_hora", "jogador", "origem", "alvo", "dano", "critico", "dano_critico"]]
    return json.loads(sub.to_json(orient="records", force_ascii=False))
