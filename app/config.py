from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


def load_environment() -> None:
    _load_env_file(PROJECT_ROOT / ".env")
    # Local development convenience: reuse the parent workspace key when present.
    _load_env_file(PROJECT_ROOT.parent / ".env")


@dataclass(frozen=True)
class Settings:
    model: str = "qwen-turbo"
    api_key: str = ""
    ai_mode: str = "auto"
    host: str = "127.0.0.1"
    port: int = 8010
    max_file_mb: int = 10
    llm_timeout_seconds: int = 30
    llm_max_attempts: int = 2
    llm_input_char_limit: int = 6000
    dashscope_proxy_mode: str = "direct"
    database_path: Path = PROJECT_ROOT / "data" / "hireagent.db"

    @classmethod
    def from_env(cls) -> "Settings":
        load_environment()
        return cls(
            model=os.getenv("MODEL", "qwen-turbo").strip() or "qwen-turbo",
            api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            ai_mode=os.getenv("AI_MODE", "auto").strip().lower() or "auto",
            host=os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("PORT", "8010")),
            max_file_mb=int(os.getenv("MAX_FILE_MB", "10")),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            llm_max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "1")),
            llm_input_char_limit=int(os.getenv("LLM_INPUT_CHAR_LIMIT", "6000")),
            dashscope_proxy_mode=os.getenv("DASHSCOPE_PROXY_MODE", "direct").strip().lower() or "direct",
        )

    @property
    def llm_enabled(self) -> bool:
        if self.ai_mode == "off":
            return False
        return bool(self.api_key)
