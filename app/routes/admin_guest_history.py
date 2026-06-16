"""Admin-only guest chat audit (``chat_events`` table)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps.supabase_auth import token_and_claims
from app.schemas.guest_history import GuestChatEvent, GuestHistoryResponse
from app.services.chat_events_service import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT, list_guest_chat_events
from app.services.supabase_client import admin_client_configured

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _require_admin(claims: object) -> None:
    roles = [str(r).strip().lower() for r in (getattr(claims, "roles", None) or [])]
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/guest-history", response_model=GuestHistoryResponse)
def guest_history(
    auth: tuple[str, object] = Depends(token_and_claims),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
):
    """List recent guest chat audit events (newest first)."""
    _token, claims = auth
    _require_admin(claims)

    if not admin_client_configured():
        raise HTTPException(
            status_code=503,
            detail="Guest audit unavailable (SUPABASE_SERVICE_KEY not configured)",
        )

    rows = list_guest_chat_events(limit=limit)
    return GuestHistoryResponse(events=[GuestChatEvent(**row) for row in rows])
