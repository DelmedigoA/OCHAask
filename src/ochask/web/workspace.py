"""Local conversation persistence for the chat interface."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ochask.config import Settings


def connect(settings: Settings) -> sqlite3.Connection:
    """Open the local chat database and create its conversation table."""
    path = Path(settings.workspace_db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            messages TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    db.commit()
    return db


def list_conversations(settings: Settings) -> list[dict[str, Any]]:
    with connect(settings) as db:
        rows = db.execute(
            "SELECT id,title,created_at,updated_at FROM conversations "
            "ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(
    settings: Settings, conversation_id: str
) -> dict[str, Any] | None:
    UUID(conversation_id)
    with connect(settings) as db:
        row = db.execute(
            "SELECT id,title,messages,created_at,updated_at FROM conversations "
            "WHERE id=?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["messages"] = json.loads(result["messages"])
    return result


def save_conversation(
    settings: Settings,
    conversation_id: str,
    title: str,
    messages: list[dict[str, Any]],
) -> None:
    UUID(conversation_id)
    title = title.strip()[:160] or "New research chat"
    stamp = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """INSERT INTO conversations(id,title,messages,created_at,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               title=excluded.title,messages=excluded.messages,
               updated_at=excluded.updated_at""",
            (
                conversation_id,
                title,
                json.dumps(messages, ensure_ascii=False),
                stamp,
                stamp,
            ),
        )


def delete_conversation(settings: Settings, conversation_id: str) -> None:
    UUID(conversation_id)
    with connect(settings) as db:
        db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))


def create_conversation() -> str:
    return str(uuid4())
