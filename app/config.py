from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
APPLICATION_SETTINGS_FILE = ROOT_DIR / "settings" / "application.toml"


class SecretSettings(BaseSettings):
    """Secrets supplied only through the process environment or the local .env."""

    openai_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class ApplicationFileSettings(BaseModel):
    """Validated, non-secret settings loaded from settings/application.toml."""

    openai_text_model: str = Field(alias="OPENAI_TEXT_MODEL", min_length=1)
    openai_realtime_model: str = Field(alias="OPENAI_REALTIME_MODEL", min_length=1)
    openai_realtime_vad_threshold: float = Field(
        alias="OPENAI_REALTIME_VAD_THRESHOLD",
        ge=0.0,
        le=1.0,
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("openai_text_model", "openai_realtime_model")
    @classmethod
    def model_name_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model name must not be blank")
        return normalized


class Settings(ApplicationFileSettings):
    openai_api_key: str | None = None
    vehicle: str | None = None
    knowledge_settings_file: str = "settings/knowledge_sources.json"
    database_path: str = "data/knowledge.db"

    @property
    def knowledge_sources_path(self) -> Path:
        return self._resolve_path(self.knowledge_settings_file)

    @property
    def active_vehicle(self) -> str | None:
        return self.vehicle

    @property
    def knowledge_paths(self) -> list[Path]:
        return []

    @property
    def db_path(self) -> Path:
        return self._resolve_path(self.database_path)

    def _resolve_path(self, value: str) -> Path:
        configured = Path(value)
        if configured.is_absolute():
            return configured
        return ROOT_DIR / configured


def load_application_settings(path: Path = APPLICATION_SETTINGS_FILE) -> ApplicationFileSettings:
    try:
        with path.open("rb") as settings_file:
            payload = tomllib.load(settings_file)
        return ApplicationFileSettings.model_validate(payload)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Application settings file not found: {path}") from exc
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Invalid application settings in {path}: {exc}") from exc


application_settings = load_application_settings()
secret_settings = SecretSettings()
settings = Settings(
    **application_settings.model_dump(),
    openai_api_key=secret_settings.openai_api_key,
)
