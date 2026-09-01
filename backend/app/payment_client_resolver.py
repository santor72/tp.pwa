from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentTransaction
from app.schemas import PaymentCandidate


@dataclass(frozen=True, slots=True)
class ResolvedClient:
    contact_id: int
    lead_id: int | None = None


class AmbiguousClient(Exception):
    def __init__(self, candidates: list[PaymentCandidate]) -> None:
        self.candidates = candidates
        super().__init__("ambiguous CRM client")


class PaymentClientResolver:
    def __init__(self, settings: Settings, bitrix: Bitrix24Client) -> None:
        self._settings = settings
        self._bitrix = bitrix

    async def resolve(self, transaction: PaymentTransaction) -> ResolvedClient:
        phone_variants = self._phone_variants(transaction.phone_normalized)
        contact_ids = await self._bitrix.duplicate_ids("CONTACT", phone_variants)
        if len(contact_ids) > 1:
            raise AmbiguousClient([self._candidate("contact", item) for item in contact_ids])
        if contact_ids:
            return ResolvedClient(contact_id=contact_ids[0])

        lead_ids = await self._bitrix.duplicate_ids("LEAD", phone_variants)
        if len(lead_ids) > 1:
            raise AmbiguousClient([self._candidate("lead", item) for item in lead_ids])
        if lead_ids:
            return await self.resolve_lead(transaction, lead_ids[0])

        if transaction.address_id is not None and transaction.apartment:
            leads = await self._bitrix.leads_by_address(transaction.address_id, transaction.apartment)
            if len(leads) > 1:
                raise AmbiguousClient([
                    PaymentCandidate(entity_type="lead", entity_id=int(item["ID"]), display_name=str(item.get("TITLE") or f"Лид #{item['ID']}"))
                    for item in leads
                ])
            if leads:
                return await self.resolve_lead(transaction, int(leads[0]["ID"]))

        return await self._create_client(transaction)

    async def resolve_selected(self, transaction: PaymentTransaction, entity_type: str, entity_id: int) -> ResolvedClient:
        allowed = {(item.get("entity_type"), int(item.get("entity_id", 0))) for item in transaction.candidate_snapshot}
        if (entity_type, entity_id) not in allowed:
            raise ValueError("candidate is not in snapshot")
        if entity_type == "contact":
            return ResolvedClient(contact_id=entity_id)
        return await self.resolve_lead(transaction, entity_id)

    async def resolve_lead(self, transaction: PaymentTransaction, lead_id: int) -> ResolvedClient:
        contacts = await self._bitrix.lead_contacts(lead_id)
        if contacts:
            primary = [item for item in contacts if item.get("IS_PRIMARY") in {True, "Y", 1, "1"}]
            chosen = min(primary or contacts, key=lambda item: int(item.get("SORT", 2_147_483_647)))
            contact_id = int(chosen.get("CONTACT_ID", chosen.get("ID")))
            return ResolvedClient(contact_id=contact_id, lead_id=lead_id)
        contact_id = await self._find_or_create_contact(transaction, f"{transaction.id}:lead:{lead_id}")
        await self._bitrix.ensure_contact_on_lead(lead_id, contact_id)
        return ResolvedClient(contact_id=contact_id, lead_id=lead_id)

    async def _create_client(self, transaction: PaymentTransaction) -> ResolvedClient:
        origin_id = str(transaction.id)
        existing = await self._bitrix.find_by_origin("lead", origin_id)
        if existing:
            lead_id = int(existing[0]["ID"])
        else:
            fields: dict[str, Any] = {
                "TITLE": f"Оплата: {transaction.product_title}",
                "NAME": transaction.first_name,
                "SECOND_NAME": transaction.second_name or "",
                "LAST_NAME": transaction.last_name,
                "PHONE": [{"VALUE": transaction.phone_normalized, "VALUE_TYPE": "WORK"}],
                "STATUS_ID": self._settings.bx24_new_lead_status_id,
                "ORIGINATOR_ID": "TECHPORTAL_PWA",
                "ORIGIN_ID": origin_id,
            }
            if transaction.address_id is not None:
                fields.update({
                    "PARENT_ID_1032": transaction.address_id,
                    "ADDRESS": transaction.address_text,
                    "UF_CRM_1737982006": transaction.apartment,
                })
            lead_id = await self._bitrix.create_lead(fields)
        contact_id = await self._find_or_create_contact(transaction, origin_id)
        await self._bitrix.ensure_contact_on_lead(lead_id, contact_id)
        return ResolvedClient(contact_id=contact_id, lead_id=lead_id)

    async def _find_or_create_contact(self, transaction: PaymentTransaction, origin_id: str) -> int:
        existing = await self._bitrix.find_by_origin("contact", origin_id)
        if existing:
            return int(existing[0]["ID"])
        return await self._bitrix.create_contact({
            "NAME": transaction.first_name,
            "SECOND_NAME": transaction.second_name or "",
            "LAST_NAME": transaction.last_name,
            "PHONE": [{"VALUE": transaction.phone_normalized, "VALUE_TYPE": "WORK"}],
            "ORIGINATOR_ID": "TECHPORTAL_PWA",
            "ORIGIN_ID": origin_id,
        })

    @staticmethod
    def _candidate(entity_type: str, entity_id: int) -> PaymentCandidate:
        return PaymentCandidate(entity_type=entity_type, entity_id=entity_id, display_name=f"{entity_type.title()} #{entity_id}")

    @staticmethod
    def _phone_variants(phone: str) -> list[str]:
        """Search legacy Russian CRM numbers alongside canonical E.164."""
        if phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit():
            national = phone[2:]
            return [phone, f"8{national}", f"7{national}"]
        return [phone]
