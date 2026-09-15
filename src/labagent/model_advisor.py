"""Optional, fail-soft language-model advice for laboratory planning."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
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


def consult(
    task: dict[str, Any],
    memories: list[dict[str, Any]],
    settings: dict[str, Any],
    request: Callable[..., Any] = httpx.post,
) -> Advice:
    """Ask a supported advisory model. This function never controls hardware."""
    provider = settings.get("provider", "disabled")
    if provider != "ollama":
        raise ValueError(f"Provider '{provider}' has no configured runtime adapter")

    payload = {
        "model": settings["model"],
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": str({"task": task, "recent_memories": memories[-5:]})},
        ],
        "options": {
            "temperature": settings.get("temperature", 0.2),
            "num_predict": settings.get("max_tokens", 512),
        },
    }
    response = request(OLLAMA_CHAT_URL, json=payload, timeout=20)
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
