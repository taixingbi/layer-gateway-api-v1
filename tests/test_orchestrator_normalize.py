"""Tests for orchestrator answer envelope normalization."""

import json

from app.services.orchestrator_normalize import (
    gateway_done_fields_from_normalized,
    normalize_orchestrator_payload,
    route_label_from_route_obj,
)
from app.services.chat_history_service import normalize_orchestrator_usage
from app.services.orchestrator_client import _gateway_done_payload


def test_route_label_from_meta_route_tool():
    assert route_label_from_route_obj({"type": "tool", "tool": "github_search"}) == "github_search"
    assert route_label_from_route_obj({"type": "internal_intent", "intent": "help"}) == "help"


def test_normalize_nested_answer_and_meta():
    raw = {
        "type": "done",
        "meta": {
            "route": {
                "type": "tool",
                "tool": "user_profile",
                "source": "llm_router",
            },
            "tool": {"name": "user_profile", "key": "tool_rag"},
            "rewrite": "taixing visa status in us",
        },
        "answer": {
            "text": "H4 EAD [1]",
            "citations": [{"cite_id": 1, "source": "personal_profile"}],
        },
        "follow_up_questions": ["Renew?"],
        "usage": {
            "total": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "tool_rag": {"total": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}},
        },
        "latency_ms": {"total": 5000, "tool_rag": {"total": 2784}},
    }
    out = normalize_orchestrator_payload(raw)
    assert out["answer"] == "H4 EAD [1]"
    assert out["route"] == "user_profile"
    assert out["rewrite"] == "taixing visa status in us"
    assert out["tool_meta"]["key"] == "tool_rag"
    assert "type" not in out
    assert "stream" not in out


def test_gateway_done_payload_unwraps_terminal_envelope():
    raw = json.dumps(
        {
            "type": "done",
            "meta": {
                "route": {"type": "tool", "tool": "github_search", "source": "deterministic_rule"},
            },
            "answer": {"text": "orchestrator design", "citations": [{"cite_id": 1}]},
            "usage": {
                "total": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "tool_github_search": {"total": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            },
            "latency_ms": {"total": 100, "tool_github_search": {"total": 90}},
        }
    )
    body = _gateway_done_payload(raw, citations=[], follow_up_questions=[])
    assert body["route"] == "github_search"
    assert body["route_source"] == "deterministic_rule"
    assert body["citations"] == [{"cite_id": 1}]
    assert body["usage"]["tool_github_search"]


def test_normalize_orchestrator_usage_tool_rag_key():
    raw = {"usage": {"tool_rag": {"total": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}}}
    out = normalize_orchestrator_usage(raw)
    assert "tool_rag" in out


def test_gateway_done_fields_from_normalized_status_ok():
    fields = gateway_done_fields_from_normalized(
        {"status": {"ok": True, "state": "completed"}, "answer": "hi"}
    )
    assert fields["status"] == "success"
