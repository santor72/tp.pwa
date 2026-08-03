from app.actors import Actor
from app.permissions import has_permission
from app.roles import UserRole
from app.schemas import Capabilities


def capabilities_for(role: UserRole, permissions: dict, messenger_show: bool) -> Capabilities:
    return Capabilities(
        domofon=has_permission(permissions, "client", "create"),
        messenger_settings=messenger_show or role is UserRole.ADMIN,
        all_tickets=role in {UserRole.ADMIN, UserRole.MANAGER},
    )


def capabilities_for_actor(actor: Actor, messenger_show: bool) -> Capabilities:
    return capabilities_for(actor.role, actor.permissions or {}, messenger_show)
