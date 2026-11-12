"""Application settings, read from environment variables (and an optional .env file).

No secret has a usable default. In ``APP_ENV=dev`` a missing JWT/storage secret is replaced
by a random value generated at start-up (tokens and UI sessions then reset on restart);
in any other environment a missing secret stops the app from starting.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger(__name__)

_PLACEHOLDER = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "Scheme Finder"
    log_level: str = "INFO"

    # MongoDB. ``mongomock://`` selects an in-memory database (dev/test only, not persisted).
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "scheme_finder"

    # Auth
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    login_max_failures: int = 5
    lockout_minutes: int = 15
    password_reset_minutes: int = 30

    # Rate limiting (per client IP, sliding 60 s window)
    rate_limit_auth_per_minute: int = 10
    rate_limit_global_per_minute: int = 300

    # Web
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:8080"])
    storage_secret: str = ""  # signs the NiceGUI browser-session cookie
    host: str = "0.0.0.0"
    port: int = 8080

    # Seeding: create demo data on start-up when the database is empty (handy with mongomock://)
    seed_on_startup: bool = False

    # Uploaded documents: encrypted at rest with Fernet; the key lives outside the database.
    fernet_key: str = ""
    file_storage_dir: str = "storage/documents"
    max_upload_mb: int = 5
    max_pdf_pages: int = 10
    tesseract_cmd: str = ""  # optional path to the tesseract binary

    # LLM (provider-agnostic). With no key, a clearly labelled mock is used and the app still runs.
    llm_provider: Literal["mock", "anthropic"] = "mock"
    llm_api_key: str = ""
    llm_model: str = "claude-opus-5-5"

    # Retrieval (RAG). "auto" uses multilingual-e5-small when sentence-transformers is installed,
    # otherwise a small built-in hashing embedder (clearly reported by /api/assistant/status).
    embedding_backend: Literal["auto", "e5", "hash"] = "auto"
    embedding_model: str = "intfloat/multilingual-e5-small"
    vector_backend: Literal["chroma", "memory"] = "chroma"
    chroma_dir: str = "storage/chroma"
    assistant_min_confidence: float = 0.6

    # Hindi/English speech-to-text with faster-whisper (feature flag; the model downloads on first use).
    enable_voice: bool = False
    whisper_model: str = "small"
    whisper_device: str = "cpu"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def finalize(self) -> "Settings":
        for name in ("jwt_secret", "storage_secret"):
            value = getattr(self, name)
            if not value or value.startswith(_PLACEHOLDER):
                if self.app_env == "prod":
                    raise RuntimeError(f"{name.upper()} must be set in production")
                setattr(self, name, secrets.token_urlsafe(48))
                log.warning("%s not set; generated a random one for this run (%s mode)", name.upper(), self.app_env)
        if not self.fernet_key or self.fernet_key.startswith(_PLACEHOLDER):
            if self.app_env == "prod":
                raise RuntimeError("FERNET_KEY must be set in production")
            from cryptography.fernet import Fernet

            self.fernet_key = Fernet.generate_key().decode()
            log.warning("FERNET_KEY not set; generated one for this run. Files uploaded now cannot be "
                        "decrypted after a restart (%s mode)", self.app_env)
        if self.llm_provider != "mock" and not self.llm_api_key:
            log.warning("LLM_PROVIDER=%s but LLM_API_KEY is empty; falling back to the mock LLM", self.llm_provider)
            self.llm_provider = "mock"
        if len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters")
        return self

    @property
    def uses_mock_db(self) -> bool:
        return self.mongo_url.startswith("mongomock://")


@lru_cache
def get_settings() -> Settings:
    return Settings().finalize()
