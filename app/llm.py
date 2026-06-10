from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Any

from .config import Settings
from .schemas import ModelCallTrace
from .text_hygiene import normalize_payload_text


logger = logging.getLogger("hireagent")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@contextmanager
def _dashscope_network_env(proxy_mode: str):
    if proxy_mode != "direct":
        yield
        return
    previous = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class QwenClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._call_traces: list[ModelCallTrace] = []
        self._circuit_open = False

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def begin_run(self) -> None:
        self._call_traces = []
        self._circuit_open = False

    @property
    def call_traces(self) -> list[ModelCallTrace]:
        return list(self._call_traces)

    @property
    def enhanced_this_run(self) -> bool:
        return any(item.status == "success" for item in self.call_traces)

    def _record(self, trace: ModelCallTrace) -> None:
        self._call_traces.append(trace)

    def mark_invalid(self, task: str, error: str) -> None:
        traces = self.call_traces
        for index in range(len(traces) - 1, -1, -1):
            if traces[index].task == task and traces[index].status == "success":
                traces[index] = ModelCallTrace(
                    task=task,
                    status="fallback",
                    attempts=traces[index].attempts,
                    error=error[:240],
                )
                self._call_traces = traces
                return

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("模型输出不是 JSON 对象")
        return normalize_payload_text(value)

    def call_json(
        self,
        system_prompt: str,
        user_content: str,
        *,
        task: str = "model_call",
        max_attempts: int | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            self._record(ModelCallTrace(task=task, status="skipped"))
            return None
        if self._circuit_open:
            self._record(
                ModelCallTrace(
                    task=task,
                    status="skipped",
                    error="本轮模型调用已熔断，使用确定性降级结果",
                )
            )
            return None
        attempts = max_attempts or self.settings.llm_max_attempts
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                import dashscope

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ]
                logger.info(
                    "Qwen JSON 调用 (%s): model=%s user_chars=%s system_chars=%s timeout=%ss proxy_mode=%s",
                    task,
                    self.settings.model,
                    len(user_content),
                    len(system_prompt),
                    self.settings.llm_timeout_seconds,
                    self.settings.dashscope_proxy_mode,
                )
                if attempt > 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上一轮输出无法解析。请重新输出一个完整、合法、"
                                "无 Markdown 代码块的 JSON 对象，不要附加解释。"
                            ),
                        }
                    )
                with _dashscope_network_env(self.settings.dashscope_proxy_mode):
                    response = dashscope.Generation.call(
                        api_key=self.settings.api_key,
                        model=self.settings.model,
                        messages=messages,
                        result_format="message",
                        temperature=0.1,
                        request_timeout=self.settings.llm_timeout_seconds,
                    )
                if response.status_code != 200:
                    raise RuntimeError(f"Qwen 调用失败: {response.code} {response.message}")
                content = response.output.choices[0].message.content
                payload = self._extract_json(content)
                self._record(
                    ModelCallTrace(task=task, status="success", attempts=attempt)
                )
                return payload
            except Exception as exc:
                last_error = str(exc)[:240]
                logger.warning(
                    "Qwen JSON 调用第 %s/%s 次失败 (%s): %s",
                    attempt,
                    attempts,
                    task,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(0.2 * attempt)
        self._record(
            ModelCallTrace(
                task=task,
                status="fallback",
                attempts=attempts,
                error=last_error,
            )
        )
        self._circuit_open = True
        return None
