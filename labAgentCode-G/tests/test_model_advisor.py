from datetime import UTC, datetime

from labagent.model_advisor import consult, deepseek_flash_cost


def test_ollama_consult_uses_local_chat_api_and_reports_usage():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"content": "建议先检查测速仪标定，再执行低风险扫描。"},
                "prompt_eval_count": 81,
                "eval_count": 22,
                "total_duration": 1_250_000_000,
            }

    def request(url, *, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    advice = consult(
        {"scenario": "field_generation", "objective": "生成目标湍流度"},
        [],
        {"provider": "ollama", "model": "qwen2.5:1.5b", "temperature": 0.2, "max_tokens": 128},
        request=request,
    )

    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1]["model"] == "qwen2.5:1.5b"
    assert calls[0][1]["stream"] is False
    assert calls[0][1]["options"] == {"temperature": 0.2, "num_predict": 128}
    assert advice.content.startswith("建议先检查")
    assert advice.input_tokens == 81
    assert advice.output_tokens == 22
    assert advice.cost_usd == 0
    assert advice.latency_ms == 1250


def test_deepseek_consult_uses_openai_compatible_api(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "建议先检查标定和图像质量。"}}],
                "usage": {"prompt_tokens": 70, "completion_tokens": 18},
            }

    def request(url, *, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    advice = consult(
        {"scenario": "piv", "objective": "检查图像质量"},
        [],
        {"provider": "deepseek", "model": "deepseek-flash", "temperature": 0.2, "max_tokens": 128},
        request=request,
    )

    assert calls[0][0] == "https://api.deepseek.com/chat/completions"
    assert calls[0][1]["model"] == "deepseek-flash"
    assert calls[0][1]["max_tokens"] == 128
    assert calls[0][1]["thinking"] == {"type": "disabled"}
    assert calls[0][2]["Authorization"] == "Bearer test-key"
    assert advice.content == "建议先检查标定和图像质量。"
    assert advice.input_tokens == 70
    assert advice.output_tokens == 18


def test_deepseek_flash_cost_uses_off_peak_cache_miss_prices():
    cost = deepseek_flash_cost(
        input_tokens=335,
        output_tokens=96,
        cached_input_tokens=0,
        now=datetime(2026, 9, 15, 11, 0, tzinfo=UTC),
    )

    assert cost == 0.00010785
