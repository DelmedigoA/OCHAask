"""Environment configuration shared by indexing and search."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Server-side settings; service credentials must never reach browser clients."""

    supabase_url: str
    supabase_secret_key: str
    embedding_model: str
    pdf_bucket: str = "pdfs"

    @classmethod
    def from_environment(cls) -> Settings:
        """Load the optional .env file and check the required configuration."""
        load_dotenv()
        required = (
            "OPENAI_API_KEY",
            "OPENAI_EMBEDDING_MODEL",
            "SUPABASE_URL",
        )
        values = {name: os.getenv(name, "").strip() for name in required}
        secret = os.getenv("SUPABASE_SECRET_KEY", "").strip()
        keys_json = os.getenv("SUPABASE_SECRET_KEYS", "").strip()
        if not secret and keys_json:
            try:
                keys = json.loads(keys_json)
                secret = keys.get("default", "") if isinstance(keys, dict) else ""
            except ValueError as exc:
                raise ValueError("SUPABASE_SECRET_KEYS must be a JSON object") from exc
            if not isinstance(secret, str):
                raise ValueError("SUPABASE_SECRET_KEYS default must be a string")
            secret = secret.strip()
        missing = [name for name, value in values.items() if not value]
        if not secret:
            missing.append("SUPABASE_SECRET_KEY (or SUPABASE_SECRET_KEYS.default)")
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return cls(
            supabase_url=values["SUPABASE_URL"],
            supabase_secret_key=secret,
            embedding_model=values["OPENAI_EMBEDDING_MODEL"],
            pdf_bucket=os.getenv("SUPABASE_PDF_BUCKET", "pdfs").strip() or "pdfs",
        )
