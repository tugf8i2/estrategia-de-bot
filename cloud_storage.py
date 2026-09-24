"""Persistencia opcional de archivos pequeños en PostgreSQL para la nube."""

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_READY = False


def _url():
    return os.environ.get("DATABASE_URL", "").strip()


def _connect():
    import psycopg
    return psycopg.connect(_url(), autocommit=True)


def _ensure_table():
    global _READY
    if _READY or not _url():
        return
    with _LOCK:
        if _READY:
            return
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS bot_files ("
                "name TEXT PRIMARY KEY, content BYTEA NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
        _READY = True


def restore_file(name: str, destination: str, seed: str | None = None):
    """Restaura un archivo desde PostgreSQL o carga una semilla la primera vez."""
    if not _url():
        return
    _ensure_table()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT content FROM bot_files WHERE name = %s", (name,))
        row = cur.fetchone()
    if row:
        target.write_bytes(bytes(row[0]))
    elif seed and Path(seed).exists():
        target.write_bytes(Path(seed).read_bytes())
        persist_file(name, destination)


def persist_file(name: str, source: str):
    """Guarda la versión completa de un archivo pequeño de forma atómica."""
    if not _url() or not Path(source).exists():
        return
    _ensure_table()
    payload = Path(source).read_bytes()
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_files(name, content, updated_at) VALUES (%s, %s, NOW()) "
            "ON CONFLICT(name) DO UPDATE SET content = EXCLUDED.content, updated_at = NOW()",
            (name, payload),
        )
