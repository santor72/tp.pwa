from typing import Any

from app.actors import Actor
from app.errors import PermissionDeniedError


def has_permission(permissions: dict[str, Any], *path: str) -> bool:
    """Return true only for an explicit boolean permission at the supplied path."""
    value: Any = permissions
    for key in path:
        if not isinstance(value, dict):
            return False
        value = value.get(key)
    return value is True


def require_permission(actor: Actor, *path: str) -> None:
    if not has_permission(actor.permissions or {}, *path):
        raise PermissionDeniedError()
