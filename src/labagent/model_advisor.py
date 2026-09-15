"""Optional, fail-soft language-model advice for laboratory planning."""
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
SYSTEM_PROMPT = (
    "You are a cautious fluid-laboratory planning adviser. Respond concisely in Chinese. "
    "Suggest measurement checks, analysis options, or evidence gaps only. "
    "Do not issue equipment commands. Simulation records are not experimental evidence."
)


@dataclass(frozen=True)
class Advice:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


def deepseek_flash_cost(
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    now: datetime | None = None,
) -> float:
    """Estimate DeepSeek Flash USD cost from its published per-million-token rates."""
    at = now or datetime.now(UTC)
    utc_hour = at.astimezone(UTC).hour
    peak = at.weekday() < 5 and (1 <= utc_hour < 4 or 6 <= utc_hour < 10)
    factor = 2 if peak else 1
    cache_hit_rate = 0.003 * factor
    cache_miss_rate = 0.15 * factor
    output_rate = 0.6 * factor
    cached = min(max(cached_input_tokens, 0), input_tokens)
    cost = (cached * cache_hit_rate + (input_tokens - cached) * cache_miss_rate
            + output_tokens * output_rate) / 1_000_000
    return round(cost, 12)


def consult(
    task: dict[str, Any],
    memories: list[dict[str, Any]],
    settings: dict[str, Any],
    request: Callable[..., Any] = httpx.post,
) -> Advice:
    """Ask a supported advisory model. This function never controls hardware."""
    provider = settings.get("provider", "disabled")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": str({"task": task, "recent_memories": memories[-5:]})},
    ]
    if provider == "ollama":
        response = request(
            OLLAMA_CHAT_URL,
            json={
                "model": settings["model"],
                "stream": False,
                "messages": messages,
                "options": {
                    "temperature": settings.get("temperature", 0.2),
                    "num_predict": settings.get("max_tokens", 512),
                },
            },
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        content = result.get("message", {}).get("content", "").strip()
        if not content:
            raise ValueError("Ollama returned an empty advisory response")
        return Advice(
            content=content,
            input_tokens=int(result.get("prompt_eval_count", 0)),
            output_tokens=int(result.get("eval_count", 0)),
            cost_usd=0,
            latency_ms=round(int(result.get("total_duration", 0)) / 1_000_000),
        )

    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        started = time.monotonic()
        response = request(
            DEEPSEEK_CHAT_URL,
            json={
                "model": settings["model"],
                "messages": messages,
                "temperature": settings.get("temperature", 0.2),
                "max_tokens": settings.get("max_tokens", 512),
                "thinking": {"type": "disabled"},
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise ValueError("DeepSeek returned an empty advisory response")
        usage = result.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached_input_tokens = int(usage.get("prompt_cache_hit_tokens", 0))
        return Advice(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=deepseek_flash_cost(input_tokens, output_tokens, cached_input_tokens),
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    raise ValueError(f"Provider '{provider}' has no configured runtime adapter")
