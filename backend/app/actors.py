from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.roles import UserRole


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: UUID
    techportal_user_id: str
    channel: Literal["pwa", "messenger"]
    provider: str | None = None
    external_identity_id: UUID | None = None
    permissions: dict[str, Any] | None = None
    role: UserRole = UserRole.USER
