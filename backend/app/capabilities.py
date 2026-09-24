from app.actors import Actor
from app.permissions import has_permission
from app.roles import UserRole
from app.schemas import Capabilities


def gis_visible_for_techportal_role(status: str | None, roles: str) -> bool:
    allowed = {item.strip().lower() for item in roles.split(',') if item.strip()}
    return bool(status and status.strip().lower() in allowed)


def capabilities_for(role: UserRole, permissions: dict, messenger_show: bool, *, techportal_status: str | None = None,
                     gis_visible_techportal_roles: str = '', gis_tikets_visible_techportal_roles: str = '', connection_photos: bool = False,
                     gis_photos: bool = False) -> Capabilities:
    return Capabilities(
        payments=has_permission(permissions, "tickets", "execution"),
        gis=gis_visible_for_techportal_role(techportal_status, gis_visible_techportal_roles),
        gis_tickets=gis_visible_for_techportal_role(techportal_status, gis_tikets_visible_techportal_roles),
        connection_photos=connection_photos,
        gis_photos=gis_photos,
        messenger_settings=messenger_show or role is UserRole.ADMIN,
        all_tickets=role in {UserRole.ADMIN, UserRole.MANAGER},
        payment_admin=role is UserRole.ADMIN,
    )


def capabilities_for_actor(actor: Actor, messenger_show: bool) -> Capabilities:
    return capabilities_for(actor.role, actor.permissions or {}, messenger_show)
