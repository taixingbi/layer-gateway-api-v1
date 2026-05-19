"""Structured auth event logging (no secrets in logs)."""

from __future__ import annotations

from app.core.logging import log_event


def mask_identifier(identifier: str) -> str:
    s = identifier.strip()
    at = s.find("@")
    if at > 0:
        return f"***{s[at:]}"
    return "***"


def mask_email(email: str) -> str:
    return mask_identifier(email)


def log_auth_event(event: str, *, level: str = "INFO", **fields: object) -> None:
    log_event(event, phase="auth", level=level, **fields)
