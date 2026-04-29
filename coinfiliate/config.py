from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Tuple
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SyncConfig(BaseModel):
    page: int = 1
    page_size: int = 100
    selectable_fields: str = "all"


class RunnerConfig(BaseModel):
    max_shops_per_batch: int = 50
    max_concurrency: int = 4
    inter_shop_jitter_ms: Tuple[int, int] = (500, 2000)


class HarvestConfig(BaseModel):
    networkidle_timeout_seconds: int = 15
    consent_wait_ms: int = 2000
    review_threshold: float = 0.0


class WritebackConfig(BaseModel):
    verify_after_save: bool = True


class LLMConfig(BaseModel):
    provider: Literal["openai", "gemini"] = "openai"
    model: str = "gpt-4o-mini"
    max_retries: int = 3
    timeout_seconds: int = 30


class LoggingConfig(BaseModel):
    level: str = "INFO"
    debug_log_retention_days: int = 7


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # From config.yaml
    networks: list[str] = Field(default_factory=lambda: ["flexoffers"])
    sync: SyncConfig = Field(default_factory=SyncConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    harvest: HarvestConfig = Field(default_factory=HarvestConfig)
    writeback: WritebackConfig = Field(default_factory=WritebackConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # From env only
    coinfiliate_email: str
    coinfiliate_pass: str
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None


def load_settings(config_path: Path = Path("config.yaml")) -> Settings:
    yaml_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    return Settings(**yaml_data)
