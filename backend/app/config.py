"""The only place env vars are read. Everything else takes a Settings instance."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingSettingError(RuntimeError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"Missing required environment variable(s): {', '.join(names)}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "dev"

    # Claude
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_FAST: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_MODEL_SMART: str = "claude-sonnet-5"

    # Sarvam (STT, and stretch TTS fallback)
    SARVAM_API_KEY: str | None = None
    SARVAM_STT_MODEL: str = "saaras:v3-realtime"
    SARVAM_STT_MODE: str = "translit"
    # "auto" = Sarvam's adaptive language detection (English stays English); "profile" = lock to
    # the bound profile's language_code (the original §5.1 behaviour).
    SARVAM_STT_LANGUAGE: str = "auto"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_SPEAKER: str | None = None

    # TTS
    TTS_PROVIDER: str = "elevenlabs"
    ELEVENLABS_API_KEY: str | None = None
    ELEVENLABS_VOICE_ID: str | None = None
    ELEVENLABS_MODEL: str = "eleven_flash_v2_5"
    REPLY_LANGUAGE: str = "en-IN"

    # Payments
    PAYMENTS: str = "dodo"
    DODO_PAYMENTS_API_KEY: str | None = None
    DODO_ENVIRONMENT: str = "test_mode"

    # Vobiz
    VOBIZ_AUTH_ID: str | None = None
    VOBIZ_AUTH_TOKEN: str | None = None
    VOBIZ_PHONE_NUMBER: str | None = None
    VOBIZ_STREAM_SECRET: str | None = None
    PUBLIC_BASE_URL: str | None = None

    # AWS
    AWS_REGION: str = "ap-south-1"
    AUDIT_ARCHIVE: str = "s3"
    S3_AUDIT_BUCKET: str | None = None

    # LLM provider selection
    LLM_PROVIDER: str = "anthropic"
    BEDROCK_MODEL_FAST: str | None = None
    BEDROCK_MODEL_SMART: str | None = None

    # Helpdesk
    HELPDESK: str = "freshdesk"
    FRESHDESK_DOMAIN: str | None = None
    FRESHDESK_API_KEY: str | None = None
    FRESHDESK_CUSTOM_FIELDS: bool = False
    FRESHSERVICE_DOMAIN: str | None = None
    FRESHSERVICE_API_KEY: str | None = None

    # GuardRail
    GUARDRAIL_API_KEY: str | None = None
    UNDO_WINDOW_SECONDS: int = 30
    SILENCE_TIMEOUT_SECONDS: int = 8
    MAX_CALL_SECONDS: int = 180
    DEMO_FAULT_INJECTION: str = "off"

    # App
    DATABASE_URL: str = "sqlite:///./guardrail.db"
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    def model_post_init(self, __context: object) -> None:
        # A pasted full host (https://acme.freshdesk.com) is normalised to the subdomain.
        if self.FRESHDESK_DOMAIN:
            domain = self.FRESHDESK_DOMAIN
            domain = domain.removeprefix("https://").removeprefix("http://")
            domain = domain.removesuffix(".freshdesk.com").removesuffix("/")
            self.FRESHDESK_DOMAIN = domain

    @property
    def public_host(self) -> str | None:
        if not self.PUBLIC_BASE_URL:
            return None
        return self.PUBLIC_BASE_URL.removeprefix("https://").removeprefix("http://").split("/")[0]

    def require(self, *names: str) -> None:
        """Raise MissingSettingError naming every requested var that is unset or blank."""
        missing = [name for name in names if not getattr(self, name, None)]
        if missing:
            raise MissingSettingError(missing)

    # Each provider factory calls the group it's about to construct, at the point it's
    # actually wired in — not eagerly at import time, so phases that don't need a provider
    # yet (see ARCHITECTURE.md §15) aren't blocked by keys the user hasn't collected yet.
    def require_llm(self) -> None:
        if self.LLM_PROVIDER == "anthropic":
            self.require("ANTHROPIC_API_KEY")
        elif self.LLM_PROVIDER == "bedrock":
            self.require("BEDROCK_MODEL_FAST", "BEDROCK_MODEL_SMART")

    def require_stt(self) -> None:
        self.require("SARVAM_API_KEY")

    def require_tts(self) -> None:
        if self.TTS_PROVIDER == "elevenlabs":
            self.require("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
        elif self.TTS_PROVIDER == "sarvam":
            self.require("SARVAM_API_KEY", "SARVAM_TTS_SPEAKER")

    def require_payments(self) -> None:
        if self.PAYMENTS == "dodo":
            self.require("DODO_PAYMENTS_API_KEY")

    def require_vobiz(self) -> None:
        self.require("VOBIZ_STREAM_SECRET", "VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN", "PUBLIC_BASE_URL")

    def require_helpdesk(self) -> None:
        if self.HELPDESK == "freshdesk":
            self.require("FRESHDESK_DOMAIN", "FRESHDESK_API_KEY")
        elif self.HELPDESK == "freshservice":
            self.require("FRESHSERVICE_DOMAIN", "FRESHSERVICE_API_KEY")

    def require_audit_archive(self) -> None:
        if self.AUDIT_ARCHIVE == "s3":
            self.require("S3_AUDIT_BUCKET")

    def require_public_api(self) -> None:
        self.require("GUARDRAIL_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
