import logging
import sys
import time
from typing import Any

from pythonjsonlogger import jsonlogger

from app.core.time_util import eastern_from_timestamp


def _base_log_extra(event: str, **fields: Any) -> dict[str, Any]:
    return {
        "ts": eastern_from_timestamp(time.time()),
        "level": "INFO",
        "event": event,
        **fields,
    }


class EasternJsonFormatter(jsonlogger.JsonFormatter):
    """JSON logs without stdlib ``asctime`` / ``levelname``; stable field order for operators."""

    def __init__(self) -> None:
        # Only ``message`` from the LogRecord; ``ts`` / ``level`` come from ``_base_log_extra`` / route extras.
        super().__init__("%(message)s")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: ARG002
        return eastern_from_timestamp(record.created)

    def process_log_record(self, log_data: dict[str, Any]) -> dict[str, Any]:
        log_data.pop("asctime", None)
        levelname = log_data.pop("levelname", None)
        if levelname is not None and "level" not in log_data:
            log_data["level"] = levelname
        priority = [
            "ts",
            "level",
            "message",
            "event",
            "service",
            "request_id",
            "trace_id",
            "session_id",
            "path",
            "method",
            "status_code",
            "latency_ms",
            "ttfb_ms",
            "stream",
            "backend",
        ]
        ordered: dict[str, Any] = {}
        seen: set[str] = set()
        for key in priority:
            if key in log_data:
                ordered[key] = log_data[key]
                seen.add(key)
        for key in sorted(log_data.keys()):
            if key not in seen:
                ordered[key] = log_data[key]
        return ordered


def configure_logging(env: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = EasternJsonFormatter()
    handler.setFormatter(formatter)
    logger = logging.getLogger("gateway")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("logger_configured", extra=_base_log_extra("logger_configured", env=env))


def get_logger() -> logging.Logger:
    return logging.getLogger("gateway")


def log_event(event: str, **fields: Any) -> None:
    get_logger().info(event, extra=_base_log_extra(event, **fields))
