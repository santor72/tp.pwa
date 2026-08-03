from uuid import uuid4

import pytest

from app.actors import Actor
from app.errors import PermissionDeniedError
from app.permissions import has_permission, require_permission


def actor(permissions: dict) -> Actor:
    return Actor(user_id=uuid4(), techportal_user_id="7", channel="pwa", permissions=permissions)


def test_nested_permission_requires_explicit_true() -> None:
    assert has_permission({"client": {"create": True}}, "client", "create")
    assert not has_permission({"client": {"create": "w"}}, "client", "create")
    assert not has_permission({"client": {}}, "client", "create")
    assert not has_permission({}, "client", "create")


def test_require_permission_rejects_actor_without_permission() -> None:
    with pytest.raises(PermissionDeniedError, match="Недостаточно прав"):
        require_permission(actor({"client": {"create": False}}), "client", "create")
