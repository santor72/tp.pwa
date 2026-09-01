from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import PaymentCreateRequest


def payment_payload(**overrides: object) -> dict:
    return {
        "idempotency_key": str(uuid4()), "product_id": 10, "first_name": " Иван ",
        "last_name": " Иванов ", "phone": "8 (999) 123-45-67", "amount": "1500.00", **overrides,
    }


def test_payment_create_normalizes_phone_and_accepts_string_apartment() -> None:
    request = PaymentCreateRequest.model_validate(payment_payload(
        address={"locid": 1, "loctext": " Адрес "}, apartment=" 12А ",
    ))
    assert request.phone == "+79991234567"
    assert request.apartment == "12А"
    assert request.address and request.address.loctext == "Адрес"


def test_payment_create_requires_apartment_with_address() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate(payment_payload(address={"locid": 1, "loctext": "Адрес"}))


def test_payment_create_rejects_numeric_amount_and_apartment_without_address() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate(payment_payload(amount=1500.0))
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate(payment_payload(apartment="42"))
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate(payment_payload(amount="NaN"))
