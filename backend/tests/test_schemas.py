import pytest
from pydantic import ValidationError

from app.schemas import DomofonCreateRequest


def test_domofon_create_requires_integer_fields() -> None:
    request = DomofonCreateRequest(locid=1, field_flat=42, field_podezd=3, client_name="Иванов Иван")
    assert request.field_flat == 42
    assert request.field_podezd == 3
    assert request.phone is None

    request_with_phone = DomofonCreateRequest(
        locid=1,
        field_flat=42,
        field_podezd=3,
        client_name="Иванов Иван",
        phone="  +79990000000  ",
    )
    assert request_with_phone.phone == "+79990000000"

    with pytest.raises(ValidationError):
        DomofonCreateRequest(locid=1, field_flat="4.2", field_podezd=3, client_name="Иванов Иван")

    with pytest.raises(ValidationError):
        DomofonCreateRequest(locid=1, field_flat="42", field_podezd=3, client_name="Иванов Иван")
