from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=True)


class Settings(BaseSettings):
    openai_api_key: str | None = None
    openai_text_model: str = "gpt-4.1-mini"
    openai_realtime_model: str = "gpt-realtime"
    vehicle: str | None = None
    knowledge_settings_file: str = "settings/knowledge_sources.json"
    database_path: str = "data/knowledge.db"

    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), extra="ignore")

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


settings = Settings()
