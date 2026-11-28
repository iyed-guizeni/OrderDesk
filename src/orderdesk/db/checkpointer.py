"""Postgres-backed LangGraph checkpoint setup and connections."""

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row


def open_checkpointer(database_url: str) -> PostgresSaver:
    """Open a PostgresSaver on its own long-lived, autocommit connection."""
    conn = psycopg.connect(
        database_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    return PostgresSaver(conn)


def setup_checkpointer(database_url: str) -> None:
    """Create LangGraph's Postgres checkpoint tables; safe to call repeatedly."""
    saver = open_checkpointer(database_url)
    try:
        saver.setup()
    finally:
        saver.conn.close()
