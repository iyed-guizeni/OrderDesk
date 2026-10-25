import psycopg


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, autocommit=True, connect_timeout=3)