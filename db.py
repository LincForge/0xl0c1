"""Multi-tenant storage layer (SQLite locally; Cloud SQL PostgreSQL 16 with RLS in production)."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import string
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from auth import sha256_hex

HERE = Path(__file__).parent
DEFAULT_SPACE_ID = "default"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_token(length: int = 24) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def default_path_token() -> str:
    if tok := os.environ.get("LOCI_PATH_TOKEN"):
        return tok.strip()
    token_file = HERE / ".loci-token"
    if not token_file.exists():
        token_file.write_text(new_token(24))
    return token_file.read_text().strip()


def backend() -> str:
    return (
        "postgres"
        if (os.environ.get("LOCI_DATABASE_URL") or os.environ.get("LOCI_DB_HOST"))
        else "sqlite"
    )


def _pg_dsn() -> str:
    if url := os.environ.get("LOCI_DATABASE_URL"):
        return url
    secret = json.loads(os.environ["LOCI_DB_SECRET"])
    return (
        f"postgresql://{secret['username']}:{secret['password']}@{os.environ['LOCI_DB_HOST']}:"
        f"{os.environ.get('LOCI_DB_PORT', '5432')}/{os.environ.get('LOCI_DB_NAME', 'loci')}"
    )


def connect() -> sqlite3.Connection | psycopg.Connection[dict[str, Any]]:
    if backend() == "postgres":
        return psycopg.connect(
            _pg_dsn(), row_factory=dict_row, autocommit=True, connect_timeout=5
        )
    conn = sqlite3.connect(os.environ.get("LOCI_DB", str(HERE / "loci.db")))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q(sql: str) -> str:
    """Rewrite ? placeholders for the active backend (sqlite keeps ?, psycopg wants %s)."""
    return sql if backend() == "sqlite" else sql.replace("?", "%s")


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


@contextmanager
def tx(
    space_id: str | None = None,
) -> Iterator[sqlite3.Connection | psycopg.Connection[dict[str, Any]]]:
    """Execute one database transaction. On Postgres, sets loci.current_space_id for RLS."""
    conn = connect()
    try:
        if isinstance(conn, psycopg.Connection):
            with conn.transaction():
                if space_id:
                    conn.execute(
                        "SELECT set_config('loci.current_space_id', %s, true)",
                        (space_id,),
                    )
                yield conn
        else:
            with conn:
                yield conn
    finally:
        conn.close()


def _migrate_legacy_postgres(conn: psycopg.Connection[dict[str, Any]]) -> None:
    """Idempotently upgrade a 4-table hackathon Postgres schema to the 6-table RLS schema."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS space (
            id                    TEXT PRIMARY KEY,
            slug                  TEXT NOT NULL UNIQUE,
            name                  TEXT NOT NULL,
            tier                  TEXT NOT NULL DEFAULT 'free',
            capability_token      TEXT NOT NULL UNIQUE,
            capability_token_hash TEXT NOT NULL UNIQUE,
            tag_prefix            TEXT NOT NULL DEFAULT '',
            created_at            TEXT NOT NULL
        );
        """
    )
    tok = default_path_token()
    conn.execute(
        """
        INSERT INTO space (id, slug, name, tier, capability_token, capability_token_hash, tag_prefix, created_at)
        VALUES (%s, %s, %s, 'pro', %s, %s, 'LC', %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            DEFAULT_SPACE_ID,
            "default",
            "Default Loci Space",
            tok,
            sha256_hex(tok),
            now_iso(),
        ),
    )
    for table in ("place", "object", "lesson", "claim"):
        conn.execute(
            f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS space_id TEXT NOT NULL DEFAULT 'default'"
        )
    conn.execute(
        "ALTER TABLE IF EXISTS object ADD COLUMN IF NOT EXISTS canonical_class TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "ALTER TABLE IF EXISTS object ADD COLUMN IF NOT EXISTS material TEXT NOT NULL DEFAULT 'composite_or_other'"
    )
    conn.execute(
        "ALTER TABLE IF EXISTS object ADD COLUMN IF NOT EXISTS mounting TEXT NOT NULL DEFAULT 'handheld_portable'"
    )
    conn.execute(
        "ALTER TABLE IF EXISTS object ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'deterministic-64d'"
    )
    conn.execute(
        "ALTER TABLE IF EXISTS object ADD COLUMN IF NOT EXISTS embedding_json TEXT NOT NULL DEFAULT '[]'"
    )
    conn.execute(
        "ALTER TABLE IF EXISTS lesson ADD COLUMN IF NOT EXISTS author_subject TEXT NOT NULL DEFAULT 'anonymous'"
    )
    conn.execute("ALTER TABLE IF EXISTS place DROP CONSTRAINT IF EXISTS place_path_key")
    conn.execute(
        "ALTER TABLE IF EXISTS object DROP CONSTRAINT IF EXISTS object_tag_code_key"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_place_space_id ON place(space_id, id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_place_space_path ON place(space_id, path)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_object_space_id ON object(space_id, id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_space_id ON lesson(space_id, id)"
    )


def _migrate_legacy_sqlite(conn: sqlite3.Connection) -> None:
    """If an existing local loci.db has a 4-table schema without space_id, upgrade it in place."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='object'"
    ).fetchone()
    if cur is None:
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(object)").fetchall()}
    if "space_id" in cols and "embedding_model" in cols:
        return
    for table in ("place", "object", "lesson", "claim"):
        existing_cols = {
            str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if existing_cols and "space_id" not in existing_cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN space_id TEXT NOT NULL DEFAULT 'default'"
            )
    obj_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(object)").fetchall()}
    for col, default in (
        ("canonical_class", "''"),
        ("material", "'composite_or_other'"),
        ("mounting", "'handheld_portable'"),
        ("embedding_model", "'deterministic-64d'"),
        ("embedding_json", "'[]'"),
    ):
        if col not in obj_cols:
            conn.execute(
                f"ALTER TABLE object ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
            )
    lesson_cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(lesson)").fetchall()
    }
    if "author_subject" not in lesson_cols:
        conn.execute(
            "ALTER TABLE lesson ADD COLUMN author_subject TEXT NOT NULL DEFAULT 'anonymous'"
        )


def ensure_default_space(default_token: str | None = None) -> dict[str, Any]:
    tok = (default_token or default_path_token()).strip()
    tok_hash = sha256_hex(tok)
    now = now_iso()
    with tx() as conn:
        existing = conn.execute(
            q("SELECT * FROM space WHERE id = ?"), (DEFAULT_SPACE_ID,)
        ).fetchone()
        if existing is None:
            conn.execute(
                q(
                    "INSERT INTO space (id, slug, name, tier, capability_token, capability_token_hash, tag_prefix, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    DEFAULT_SPACE_ID,
                    "default",
                    "Default Loci Space",
                    "pro",
                    tok,
                    tok_hash,
                    "LC",
                    now,
                ),
            )
        else:
            row = _row_dict(existing)
            if row.get("capability_token") != tok:
                conn.execute(
                    q(
                        "UPDATE space SET capability_token = ?, capability_token_hash = ? WHERE id = ?"
                    ),
                    (tok, tok_hash, DEFAULT_SPACE_ID),
                )
        out = conn.execute(
            q("SELECT * FROM space WHERE id = ?"), (DEFAULT_SPACE_ID,)
        ).fetchone()
        return _row_dict(out)


_PG_DDL_INITIALIZED: set[str] = set()


def init_db(default_token: str | None = None) -> None:
    if backend() == "postgres":
        pg_key = _pg_dsn()
        if pg_key not in _PG_DDL_INITIALIZED:
            with connect() as conn:
                assert isinstance(conn, psycopg.Connection)
                _migrate_legacy_postgres(conn)
                conn.execute((HERE / "schema.postgres.sql").read_text())
            _PG_DDL_INITIALIZED.add(pg_key)
    else:
        db_path = os.environ.get("LOCI_DB", str(HERE / "loci.db"))
        with sqlite3.connect(db_path) as conn:
            _migrate_legacy_sqlite(conn)
            conn.executescript((HERE / "schema.sql").read_text())
    ensure_default_space(default_token)


def _slugify(value: str) -> str:
    base = _SLUG_RE.sub("-", value.casefold()).strip("-")
    return base or f"space-{secrets.token_hex(3)}"


def create_space(
    *,
    name: str,
    slug: str | None = None,
    tier: str = "free",
    tag_prefix: str = "",
    capability_token: str | None = None,
    owner_subject: str = "owner@lincinnovations.com",
) -> dict[str, Any]:
    init_db()
    space_id = f"sp_{uuid4().hex[:12]}"
    clean_slug = _slugify(slug or name)
    tok = (capability_token or new_token(24)).strip()
    tok_hash = sha256_hex(tok)
    now = now_iso()
    with tx() as conn:
        if conn.execute(
            q("SELECT 1 FROM space WHERE slug = ?"), (clean_slug,)
        ).fetchone():
            clean_slug = f"{clean_slug}-{secrets.token_hex(2)}"
        conn.execute(
            q(
                "INSERT INTO space (id, slug, name, tier, capability_token, capability_token_hash, tag_prefix, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                space_id,
                clean_slug,
                name.strip() or clean_slug,
                tier,
                tok,
                tok_hash,
                tag_prefix.upper()[:2],
                now,
            ),
        )
        conn.execute(
            q(
                "INSERT INTO membership (id, space_id, subject, role, token_hash, created_at) "
                "VALUES (?, ?, ?, 'owner', ?, ?)"
            ),
            (f"mem_{uuid4().hex[:12]}", space_id, owner_subject, tok_hash, now),
        )
    return {
        "id": space_id,
        "space_id": space_id,
        "slug": clean_slug,
        "name": name.strip() or clean_slug,
        "tier": tier,
        "capability_token": tok,
        "capability_token_hash": tok_hash,
        "tag_prefix": tag_prefix.upper()[:2],
        "created_at": now,
    }


def get_space_by_token(capability_token: str) -> dict[str, Any] | None:
    tok = capability_token.strip()
    if not tok:
        return None
    tok_hash = sha256_hex(tok)
    with tx() as conn:
        row = conn.execute(
            q(
                "SELECT * FROM space WHERE capability_token_hash = ? OR capability_token = ?"
            ),
            (tok_hash, tok),
        ).fetchone()
        return _row_dict(row) if row is not None else None


def get_space_by_id(space_id: str) -> dict[str, Any] | None:
    with tx() as conn:
        row = conn.execute(
            q("SELECT * FROM space WHERE id = ?"), (space_id,)
        ).fetchone()
        return _row_dict(row) if row is not None else None


def list_all_spaces() -> list[dict[str, Any]]:
    with tx() as conn:
        rows = conn.execute(
            q(
                "SELECT id, slug, name, tier, tag_prefix, created_at FROM space ORDER BY created_at ASC"
            )
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = _row_dict(r)
            sid = str(d["id"])
            if isinstance(conn, psycopg.Connection):
                conn.execute(
                    "SELECT set_config('loci.current_space_id', %s, true)", (sid,)
                )
            obj_cnt = conn.execute(
                q("SELECT COUNT(*) AS cnt FROM object WHERE space_id = ?"), (sid,)
            ).fetchone()
            les_cnt = conn.execute(
                q("SELECT COUNT(*) AS cnt FROM lesson WHERE space_id = ?"), (sid,)
            ).fetchone()
            d["object_count"] = int(_row_dict(obj_cnt)["cnt"]) if obj_cnt else 0
            d["lesson_count"] = int(_row_dict(les_cnt)["cnt"]) if les_cnt else 0
            out.append(d)
        return out


def ping() -> dict[str, str]:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"db": "ok"}
    except Exception as e:  # noqa: BLE001 — health must never raise
        return {"db": "error", "detail": str(e)[:200]}
