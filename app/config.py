from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=True)


class VehicleKnowledgeSource(BaseModel):
    vehicle: str
    documents: list[str] = Field(default_factory=list)


class KnowledgeSourcesSettings(BaseModel):
    default_vehicle: str = "EMU800"
    vehicles: list[VehicleKnowledgeSource] = Field(default_factory=list)


class Settings(BaseSettings):
    openai_api_key: str | None = None
    openai_text_model: str = "gpt-4.1-mini"
    openai_realtime_model: str = "gpt-realtime"
    vehicle: str = "EMU800"
    knowledge_settings_file: str = "settings/knowledge_sources.json"
    knowledge_markdown: str | None = None
    database_path: str = "data/knowledge.db"

    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), extra="ignore")

    @property
    def knowledge_sources_path(self) -> Path:
        return self._resolve_path(self.knowledge_settings_file)

    @property
    def knowledge_sources(self) -> KnowledgeSourcesSettings:
        path = self.knowledge_sources_path
        if not path.exists():
            if self.knowledge_markdown:
                return KnowledgeSourcesSettings(
                    default_vehicle=self.vehicle,
                    vehicles=[
                        VehicleKnowledgeSource(vehicle=self.vehicle, documents=[self.knowledge_markdown])
                    ],
                )
            raise FileNotFoundError(f"Knowledge settings file not found: {path}")
        return KnowledgeSourcesSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @property
    def active_vehicle(self) -> str:
        return self.vehicle or self.knowledge_sources.default_vehicle

    @property
    def knowledge_paths(self) -> list[Path]:
        sources = self.knowledge_sources
        vehicle = self.active_vehicle
        for item in sources.vehicles:
            if item.vehicle == vehicle:
                return [self._resolve_path(document) for document in item.documents]
        raise ValueError(f"No knowledge documents configured for vehicle: {vehicle}")

    @property
    def knowledge_path(self) -> Path:
        paths = self.knowledge_paths
        if not paths:
            raise ValueError(f"No knowledge documents configured for vehicle: {self.active_vehicle}")
        return paths[0]

    @property
    def db_path(self) -> Path:
        return self._resolve_path(self.database_path)

    def _resolve_path(self, value: str) -> Path:
        configured = Path(value)
        if configured.is_absolute():
            return configured
        return ROOT_DIR / configured


settings = Settings()
