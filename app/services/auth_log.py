"""Structured auth event logging (no secrets in logs)."""

from __future__ import annotations

from app.core.logging import log_event


def mask_identifier(identifier: str) -> str:
    """Mask email local-part or username; keep domain for emails (``***@example.com``)."""
    s = identifier.strip()
    at = s.find("@")
    if at > 0:
        return f"***{s[at:]}"
    return "***"


def mask_email(email: str) -> str:
    """Alias for :func:`mask_identifier` on email addresses."""
    return mask_identifier(email)


def log_auth_event(event: str, *, level: str = "INFO", **fields: object) -> None:
    """Emit one auth-phase structured log line (``phase=auth``)."""
    log_event(event, phase="auth", level=level, **fields)
