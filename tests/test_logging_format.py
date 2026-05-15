"""JSON log shape: logger, phase, event, message; stable leading keys."""

import json
import logging

import pytest

from app.core.logging import EasternJsonFormatter, GATEWAY_LOGGER_NAME, LOG_FIELD_PRIORITY


@pytest.fixture
def formatter() -> EasternJsonFormatter:
    return EasternJsonFormatter()


def test_eastern_json_formatter_standard_shape(formatter: EasternJsonFormatter) -> None:
    record = logging.LogRecord(
        name=GATEWAY_LOGGER_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_complete",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(
        {
            "ts": "2026-05-15T12:32:43.350612-04:00",
            "level": "INFO",
            "logger": GATEWAY_LOGGER_NAME,
            "phase": "access",
            "event": "request_complete",
            "log_message": "Request complete",
            "service": "gateway-api",
            "request_id": "req_demo_002",
            "trace_id": "trace_demo_002",
            "session_id": "sess_123",
            "conversation_id": "conv_demo",
            "path": "/api/chat",
            "method": "POST",
            "status": 200,
            "latency_ms": 12.371,
            "stream": True,
            "backend": "orchestrator",
        }
    )
    line = formatter.format(record)
    data = json.loads(line)
    assert "asctime" not in data
    assert "levelname" not in data
    assert data["logger"] == GATEWAY_LOGGER_NAME
    assert data["phase"] == "access"
    assert data["event"] == "request_complete"
    assert data["message"] == "Request complete"
    prefix = [k for k in LOG_FIELD_PRIORITY if k in data]
    assert list(data.keys())[: len(prefix)] == prefix


def test_log_event_emits_pattern(capsys) -> None:
    from app.core.logging import configure_logging, log_event

    configure_logging("test")
    log_event(
        "orchestrator_http_request",
        request_id="req_1",
        session_id="sess_1",
        conversation_id="conv_1",
        path="/v1/chat",
        method="POST",
        stream=False,
        backend="orchestrator",
    )
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["ts"]
    assert data["level"] == "INFO"
    assert data["logger"] == GATEWAY_LOGGER_NAME
    assert data["phase"] == "upstream"
    assert data["event"] == "orchestrator_http_request"
    assert data["message"] == "Orchestrator HTTP request"
    assert data["request_id"] == "req_1"
    assert data["session_id"] == "sess_1"
    assert data["conversation_id"] == "conv_1"
