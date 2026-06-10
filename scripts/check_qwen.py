from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.llm import QwenClient


def main() -> None:
    settings = Settings.from_env()
    print(
        {
            "llm_enabled": settings.llm_enabled,
            "model": settings.model,
            "ai_mode": settings.ai_mode,
            "has_key": bool(settings.api_key),
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_attempts": settings.llm_max_attempts,
            "proxy_mode": settings.dashscope_proxy_mode,
        }
    )
    client = QwenClient(settings)
    client.begin_run()
    payload = client.call_json(
        '只输出合法 JSON 对象，例如 {"ok": true}',
        '请输出 {"ok": true}',
        task="qwen_smoke_test",
    )
    if payload:
        print({"status": "success", "payload": payload})
        return
    trace = client.call_traces[0] if client.call_traces else None
    print(
        {
            "status": "failed",
            "model_call_status": trace.status if trace else "",
            "attempts": trace.attempts if trace else 0,
            "error": trace.error if trace else "没有模型调用记录",
        }
    )


if __name__ == "__main__":
    main()
