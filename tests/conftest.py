"""Shared fixtures. `loci_db` gives every test a fresh SQLite file and a row-count helper."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class LociDb:
    def __init__(self, path: str) -> None:
        self.path = path

    def counts(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as conn:
            return {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("place", "object", "lesson", "claim")
            }

    def rows(self, sql: str, *params: object) -> list[sqlite3.Row]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            return list(conn.execute(sql, params))


@pytest.fixture
def loci_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[LociDb]:
    for k in ("LOCI_DATABASE_URL", "LOCI_DB_HOST"):
        monkeypatch.delenv(k, raising=False)
    path = str(tmp_path / "loci.db")
    monkeypatch.setenv("LOCI_DB", path)
    monkeypatch.setenv("LOCI_PATH_TOKEN", "testtok")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import db

    db.init_db()
    yield LociDb(path)


@pytest.fixture
def server_mod(loci_db: LociDb) -> object:
    """The server module, imported after the DB env is set."""
    import server

    return server


__all__ = ["LociDb", "Callable", "os"]
