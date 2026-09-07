from app.capabilities import capabilities_for
from app.config import Settings
from app.roles import UserRole


def test_capabilities_combine_role_configuration_and_techportal_permission() -> None:
    admin = capabilities_for(UserRole.ADMIN, {"tickets": {"execution": True}}, False)
    manager = capabilities_for(UserRole.MANAGER, {}, False)
    user = capabilities_for(UserRole.USER, {}, False)
    visible_for_all = capabilities_for(UserRole.USER, {}, True)

    assert admin.model_dump() == {"payments": True, "messenger_settings": True, "all_tickets": True, "payment_admin": True}
    assert manager.model_dump() == {"payments": False, "messenger_settings": False, "all_tickets": True, "payment_admin": False}
    assert user.model_dump() == {"payments": False, "messenger_settings": False, "all_tickets": False, "payment_admin": False}
    assert visible_for_all.messenger_settings is True


def test_messenger_show_accepts_the_current_and_legacy_environment_names() -> None:
    assert Settings(MESSENGER_SHOW="true").messenger_show is True
    assert Settings(MESSENDGER_SHOW="true").messenger_show is True
