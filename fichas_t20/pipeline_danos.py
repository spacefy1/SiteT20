"""Gera os arquivos para o Tableau (Excel, CSV, SQLite e .hyper) direto do banco do site.

Sem login, sem scraping e sem senha: lê as rolagens do fichas.db pelo mesmo código do painel do mestre.
Rode no computador onde o site está instalado (apenas o mestre tem acesso a ele):
  python pipeline_danos.py            gera os arquivos uma vez
  python pipeline_danos.py --vigiar   fica rodando e regenera tudo a cada rolagem nova
"""
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

import analytics
import db

PASTA_PROJETO = Path(__file__).resolve().parent
DB_ANALYTICS = PASTA_PROJETO / "analytics_danos.db"
ARQUIVO_EXCEL = PASTA_PROJETO / "danos_t20.xlsx"
ARQUIVO_CSV = PASTA_PROJETO / "danos_t20.csv"
ARQUIVO_HYPER = PASTA_PROJETO / "danos_t20.hyper"


def salvar_banco_e_planilha(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if not df.empty:
        df["dano"] = df["dano"].astype(int)

    df.to_excel(ARQUIVO_EXCEL, index=False, engine="openpyxl")
    df.to_csv(ARQUIVO_CSV, index=False, encoding="utf-8-sig")

    with sqlite3.connect(DB_ANALYTICS) as conn:
        df.to_sql("danos_combate", conn, if_exists="replace", index=False)

    return df


def exportar_para_hyper(df: pd.DataFrame):
    try:
        from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, NOT_NULLABLE, NULLABLE,
                                     SqlType, TableDefinition, TableName, Telemetry)
    except ImportError:
        print(f"tableauhyperapi não instalado: .hyper não gerado (pip install tableauhyperapi). "
              f"{ARQUIVO_EXCEL.name} e {ARQUIVO_CSV.name} foram atualizados ({len(df)} danos).")
        return

    tabela_danos = TableDefinition(
        table_name=TableName("Extract", "DanosT20"),
        columns=[
            TableDefinition.Column("data_hora", SqlType.text(), NOT_NULLABLE),
            TableDefinition.Column("jogador", SqlType.text(), NULLABLE),
            TableDefinition.Column("origem", SqlType.text(), NOT_NULLABLE),
            TableDefinition.Column("alvo", SqlType.text(), NOT_NULLABLE),
            TableDefinition.Column("d20", SqlType.big_int(), NULLABLE),
            TableDefinition.Column("rolagem_ataque", SqlType.big_int(), NULLABLE),
            TableDefinition.Column("dano", SqlType.big_int(), NOT_NULLABLE),
            TableDefinition.Column("critico", SqlType.bool(), NOT_NULLABLE),
            TableDefinition.Column("dano_critico", SqlType.big_int(), NULLABLE),
            TableDefinition.Column("mensagem_bruta", SqlType.text(), NULLABLE),
        ],
    )

    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(
            endpoint=hyper.endpoint,
            database=str(ARQUIVO_HYPER),
            create_mode=CreateMode.CREATE_AND_REPLACE,
        ) as connection:
            connection.catalog.create_schema_if_not_exists("Extract")
            connection.catalog.create_table(tabela_danos)

            if not df.empty:
                linhas = [
                    (
                        str(row["data_hora"]),
                        str(row["jogador"]) if pd.notna(row["jogador"]) else None,
                        str(row["origem"]),
                        str(row["alvo"]),
                        int(row["d20"]) if pd.notna(row["d20"]) else None,
                        int(row["rolagem_ataque"]) if pd.notna(row["rolagem_ataque"]) else None,
                        int(row["dano"]),
                        bool(row["critico"]),
                        int(row["dano_critico"]) if pd.notna(row["dano_critico"]) else None,
                        str(row["mensagem_bruta"]),
                    )
                    for _, row in df.iterrows()
                ]
                with Inserter(connection, tabela_danos) as inserter:
                    inserter.add_rows(linhas)
                    inserter.execute()

    print(f"Atualizado: {DB_ANALYTICS.name}, {ARQUIVO_EXCEL.name} e {ARQUIVO_HYPER.name} ({len(df)} danos)")


def executar_pipeline():
    df = salvar_banco_e_planilha(analytics.df_danos())
    exportar_para_hyper(df)
    return len(df)


def vigiar(intervalo=3):
    """Regenera os arquivos sempre que entra uma rolagem nova no banco."""
    ultimo = None
    print(f"Vigiando {db.CAMINHO.name} a cada {intervalo}s. Ctrl+C para parar.")
    while True:
        atual = db.ultimo_id_rolagem()
        if atual != ultimo:
            try:
                executar_pipeline()
                ultimo = atual
            except Exception as e:  # arquivo aberto no Excel ou no Tableau, por exemplo
                print(f"Não consegui atualizar agora ({e}). Tento de novo em instantes.")
        time.sleep(intervalo)


if __name__ == "__main__":
    vigiar() if "--vigiar" in sys.argv else executar_pipeline()
