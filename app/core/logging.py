import logging
import sys
import time
from typing import Any

from pythonjsonlogger import jsonlogger

from app.core.time_util import eastern_from_timestamp

GATEWAY_LOGGER_NAME = "gateway"

# Stable leading keys for JSON log lines (operators / log aggregators).
LOG_FIELD_PRIORITY: tuple[str, ...] = (
    "ts",
    "level",
    "logger",
    "phase",
    "event",
    "message",
    "service",
    "request_id",
    "trace_id",
    "session_id",
    "conversation_id",
    "path",
    "method",
    "status",
    "gateway_meta",
    "latency_ms",
    "ttfb_ms",
    "stream",
    "backend",
)

_EVENT_PHASE: dict[str, str] = {
    "request_received": "ingress",
    "request_validated": "ingress",
    "request_complete": "access",
    "orchestrator_call_started": "upstream",
    "orchestrator_call_succeeded": "upstream",
    "orchestrator_call_failed": "upstream",
    "orchestrator_http_request": "upstream",
    "orchestrator_http_response": "upstream",
    "orchestrator_api_request": "orchestrator_upstream",
    "orchestrator_api_response": "orchestrator_upstream",
    "stream_metadata_supplement_failed": "upstream",
    "logger_configured": "system",
    "password_reset_redirect_resolved": "auth",
    "password_reset_redirect_rejected": "auth",
}

_EVENT_MESSAGE: dict[str, str] = {
    "request_received": "Request received",
    "request_validated": "Request validated",
    "request_complete": "Request complete",
    "orchestrator_call_started": "Orchestrator call started",
    "orchestrator_call_succeeded": "Orchestrator call succeeded",
    "orchestrator_call_failed": "Orchestrator call failed",
    "orchestrator_http_request": "Orchestrator HTTP request",
    "orchestrator_http_response": "Orchestrator HTTP response",
    "orchestrator_api_request": "orchestrator_api_request",
    "orchestrator_api_response": "orchestrator_api_response",
    "stream_metadata_supplement_failed": "Stream metadata supplement failed",
    "logger_configured": "Logger configured",
    "password_reset_redirect_resolved": "Password reset redirect resolved",
    "password_reset_redirect_rejected": "Password reset redirect rejected",
}

_LEVEL_NUM: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _phase_for_event(event: str) -> str:
    return _EVENT_PHASE.get(event, "system")


def _message_for_event(event: str) -> str:
    return _EVENT_MESSAGE.get(event, event)


def _base_log_extra(
    event: str,
    *,
    level: str = "INFO",
    phase: str | None = None,
    message: str | None = None,
    logger: str = GATEWAY_LOGGER_NAME,
    ts: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    # ``message`` is reserved on ``LogRecord``; emit human text as ``log_message`` and
    # promote to ``message`` in ``EasternJsonFormatter``.
    return {
        "ts": ts or eastern_from_timestamp(time.time()),
        "level": level.upper(),
        "logger": logger,
        "phase": phase or _phase_for_event(event),
        "event": event,
        "log_message": message or _message_for_event(event),
        **fields,
    }


class EasternJsonFormatter(jsonlogger.JsonFormatter):
    """JSON logs without stdlib ``asctime`` / ``levelname``; stable field order for operators."""

    def __init__(self) -> None:
        super().__init__("%(message)s")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: ARG002
        return eastern_from_timestamp(record.created)

    def process_log_record(self, log_data: dict[str, Any]) -> dict[str, Any]:
        log_data.pop("asctime", None)
        levelname = log_data.pop("levelname", None)
        if levelname is not None and "level" not in log_data:
            log_data["level"] = levelname
        if "logger" not in log_data:
            log_data["logger"] = record_name if (record_name := log_data.get("name")) else GATEWAY_LOGGER_NAME
        log_data.pop("name", None)
        if "log_message" in log_data:
            log_data["message"] = log_data.pop("log_message")
        elif "event" in log_data:
            event_name = str(log_data["event"])
            if log_data.get("message") in (None, event_name, log_data.get("msg")):
                log_data["message"] = _message_for_event(event_name)
        log_data.pop("msg", None)
        ordered: dict[str, Any] = {}
        seen: set[str] = set()
        for key in LOG_FIELD_PRIORITY:
            if key in log_data:
                ordered[key] = log_data[key]
                seen.add(key)
        for key in sorted(log_data.keys()):
            if key not in seen:
                ordered[key] = log_data[key]
        return ordered


def configure_logging(env: str) -> None:
    from app.core.config import get_settings

    handler = logging.StreamHandler(sys.stdout)
    formatter = EasternJsonFormatter()
    handler.setFormatter(formatter)
    logger = logging.getLogger(GATEWAY_LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    settings = get_settings()
    logger.info(
        "logger_configured",
        extra=_base_log_extra("logger_configured", env=env, service=settings.service_name),
    )


def get_logger() -> logging.Logger:
    return logging.getLogger(GATEWAY_LOGGER_NAME)


def log_event(event: str, **fields: Any) -> None:
    """Emit one structured JSON log line (``logger``, ``phase``, ``event``, ``message``, …)."""
    merged: dict[str, Any] = dict(fields)
    level = str(merged.pop("level", "INFO")).upper()
    phase = merged.pop("phase", None)
    message = merged.pop("message", None)
    logger_name = str(merged.pop("logger", GATEWAY_LOGGER_NAME))
    ts = merged.pop("ts", None)
    omit_service = bool(merged.pop("omit_service", False))
    if not omit_service and "service" not in merged:
        from app.core.config import get_settings

        merged["service"] = get_settings().service_name
    extra = _base_log_extra(
        event,
        level=level,
        phase=phase,
        message=message,
        logger=logger_name,
        ts=ts,
        **merged,
    )
    get_logger().log(_LEVEL_NUM.get(level, logging.INFO), event, extra=extra)
