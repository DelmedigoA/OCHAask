"""Environment configuration shared by indexing and search."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Server-side settings; service credentials must never reach browser clients."""

    supabase_url: str
    supabase_service_role_key: str
    embedding_model: str

    @classmethod
    def from_environment(cls) -> Settings:
        """Load the optional .env file and check the required configuration."""
        load_dotenv()
        required = (
            "OPENAI_API_KEY",
            "OPENAI_EMBEDDING_MODEL",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
        )
        values = {name: os.getenv(name, "").strip() for name in required}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return cls(
            supabase_url=values["SUPABASE_URL"],
            supabase_service_role_key=values["SUPABASE_SERVICE_ROLE_KEY"],
            embedding_model=values["OPENAI_EMBEDDING_MODEL"],
        )
