"""Allowed profile roles (must match web UI)."""

ROLE_OPTIONS = ("user", "admin")


def normalize_roles(value) -> list[str]:
    raw: list[str] = []
    if value is None:
        raw = []
    elif isinstance(value, str):
        raw = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, list):
        raw = [str(r).strip() for r in value if str(r).strip()]

    picked = [role for role in ROLE_OPTIONS if any(r.lower() == role for r in raw)]
    return picked if picked else ["user"]
