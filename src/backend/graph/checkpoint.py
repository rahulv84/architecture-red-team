"""File-backed SQLite LangGraph checkpointer (embedded, no server).

This is intentionally separate from the primary in-memory application database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def create_checkpointer(db_path: Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver, conn


def has_checkpoint(saver: SqliteSaver, thread_id: str) -> bool:
    """True when at least one checkpoint exists for ``thread_id``."""
    return saver.get_tuple({"configurable": {"thread_id": thread_id}}) is not None
