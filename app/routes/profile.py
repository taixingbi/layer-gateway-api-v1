from fastapi import APIRouter, Depends

from app.deps.supabase_auth import token_and_claims
from app.services.profile_service import ProfileUpdate, get_profile, update_profile

router = APIRouter(tags=["profile"])


@router.get("/profile")
def profile_get(auth: tuple[str, object] = Depends(token_and_claims)):
    token, claims = auth
    return get_profile(token, claims)


@router.patch("/profile")
def profile_patch(
    body: ProfileUpdate,
    auth: tuple[str, object] = Depends(token_and_claims),
):
    token, claims = auth
    return update_profile(token, claims, body)
