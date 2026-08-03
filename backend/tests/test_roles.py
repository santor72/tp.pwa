import pytest

from app.roles import UserRole, role_for_status
from app.schemas import UserProfile


@pytest.mark.parametrize(
    ("status", "role"),
    [
        ("admin", UserRole.ADMIN),
        (" manager ", UserRole.MANAGER),
        ("active", UserRole.USER),
        (None, UserRole.USER),
    ],
)
def test_role_is_derived_from_techportal_status(status: str | None, role: UserRole) -> None:
    assert role_for_status(status) == role
    assert UserProfile(id=7, email="tech@example.test", status=status).role == role


def test_session_profile_serializes_derived_role() -> None:
    profile = UserProfile(id=7, email="tech@example.test", status="manager")
    assert profile.model_dump(mode="json")["role"] == "manager"
