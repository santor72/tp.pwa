from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


def role_for_status(status: str | None) -> UserRole:
    normalized = status.strip().lower() if isinstance(status, str) else ""
    if normalized == UserRole.ADMIN:
        return UserRole.ADMIN
    if normalized == UserRole.MANAGER:
        return UserRole.MANAGER
    return UserRole.USER
