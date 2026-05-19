"""Profile-derived user claims for JWT metadata and orchestrator auth."""

from dataclasses import dataclass

from app.services.auth_roles import normalize_roles


@dataclass
class UserClaims:
    """Identity and RBAC fields mirrored from Supabase profile."""

    user_id: str
    email: str | None
    roles: list[str]
    team: str
    group: str
    plan: str

    def to_jwt_claims(self) -> dict:
        """Serialize claims for JWT user_metadata."""
        return {
            "sub": self.user_id,
            "email": self.email or "",
            "roles": self.roles,
            "team": self.team,
            "group": self.group,
            "plan": self.plan,
        }

    def to_user_dict(self) -> dict:
        """Flatten claims for API responses."""
        claims = self.to_jwt_claims()
        return {
            "user_id": self.user_id,
            "email": self.email,
            **claims,
            "jwt_claims": claims,
        }

    def to_auth_context(self, settings) -> dict:
        """Map to gateway orchestrator auth_context."""
        tenant = (getattr(settings, "auth_jwt_default_tenant_id", None) or "").strip()
        return {
            "user_id": self.user_id,
            "tenant_id": tenant,
            "roles": self.roles,
            "groups": [self.group] if self.group else [],
            "teams": [self.team] if self.team else [],
        }
