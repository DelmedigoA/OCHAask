from __future__ import annotations

from typing import Any

from ochask.config import Settings
from ochask.web import workspace


def settings(tmp_path) -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_secret_key="sb_secret_test",
        embedding_model="text-embedding-3-small",
        workspace_db_path=str(tmp_path / "workspace.sqlite3"),
    )


def test_conversation_round_trip_and_delete(tmp_path):
    config = settings(tmp_path)
    conversation_id = workspace.create_conversation()
    messages: list[dict[str, Any]] = [{"id": "m1", "role": "user", "content": []}]

    workspace.save_conversation(config, conversation_id, "Hospital conditions", messages)

    assert workspace.list_conversations(config)[0]["title"] == "Hospital conditions"
    assert workspace.get_conversation(config, conversation_id)["messages"] == messages
    workspace.delete_conversation(config, conversation_id)
    assert workspace.get_conversation(config, conversation_id) is None
