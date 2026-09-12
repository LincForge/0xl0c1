"""Storage selection. SQLite by default (laptop); Postgres when App Runner injects the RDS env.

Plumbing only: connect, apply the brought schema, answer a health ping. No tool logic lives here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).parent


def backend() -> str:
    return (
        "postgres"
        if (os.environ.get("LOCI_DATABASE_URL") or os.environ.get("LOCI_DB_HOST"))
        else "sqlite"
    )


def _pg_dsn() -> str:
    if url := os.environ.get("LOCI_DATABASE_URL"):
        return url
    secret = json.loads(
        os.environ["LOCI_DB_SECRET"]
    )  # RDS-managed master secret: {"username","password"}
    return (
        f"postgresql://{secret['username']}:{secret['password']}@{os.environ['LOCI_DB_HOST']}:"
        f"{os.environ.get('LOCI_DB_PORT', '5432')}/{os.environ.get('LOCI_DB_NAME', 'loci')}"
    )


def connect() -> sqlite3.Connection | psycopg.Connection[dict[str, object]]:
    if backend() == "postgres":
        return psycopg.connect(
            _pg_dsn(), row_factory=dict_row, autocommit=True, connect_timeout=5
        )
    conn = sqlite3.connect(os.environ.get("LOCI_DB", HERE / "loci.db"))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    if backend() == "postgres":
        with connect() as conn:
            conn.execute((HERE / "schema.postgres.sql").read_text())
    else:
        with sqlite3.connect(os.environ.get("LOCI_DB", HERE / "loci.db")) as conn:
            conn.executescript((HERE / "schema.sql").read_text())


def q(sql: str) -> str:
    """Rewrite ? placeholders for the active backend (sqlite keeps ?, psycopg wants %s)."""
    return sql if backend() == "sqlite" else sql.replace("?", "%s")


@contextmanager
def tx() -> Iterator[sqlite3.Connection | psycopg.Connection[dict[str, object]]]:
    """One transaction: psycopg connects autocommit, sqlite3 does not, so wrap both the same way."""
    conn = connect()
    try:
        if isinstance(conn, psycopg.Connection):
            with conn.transaction():
                yield conn
        else:
            with conn:  # commits on success, rolls back on exception
                yield conn
    finally:
        conn.close()


def ping() -> dict[str, str]:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"db": "ok"}
    except Exception as e:  # noqa: BLE001 — health must never raise
        return {"db": "error", "detail": str(e)[:200]}
