from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import Bitrix24Error
from app.payment_client_resolver import AmbiguousClient, PaymentClientResolver


def transaction(**values):
    defaults = {
        "id": uuid4(), "phone_normalized": "+79991234567", "address_id": None,
        "address_text": None, "apartment": None, "first_name": "Иван", "second_name": None,
        "last_name": "Иванов", "product_title": "Услуга", "candidate_snapshot": [], "email": None,
    }
    return SimpleNamespace(**(defaults | values))


class FakeBitrix:
    def __init__(self) -> None:
        self.contacts: list[int] = []
        self.leads: list[int] = []
        self.address_leads: list[dict] = []
        self.relations: list[dict] = []
        self.origins: dict[str, list[dict]] = {}
        self.contact_records: dict[int, dict] = {}
        self.lead_records: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.duplicate_calls: list[tuple] = []

    async def duplicate_ids(self, entity_type, phones):
        self.duplicate_calls.append((entity_type, phones))
        return self.contacts if entity_type == "CONTACT" else self.leads
    async def leads_by_address(self, address_id, apartment): self.calls.append(("address", address_id, apartment)); return self.address_leads
    async def lead_contacts(self, lead_id): return self.relations
    async def find_by_origin(self, entity_type, origin_id): return self.origins.get(f"{entity_type}:{origin_id}", [])
    async def create_lead(self, fields): self.calls.append(("create_lead", fields)); return 20
    async def create_contact(self, fields): self.calls.append(("create_contact", fields)); self.contact_records[30] = dict(fields); return 30
    async def get_contact(self, contact_id): return self.contact_records.get(contact_id, {})
    async def get_lead(self, lead_id): return self.lead_records.get(lead_id, {})
    async def update_contact(self, contact_id, fields): self.calls.append(("update_contact", contact_id, fields)); self.contact_records.setdefault(contact_id, {}).update(fields)
    async def update_lead(self, lead_id, fields): self.calls.append(("update_lead", lead_id, fields)); self.lead_records.setdefault(lead_id, {}).update(fields)
    async def add_contact_to_lead(self, lead_id, contact_id): self.calls.append(("link", lead_id, contact_id))
    async def ensure_contact_on_lead(self, lead_id, contact_id):
        if contact_id not in {int(item.get("CONTACT_ID", item.get("ID", 0))) for item in self.relations}:
            await self.add_contact_to_lead(lead_id, contact_id)


@pytest.mark.asyncio
async def test_resolver_prefers_contact_and_does_not_search_lead() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7]
    result = await PaymentClientResolver(Settings(), bitrix).resolve(transaction())
    assert result.contact_id == 7 and result.lead_id is None
    assert bitrix.calls == []


@pytest.mark.asyncio
async def test_resolver_requires_selection_for_multiple_contacts() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7, 8]
    with pytest.raises(AmbiguousClient) as caught:
        await PaymentClientResolver(Settings(), bitrix).resolve(transaction())
    assert [item.entity_id for item in caught.value.candidates] == [7, 8]


@pytest.mark.asyncio
async def test_resolver_requires_selection_for_multiple_phone_leads() -> None:
    bitrix = FakeBitrix(); bitrix.leads = [11, 12]
    with pytest.raises(AmbiguousClient) as caught:
        await PaymentClientResolver(Settings(), bitrix).resolve(transaction())
    assert [(item.entity_type, item.entity_id) for item in caught.value.candidates] == [("lead", 11), ("lead", 12)]


@pytest.mark.asyncio
async def test_resolver_uses_legacy_russian_phone_variants() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7]
    result = await PaymentClientResolver(Settings(), bitrix).resolve(transaction(phone_normalized="+79255807004"))
    assert result.contact_id == 7
    assert bitrix.duplicate_calls == [("CONTACT", ["+79255807004", "89255807004", "79255807004"])]


@pytest.mark.asyncio
async def test_resolver_uses_primary_contact_of_existing_lead_without_changes() -> None:
    bitrix = FakeBitrix(); bitrix.leads = [11]; bitrix.relations = [
        {"CONTACT_ID": 2, "IS_PRIMARY": "N", "SORT": 10}, {"CONTACT_ID": 3, "IS_PRIMARY": "Y", "SORT": 20},
    ]
    result = await PaymentClientResolver(Settings(), bitrix).resolve(transaction())
    assert (result.contact_id, result.lead_id) == (3, 11)
    assert bitrix.calls == []


@pytest.mark.asyncio
async def test_resolver_appends_new_email_to_found_contact_without_replacing_existing() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7]
    bitrix.contact_records[7] = {"EMAIL": [{"VALUE": "old@example.com", "VALUE_TYPE": "HOME"}]}
    await PaymentClientResolver(Settings(), bitrix).resolve(transaction(email="new@example.com"))
    assert bitrix.calls == [("update_contact", 7, {"EMAIL": [
        {"VALUE": "old@example.com", "VALUE_TYPE": "HOME"},
        {"VALUE": "new@example.com", "VALUE_TYPE": "WORK"},
    ]})]


@pytest.mark.asyncio
async def test_resolver_does_not_duplicate_existing_email_and_updates_lead_and_contact() -> None:
    bitrix = FakeBitrix(); bitrix.leads = [11]; bitrix.relations = [{"CONTACT_ID": 3, "IS_PRIMARY": "Y"}]
    bitrix.lead_records[11] = {"EMAIL": []}
    bitrix.contact_records[3] = {"EMAIL": [{"VALUE": "NEW@EXAMPLE.COM", "VALUE_TYPE": "WORK"}]}
    await PaymentClientResolver(Settings(), bitrix).resolve(transaction(email="new@example.com"))
    assert bitrix.calls == [("update_lead", 11, {"EMAIL": [{"VALUE": "new@example.com", "VALUE_TYPE": "WORK"}]})]


@pytest.mark.asyncio
async def test_resolver_uses_lowest_sort_when_lead_has_no_primary_contact() -> None:
    bitrix = FakeBitrix(); bitrix.leads = [11]; bitrix.relations = [
        {"CONTACT_ID": 2, "IS_PRIMARY": "N", "SORT": 20},
        {"CONTACT_ID": 3, "IS_PRIMARY": "N", "SORT": 10},
    ]
    result = await PaymentClientResolver(Settings(), bitrix).resolve(transaction())
    assert (result.contact_id, result.lead_id) == (3, 11)
    assert bitrix.calls == []


@pytest.mark.asyncio
async def test_resolver_searches_exact_address_then_creates_contact() -> None:
    bitrix = FakeBitrix(); bitrix.address_leads = [{"ID": "15", "TITLE": "Квартира"}]
    bitrix.lead_records[15] = {"PARENT_ID_1032": "103"}
    result = await PaymentClientResolver(Settings(), bitrix).resolve(transaction(address_id=103, address_text="Полный адрес", apartment="12А"))
    assert (result.contact_id, result.lead_id) == (30, 15)
    assert ("address", 103, "12А") in bitrix.calls
    assert ("link", 15, 30) in bitrix.calls
    fields = next(call[1] for call in bitrix.calls if call[0] == "create_contact")
    assert fields["PARENT_ID_1032"] == 103
    assert fields["ADDRESS"] == "Полный адрес"
    assert fields["UF_CRM_6797820003612"] == "12А"


@pytest.mark.asyncio
async def test_phone_contact_without_address_is_linked_to_selected_address_lead_and_enriched() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7]; bitrix.address_leads = [{"ID": "15", "TITLE": "Квартира"}]
    bitrix.contact_records[7] = {"PARENT_ID_1032": None, "ADDRESS": None, "UF_CRM_6797820003612": None}
    tx = transaction(address_id=4297, address_text="Уездная, 4", apartment="235")

    result = await PaymentClientResolver(Settings(_env_file=None), bitrix).resolve(tx)

    assert (result.contact_id, result.lead_id) == (7, 15)
    assert ("link", 15, 7) in bitrix.calls
    assert bitrix.contact_records[7] == {
        "PARENT_ID_1032": 4297,
        "ADDRESS": "Уездная, 4",
        "UF_CRM_6797820003612": "235",
    }


@pytest.mark.asyncio
async def test_phone_contact_with_other_address_requires_manual_choice() -> None:
    bitrix = FakeBitrix(); bitrix.contacts = [7]; bitrix.address_leads = [{"ID": "15"}]
    bitrix.contact_records[7] = {"PARENT_ID_1032": "103", "ADDRESS": "Старый адрес", "UF_CRM_6797820003612": "12"}
    tx = transaction(address_id=4297, address_text="Уездная, 4", apartment="235")
    resolver = PaymentClientResolver(Settings(_env_file=None), bitrix)

    with pytest.raises(AmbiguousClient) as caught:
        await resolver.resolve(tx)

    assert [candidate.action for candidate in caught.value.candidates] == ["keep_contact_address", "apply_selected_address"]
    tx.candidate_snapshot = [candidate.model_dump() for candidate in caught.value.candidates]
    result = await resolver.resolve_selected(tx, "contact", 7, "apply_selected_address")
    assert (result.contact_id, result.lead_id) == (7, 15)
    assert bitrix.contact_records[7]["PARENT_ID_1032"] == 4297
    assert bitrix.contact_records[7]["UF_CRM_6797820003612"] == "235"


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_id", [None, "", 0, "0"])
async def test_new_contact_omits_empty_lead_address(parent_id) -> None:
    bitrix = FakeBitrix(); bitrix.leads = [15]
    bitrix.lead_records[15] = {"PARENT_ID_1032": parent_id}
    await PaymentClientResolver(Settings(), bitrix).resolve(transaction(address_id=103, apartment="12"))
    fields = next(call[1] for call in bitrix.calls if call[0] == "create_contact")
    assert "PARENT_ID_1032" not in fields


@pytest.mark.asyncio
async def test_selected_lead_contact_inherits_lead_address_not_form_address() -> None:
    bitrix = FakeBitrix()
    bitrix.lead_records[15] = {"PARENT_ID_1032": "4297"}
    tx = transaction(address_id=103, apartment="349", candidate_snapshot=[{"entity_type": "lead", "entity_id": 15}])
    result = await PaymentClientResolver(Settings(), bitrix).resolve_selected(tx, "lead", 15)
    fields = next(call[1] for call in bitrix.calls if call[0] == "create_contact")
    assert fields["PARENT_ID_1032"] == "4297"
    assert (result.contact_id, result.lead_id) == (30, 15)


@pytest.mark.asyncio
async def test_resolver_requires_selection_for_multiple_address_leads() -> None:
    bitrix = FakeBitrix(); bitrix.address_leads = [{"ID": "15", "TITLE": "Квартира"}, {"ID": "16", "TITLE": "Другая карточка"}]
    with pytest.raises(AmbiguousClient) as caught:
        await PaymentClientResolver(Settings(), bitrix).resolve(
            transaction(address_id=103, address_text="Полный адрес", apartment="12А"),
        )
    assert [item.entity_id for item in caught.value.candidates] == [15, 16]
    assert all(item.matched_by == "address" for item in caught.value.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [False, True])
async def test_address_match_appends_phone_to_lead_and_contact(selected) -> None:
    bitrix = FakeBitrix(); bitrix.address_leads = [{"ID": "15"}]
    bitrix.relations = [{"CONTACT_ID": 3, "IS_PRIMARY": "Y"}]
    old = {"ID": "90", "VALUE": "+79000000000", "VALUE_TYPE": "HOME"}
    bitrix.lead_records[15] = {"PHONE": [dict(old)]}
    bitrix.contact_records[3] = {"PHONE": [dict(old)]}
    tx = transaction(address_id=103, apartment="12", candidate_snapshot=[{"entity_type": "lead", "entity_id": 15, "matched_by": "address"}])
    resolver = PaymentClientResolver(Settings(), bitrix)
    if selected:
        await resolver.resolve_selected(tx, "lead", 15)
    else:
        await resolver.resolve(tx)
    expected = [old, {"VALUE": tx.phone_normalized, "VALUE_TYPE": "WORK"}]
    assert bitrix.lead_records[15]["PHONE"] == expected
    assert bitrix.contact_records[3]["PHONE"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["+7 (999) 123-45-67", "89991234567", "79991234567"])
async def test_address_match_does_not_duplicate_equivalent_phone(phone) -> None:
    bitrix = FakeBitrix(); bitrix.address_leads = [{"ID": "15"}]
    bitrix.relations = [{"CONTACT_ID": 3}]
    bitrix.lead_records[15] = {"PHONE": [{"VALUE": phone, "VALUE_TYPE": "HOME"}]}
    bitrix.contact_records[3] = {"PHONE": [{"VALUE": phone, "VALUE_TYPE": "HOME"}]}
    await PaymentClientResolver(Settings(), bitrix).resolve(transaction(address_id=103, apartment="12"))
    assert not any(call[0] == "update_lead" for call in bitrix.calls)
    assert bitrix.contact_records[3]["PHONE"] == [{"VALUE": phone, "VALUE_TYPE": "HOME"}]
    assert bitrix.contact_records[3]["PARENT_ID_1032"] == 103
    assert bitrix.contact_records[3]["UF_CRM_6797820003612"] == "12"


@pytest.mark.asyncio
async def test_new_lead_contains_address_fields_only_when_address_selected() -> None:
    for selected in (False, True):
        bitrix = FakeBitrix()
        tx = transaction(**({"address_id": 103, "address_text": "Полный адрес", "apartment": "офис 3"} if selected else {}))
        result = await PaymentClientResolver(Settings(bx24_new_lead_status_id="UC_NEW"), bitrix).resolve(tx)
        assert (result.contact_id, result.lead_id) == (30, 20)
        fields = next(call[1] for call in bitrix.calls if call[0] == "create_lead")
        assert fields["STATUS_ID"] == "UC_NEW"
        assert ("PARENT_ID_1032" in fields) is selected
        assert ("ADDRESS" in fields) is selected
        assert ("UF_CRM_1737982006" in fields) is selected


@pytest.mark.asyncio
async def test_resolver_recovers_lead_and_contact_after_unknown_create_results() -> None:
    class RecoveringBitrix(FakeBitrix):
        def __init__(self):
            super().__init__(); self.lead_writes = 0; self.contact_writes = 0

        async def create_lead(self, fields):
            self.lead_writes += 1
            origin_id = fields["ORIGIN_ID"]
            self.origins[f"lead:{origin_id}"] = [{"ID": "20"}]
            raise Bitrix24Error()

        async def create_contact(self, fields):
            self.contact_writes += 1
            origin_id = fields["ORIGIN_ID"]
            self.origins[f"contact:{origin_id}"] = [{"ID": "30"}]
            raise Bitrix24Error()

    bitrix = RecoveringBitrix(); resolver = PaymentClientResolver(Settings(), bitrix); tx = transaction()
    with pytest.raises(Bitrix24Error):
        await resolver.resolve(tx)
    with pytest.raises(Bitrix24Error):
        await resolver.resolve(tx)
    result = await resolver.resolve(tx)
    assert (result.contact_id, result.lead_id) == (30, 20)
    assert bitrix.lead_writes == 1 and bitrix.contact_writes == 1
    assert ("link", 20, 30) in bitrix.calls
