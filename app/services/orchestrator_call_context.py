from dataclasses import dataclass


@dataclass(frozen=True)
class OrchestratorCallContext:
    """Per-request fields for header-style orchestrator calls."""

    session_id: str
    request_id: str
    trace_id: str
    user_id: str
    roles: tuple[str, ...]
    groups: tuple[str, ...]
    teams: tuple[str, ...]
    stream: bool
