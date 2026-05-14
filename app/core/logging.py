import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from app.core.time_util import eastern_from_timestamp


class EasternJsonFormatter(jsonlogger.JsonFormatter):
    """``asctime`` in US Eastern (``America/New_York``), not server local / UTC."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: ARG002
        return eastern_from_timestamp(record.created)


def configure_logging(env: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = EasternJsonFormatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger = logging.getLogger("gateway")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("logger_configured", extra={"env": env})


def get_logger() -> logging.Logger:
    return logging.getLogger("gateway")


def log_event(event: str, **fields: Any) -> None:
    logger = get_logger()
    logger.info(event, extra={"event": event, **fields})
