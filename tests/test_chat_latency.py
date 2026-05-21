"""Tests for chat ``latency_ms`` envelope helpers."""

from app.services.chat_latency import (
    build_chat_latency_ms,
    orchestrator_latency_ms,
)


def test_orchestrator_latency_ms_reads_flat_upstream():
    payload = {
        "latency_ms": {
            "total": 5154.31,
            "intent_router": 2522.63,
            "rag": {"total": 2614.66},
        }
    }
    assert orchestrator_latency_ms(payload)["total"] == 5154.31


def test_orchestrator_latency_ms_reads_nested_orchestrator_key():
    payload = {
        "latency_ms": {
            "gateway_api": {"total": 100},
            "orchestrator": {"total": 50.5, "rag": {"total": 40}},
        }
    }
    assert orchestrator_latency_ms(payload)["total"] == 50.5


def test_orchestrator_latency_ms_legacy_timings_ms():
    assert orchestrator_latency_ms({"timings_ms": {"total": 9.0}})["total"] == 9.0


def test_build_chat_latency_ms_sums_gateway_total():
    out = build_chat_latency_ms(
        auth_ms=25,
        request_validation_ms=8,
        db_write_user_message_ms=35,
        orchestrator_call_ms=5154,
        db_write_assistant_message_ms=18,
        response_stream_ms=0,
        orchestrator={"total": 5154.31},
    )
    assert out["gateway_api"]["total"] == 5240
    assert out["orchestrator"]["total"] == 5154.31
