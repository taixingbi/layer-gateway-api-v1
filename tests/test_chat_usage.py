"""Tests for orchestrator usage pass-through helpers."""

from app.services.chat_history_service import normalize_orchestrator_usage


def test_normalize_orchestrator_usage_passes_through_nested_breakdown():
    raw = {
        "prompt_tokens": 1145,
        "completion_tokens": 163,
        "total_tokens": 1308,
        "intent_router": {"prompt_tokens": 516, "completion_tokens": 54, "total_tokens": 570},
        "rag": {
            "prompt_tokens": 629,
            "completion_tokens": 109,
            "total_tokens": 738,
            "chat": {"prompt_tokens": 356, "completion_tokens": 23, "total_tokens": 379},
        },
    }
    out = normalize_orchestrator_usage(raw)
    assert out["prompt_tokens"] == 1145
    assert out["input_tokens"] == 1145
    assert out["output_tokens"] == 163
    assert out["intent_router"]["total_tokens"] == 570
    assert out["rag"]["chat"]["total_tokens"] == 379


def test_normalize_orchestrator_usage_from_response_wrapper():
    payload = {
        "answer": "Hi",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    out = normalize_orchestrator_usage(payload)
    assert out["total_tokens"] == 15
    assert out["input_tokens"] == 10


def test_normalize_orchestrator_usage_legacy_input_output_only():
    out = normalize_orchestrator_usage({"input_tokens": 1, "output_tokens": 2})
    assert out == {"input_tokens": 1, "output_tokens": 2}
