from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    cors_origins_raw: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    duplicate_similarity_threshold: float = float(os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.62"))
    duplicate_high_similarity_threshold: float = float(os.getenv("DUPLICATE_HIGH_SIMILARITY_THRESHOLD", "0.45"))
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_api_url: str = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    @property
    def cors_origins(self) -> List[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
