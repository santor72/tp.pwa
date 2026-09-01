import pytest

from app.errors import PaymentStateError
from app.repositories import validate_payment_status_transition


@pytest.mark.parametrize("current,target", [
    ("draft", "resolving_client"),
    ("payment_created", "link_created"),
    ("send_failed", "send_queued"),
    ("sent", "paid"),
    ("expired", "paid"),
    ("paid", "paid"),
])
def test_valid_payment_status_transitions(current: str, target: str) -> None:
    validate_payment_status_transition(current, target)


@pytest.mark.parametrize("current,target", [
    ("paid", "send_queued"),
    ("expired", "resolving_client"),
    ("invoice_created", "resolving_client"),
    ("send_failed", "product_added"),
])
def test_invalid_or_stale_payment_status_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(PaymentStateError):
        validate_payment_status_transition(current, target)
