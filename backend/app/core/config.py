from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List


def _default_database_url() -> str:
    data_dir = os.getenv("AIASSIST_DATA_DIR")
    if not data_dir:
        data_dir = str(Path(__file__).resolve().parents[3] / ".runtime")
    return f"sqlite:///{(Path(data_dir) / 'app.db').as_posix()}"


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", _default_database_url())
    cors_origins_raw: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    duplicate_similarity_threshold: float = float(os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.62"))
    duplicate_high_similarity_threshold: float = float(os.getenv("DUPLICATE_HIGH_SIMILARITY_THRESHOLD", "0.50"))
    duplicate_llm_timeout_seconds: int = int(os.getenv("DUPLICATE_LLM_TIMEOUT_SECONDS", "180"))
    duplicate_llm_max_tokens: int = int(os.getenv("DUPLICATE_LLM_MAX_TOKENS", "6000"))
    duplicate_judge_max_tokens: int = int(os.getenv("DUPLICATE_JUDGE_MAX_TOKENS", "24000"))
    duplicate_llm_concurrency: int = int(os.getenv("DUPLICATE_LLM_CONCURRENCY", "4"))
    duplicate_extraction_max_tokens: int = int(os.getenv("DUPLICATE_EXTRACTION_MAX_TOKENS", "24000"))
    duplicate_task_timeout_seconds: int = int(os.getenv("DUPLICATE_TASK_TIMEOUT_SECONDS", "900"))
    duplicate_candidate_top_k: int = int(os.getenv("DUPLICATE_CANDIDATE_TOP_K", "5"))
    duplicate_judge_batch_size: int = int(os.getenv("DUPLICATE_JUDGE_BATCH_SIZE", "6"))
    duplicate_judge_batch_chars: int = int(os.getenv("DUPLICATE_JUDGE_BATCH_CHARS", "24000"))
    duplicate_cache_enabled: bool = os.getenv("DUPLICATE_CACHE_ENABLED", "true").lower() == "true"
    duplicate_cache_dir: str = os.getenv("DUPLICATE_CACHE_DIR", str(
        Path(os.getenv("AIASSIST_DATA_DIR") or Path(__file__).resolve().parents[3] / ".runtime") / "duplicate_cache"
    ))
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_api_url: str = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "DeepSeek-V4-Flash")
    rule_api_base_url: str = os.getenv(
        "RULE_API_BASE_URL",
        "https://decree-tapering-that.ngrok-free.dev/rules",
    )

    @property
    def cors_origins(self) -> List[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
