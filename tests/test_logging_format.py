"""JSON log shape: no stdlib ``asctime`` / ``levelname``; stable leading keys."""

import json
import logging

import pytest

from app.core.logging import EasternJsonFormatter


@pytest.fixture
def formatter() -> EasternJsonFormatter:
    return EasternJsonFormatter()


def test_eastern_json_formatter_omits_asctime_levelname(formatter: EasternJsonFormatter) -> None:
    record = logging.LogRecord(
        name="gateway",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_complete",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(
        {
            "ts": "2026-05-14T10:09:22-04:00",
            "level": "INFO",
            "event": "request_complete",
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
    assert data["message"] == "request_complete"
    assert data["event"] == "request_complete"
    assert list(data.keys())[:13] == [
        "ts",
        "level",
        "message",
        "event",
        "service",
        "request_id",
        "trace_id",
        "session_id",
        "conversation_id",
        "path",
        "method",
        "status",
        "latency_ms",
    ]
