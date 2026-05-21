"""Tests for chat ``latency_ms`` envelope helpers."""

from app.services.chat_latency import (
    build_chat_latency_ms,
    build_orchestrator_section,
    orchestrator_workflow_from_source,
)


def test_orchestrator_workflow_from_flat_upstream():
    payload = {
        "latency_ms": {
            "total": 3567.34,
            "intent_router": 1239.17,
            "rag": {"total": 2322.89},
        }
    }
    workflow = orchestrator_workflow_from_source(payload)
    assert workflow is not None
    assert workflow["total"] == 3567.34
    assert workflow["intent_router"] == 1239.17


def test_orchestrator_workflow_from_nested_orchestrator_section():
    payload = {
        "latency_ms": {
            "total": 4896,
            "orchestrator": {
                "proxy_total": 3586,
                "workflow": {"total": 3567.34, "intent_router": 100},
            },
        }
    }
    assert orchestrator_workflow_from_source(payload)["intent_router"] == 100


def test_orchestrator_workflow_legacy_timings_ms():
    assert orchestrator_workflow_from_source({"timings_ms": {"total": 9.0}})["total"] == 9.0


def test_build_orchestrator_section_wraps_workflow():
    section = build_orchestrator_section(
        {"total": 3567.34, "intent_router": 100},
        proxy_total_ms=3586.2,
    )
    assert section is not None
    assert section["proxy_total"] == 3586
    assert section["workflow"]["intent_router"] == 100


def test_build_chat_latency_ms_matches_envelope_schema():
    out = build_chat_latency_ms(
        auth_ms=371,
        request_validation_ms=0,
        db_write_user_message_ms=679,
        orchestrator_call_ms=3586,
        db_write_assistant_message_ms=260,
        orchestrator_workflow={
            "total": 3567.34,
            "intent_router": 1239.17,
            "rag": {"total": 2322.89},
        },
    )
    assert out["total"] == 4896
    assert out["auth"] == 371
    assert out["validation"] == 0
    assert out["storage"]["total"] == 939
    assert out["storage"]["write_user_message"] == 679
    assert out["storage"]["write_assistant_message"] == 260
    assert out["orchestrator"]["proxy_total"] == 3586
    assert out["orchestrator"]["workflow"]["total"] == 3567.34
