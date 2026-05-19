"""HTTP auth routes: signup, login, refresh, password reset (Supabase-backed)."""

from fastapi import APIRouter, Header
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.deps.auth_bearer import parse_bearer
from app.services import supabase_auth
from app.services.profile_service import ProfileUpdate, update_profile
from app.services.supabase_auth import verify_access_token

router = APIRouter(tags=["auth"])


class AuthBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class SignupBody(AuthBody):
    username: str | None = Field(default=None, min_length=1, max_length=64)


class LoginBody(BaseModel):
    password: str = Field(min_length=6)
    identifier: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_login_id(self) -> "LoginBody":
        """Require ``identifier`` or legacy ``email`` field."""
        if not (self.identifier or self.email):
            raise ValueError("identifier or email is required")
        return self

    def login_identifier(self) -> str:
        """Normalized login id (email or username)."""
        return (self.identifier or self.email or "").strip()


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class ForgotPasswordBody(BaseModel):
    email: EmailStr
    redirect_to: str | None = Field(
        default=None,
        description="Must be {FRONTEND_URL}/auth/reset-password; allowlisted on gateway.",
    )


class ResetPasswordBody(BaseModel):
    access_token: str = Field(min_length=1)
    password: str = Field(min_length=6)
    refresh_token: str | None = None


class ChangePasswordBody(BaseModel):
    password: str = Field(min_length=6)
    refresh_token: str | None = None


@router.post("/auth/signup")
def auth_signup(body: SignupBody):
    """Register a user; optionally set username on profile when session is returned."""
    payload = supabase_auth.signup(body.email, body.password)
    if body.username and payload.get("access_token"):
        token = payload["access_token"]
        claims = verify_access_token(token)
        update_profile(
            token,
            claims,
            ProfileUpdate(username=body.username.strip()),
        )
    return payload


@router.post("/auth/login")
def auth_login(body: LoginBody):
    """Sign in with email or username + password."""
    return supabase_auth.login(body.login_identifier(), body.password)


@router.post("/auth/refresh")
def auth_refresh(body: RefreshBody):
    """Exchange a refresh token for a new session."""
    return supabase_auth.refresh_session(body.refresh_token)


@router.post("/auth/forgot-password")
def auth_forgot_password(body: ForgotPasswordBody):
    """Trigger Supabase password-reset email with allowlisted ``redirect_to``."""
    return supabase_auth.forgot_password(body.email, body.redirect_to)


@router.post("/auth/reset-password")
def auth_reset_password(body: ResetPasswordBody):
    """Set new password using tokens from the reset-email link hash."""
    return supabase_auth.reset_password(body.access_token, body.password, body.refresh_token)


@router.post("/auth/change-password")
def auth_change_password(
    body: ChangePasswordBody,
    authorization: str | None = Header(default=None),
):
    """Change password for the authenticated user (Bearer access token)."""
    token = parse_bearer(authorization)
    return supabase_auth.change_password(token, body.password, body.refresh_token)
