from app.capabilities import capabilities_for
from app.config import Settings
from app.dependencies import require_gis_access
from app.errors import PermissionDeniedError
from app.roles import UserRole
from app.schemas import SessionData, UserProfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest


def test_capabilities_combine_role_configuration_and_techportal_permission() -> None:
    admin = capabilities_for(UserRole.ADMIN, {"tickets": {"execution": True}}, False)
    manager = capabilities_for(UserRole.MANAGER, {}, False)
    user = capabilities_for(UserRole.USER, {}, False)
    visible_for_all = capabilities_for(UserRole.USER, {}, True)

    assert admin.model_dump() == {"payments": True, "gis": False, "messenger_settings": True, "all_tickets": True, "payment_admin": True}
    assert manager.model_dump() == {"payments": False, "gis": False, "messenger_settings": False, "all_tickets": True, "payment_admin": False}
    assert user.model_dump() == {"payments": False, "gis": False, "messenger_settings": False, "all_tickets": False, "payment_admin": False}
    assert visible_for_all.messenger_settings is True


def test_gis_capability_uses_the_configured_techportal_statuses() -> None:
    allowed = capabilities_for(UserRole.USER, {}, False, techportal_status='Installer', gis_visible_techportal_roles='admin, installer')
    denied = capabilities_for(UserRole.ADMIN, {}, False, techportal_status='admin', gis_visible_techportal_roles='installer')

    assert allowed.gis is True
    assert denied.gis is False


def test_gis_backend_access_requires_a_configured_techportal_status() -> None:
    session = SessionData(
        user=UserProfile(id=7, email='tech@example.test', status='viewer'), csrf_token='csrf',
        created_at=datetime.now(UTC), absolute_expires_at=datetime.now(UTC) + timedelta(hours=1), internal_user_id=uuid4(),
    )

    with pytest.raises(PermissionDeniedError):
        require_gis_access(session, Settings(gis_visible_techportal_roles='installer,manager'))


def test_messenger_show_accepts_the_current_and_legacy_environment_names() -> None:
    assert Settings(MESSENGER_SHOW="true").messenger_show is True
    assert Settings(MESSENDGER_SHOW="true").messenger_show is True
