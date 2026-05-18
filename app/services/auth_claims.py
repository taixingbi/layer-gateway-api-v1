from dataclasses import dataclass

from app.services.auth_roles import normalize_roles


@dataclass
class UserClaims:
    user_id: str
    email: str | None
    roles: list[str]
    team: str
    group: str
    plan: str

    def to_jwt_claims(self) -> dict:
        return {
            "sub": self.user_id,
            "email": self.email or "",
            "roles": self.roles,
            "team": self.team,
            "group": self.group,
            "plan": self.plan,
        }

    def to_user_dict(self) -> dict:
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
