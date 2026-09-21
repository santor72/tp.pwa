from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.models import PaymentTransaction
from app.schemas import PaymentCandidate
from app.payment_execution import current_execution

CONTACT_APARTMENT_FIELD = "UF_CRM_6797820003612"


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
        saved = getattr(transaction, 'client_resolution', {}) or {}
        if saved.get('type') == 'new':
            return await self._create_client(transaction)
        if saved.get('type') == 'lead':
            return await self.resolve_lead(transaction, saved['lead_id'], add_phone=saved.get('add_phone', False))
        if saved.get('type') == 'contact_address':
            return await self._use_contact_with_selected_address(transaction, saved['contact_id'], saved.get('lead_id'))
        if saved.get('type') == 'contact':
            await self._ensure_email('contact', saved['contact_id'], transaction.email)
            return ResolvedClient(contact_id=saved['contact_id'])
        phone_variants = self._phone_variants(transaction.phone_normalized)
        contact_ids = await self._bitrix.duplicate_ids("CONTACT", phone_variants)
        if len(contact_ids) > 1:
            raise AmbiguousClient([self._candidate("contact", item) for item in contact_ids])
        if contact_ids:
            contact_id = contact_ids[0]
            if transaction.address_id is not None:
                return await self._resolve_phone_contact_with_selected_address(transaction, contact_id)
            await self._remember({'type': 'contact', 'contact_id': contact_id})
            await self._ensure_email("contact", contact_id, transaction.email)
            return ResolvedClient(contact_id=contact_id)

        lead_ids = await self._bitrix.duplicate_ids("LEAD", phone_variants)
        if len(lead_ids) > 1:
            raise AmbiguousClient([self._candidate("lead", item) for item in lead_ids])
        if lead_ids:
            return await self.resolve_lead(transaction, lead_ids[0])

        lead_id = await self._selected_address_lead(transaction)
        if lead_id is not None:
            return await self.resolve_lead(transaction, lead_id, add_phone=True)

        return await self._create_client(transaction)

    async def resolve_selected(self, transaction: PaymentTransaction, entity_type: str, entity_id: int, action: str | None = None) -> ResolvedClient:
        allowed = {(item.get("entity_type"), int(item.get("entity_id", 0)), item.get("action")) for item in transaction.candidate_snapshot}
        if (entity_type, entity_id, action) not in allowed:
            raise ValueError("candidate is not in snapshot")
        if action == 'keep_contact_address':
            await self._remember({'type': 'contact', 'contact_id': entity_id})
            await self._ensure_email("contact", entity_id, transaction.email)
            return ResolvedClient(contact_id=entity_id)
        if action == 'apply_selected_address':
            return await self._use_contact_with_selected_address(transaction, entity_id)
        if entity_type == "contact":
            await self._remember({'type': 'contact', 'contact_id': entity_id})
            await self._ensure_email("contact", entity_id, transaction.email)
            return ResolvedClient(contact_id=entity_id)
        add_phone = any(item.get("entity_type") == "lead" and int(item.get("entity_id", 0)) == entity_id and item.get("matched_by") == "address" for item in transaction.candidate_snapshot)
        return await self.resolve_lead(transaction, entity_id, add_phone=add_phone)

    async def _selected_address_lead(self, transaction: PaymentTransaction) -> int | None:
        if transaction.address_id is None or not transaction.apartment:
            return None
        leads = await self._bitrix.leads_by_address(transaction.address_id, transaction.apartment)
        if len(leads) > 1:
            raise AmbiguousClient([
                PaymentCandidate(entity_type="lead", entity_id=int(item["ID"]), display_name=str(item.get("TITLE") or f"Лид #{item['ID']}"), matched_by="address")
                for item in leads
            ])
        return int(leads[0]['ID']) if leads else None

    async def _resolve_phone_contact_with_selected_address(self, transaction: PaymentTransaction, contact_id: int) -> ResolvedClient:
        contact = await self._bitrix.get_contact(contact_id)
        if self._contact_address_conflicts(transaction, contact):
            raise AmbiguousClient([
                PaymentCandidate(entity_type='contact', entity_id=contact_id, display_name='Оставить адрес из карточки контакта', matched_by='address_conflict', action='keep_contact_address'),
                PaymentCandidate(entity_type='contact', entity_id=contact_id, display_name='Использовать выбранный адрес', matched_by='address_conflict', action='apply_selected_address'),
            ])
        return await self._use_contact_with_selected_address(transaction, contact_id)

    async def _use_contact_with_selected_address(self, transaction: PaymentTransaction, contact_id: int, lead_id: int | None = None) -> ResolvedClient:
        if transaction.address_id is not None and lead_id is None:
            lead_id = await self._selected_address_lead(transaction)
        await self._remember({'type': 'contact_address', 'contact_id': contact_id, 'lead_id': lead_id})
        await self._ensure_email('contact', contact_id, transaction.email)
        await self._apply_contact_address(transaction, contact_id)
        if lead_id is not None:
            await self._bitrix.ensure_contact_on_lead(lead_id, contact_id)
        return ResolvedClient(contact_id=contact_id, lead_id=lead_id)

    @staticmethod
    def _contact_address_conflicts(transaction: PaymentTransaction, contact: dict[str, Any]) -> bool:
        if transaction.address_id is None:
            return False
        parent = contact.get('PARENT_ID_1032')
        if parent not in (None, '', 0, '0') and str(parent) != str(transaction.address_id):
            return True
        address = str(contact.get('ADDRESS') or '').strip()
        selected_address = str(transaction.address_text or '').strip()
        if address and selected_address and address.casefold() != selected_address.casefold():
            return True
        apartment = str(contact.get(CONTACT_APARTMENT_FIELD) or '').strip()
        return bool(apartment and transaction.apartment and apartment.casefold() != transaction.apartment.strip().casefold())

    async def _apply_contact_address(self, transaction: PaymentTransaction, contact_id: int) -> None:
        if transaction.address_id is None:
            return
        contact = await self._bitrix.get_contact(contact_id)
        fields = {
            'PARENT_ID_1032': transaction.address_id,
            'ADDRESS': transaction.address_text or '',
            CONTACT_APARTMENT_FIELD: transaction.apartment or '',
        }
        if any(str(contact.get(key) or '') != str(value) for key, value in fields.items()):
            await self._bitrix.update_contact(contact_id, fields)

    async def resolve_lead(self, transaction: PaymentTransaction, lead_id: int, *, add_phone: bool = False) -> ResolvedClient:
        await self._remember({'type': 'lead', 'lead_id': lead_id, 'add_phone': add_phone})
        await self._ensure_email("lead", lead_id, transaction.email)
        contacts = await self._bitrix.lead_contacts(lead_id)
        if contacts:
            primary = [item for item in contacts if item.get("IS_PRIMARY") in {True, "Y", 1, "1"}]
            chosen = min(primary or contacts, key=lambda item: int(item.get("SORT", 2_147_483_647)))
            contact_id = int(chosen.get("CONTACT_ID", chosen.get("ID")))
            await self._ensure_email("contact", contact_id, transaction.email)
            if add_phone:
                await self._ensure_phone("contact", contact_id, transaction.phone_normalized)
                await self._ensure_phone("lead", lead_id, transaction.phone_normalized)
                contact = await self._bitrix.get_contact(contact_id)
                if self._contact_address_conflicts(transaction, contact):
                    raise AmbiguousClient([
                        PaymentCandidate(entity_type='contact', entity_id=contact_id, display_name='Оставить адрес из карточки контакта', matched_by='address_conflict', action='keep_contact_address'),
                        PaymentCandidate(entity_type='contact', entity_id=contact_id, display_name='Использовать выбранный адрес', matched_by='address_conflict', action='apply_selected_address'),
                    ])
                await self._apply_contact_address(transaction, contact_id)
            return ResolvedClient(contact_id=contact_id, lead_id=lead_id)
        contact_id = await self._find_or_create_contact(transaction, f"{transaction.id}:lead:{lead_id}", lead_id=lead_id, use_transaction_address=add_phone)
        await self._bitrix.ensure_contact_on_lead(lead_id, contact_id)
        if add_phone:
            await self._ensure_phone("contact", contact_id, transaction.phone_normalized)
            await self._ensure_phone("lead", lead_id, transaction.phone_normalized)
        return ResolvedClient(contact_id=contact_id, lead_id=lead_id)

    async def _create_client(self, transaction: PaymentTransaction) -> ResolvedClient:
        await self._remember({'type': 'new'})
        origin_id = str(transaction.id)
        existing = await self._bitrix.find_by_origin("lead", origin_id)
        if existing:
            lead_id = int(existing[0]["ID"])
            await self._ensure_email("lead", lead_id, transaction.email)
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
            if transaction.email:
                fields["EMAIL"] = [self._email_value(transaction.email)]
            if transaction.address_id is not None:
                fields.update({
                    "PARENT_ID_1032": transaction.address_id,
                    "ADDRESS": transaction.address_text,
                    "UF_CRM_1737982006": transaction.apartment,
                })
            lead_id = await self._bitrix.create_lead(fields)
        contact_id = await self._find_or_create_contact(transaction, origin_id, use_transaction_address=transaction.address_id is not None)
        await self._bitrix.ensure_contact_on_lead(lead_id, contact_id)
        return ResolvedClient(contact_id=contact_id, lead_id=lead_id)

    async def _remember(self, resolution):
        execution = current_execution.get()
        if execution is not None:
            await execution.repository.set_resolution(execution.claim, resolution)

    async def _find_or_create_contact(self, transaction: PaymentTransaction, origin_id: str, *, lead_id: int | None = None, use_transaction_address: bool = False) -> int:
        existing = await self._bitrix.find_by_origin("contact", origin_id)
        if existing:
            contact_id = int(existing[0]["ID"])
            await self._ensure_email("contact", contact_id, transaction.email)
            return contact_id
        fields: dict[str, Any] = {
            "NAME": transaction.first_name,
            "SECOND_NAME": transaction.second_name or "",
            "LAST_NAME": transaction.last_name,
            "PHONE": [{"VALUE": transaction.phone_normalized, "VALUE_TYPE": "WORK"}],
            "ORIGINATOR_ID": "TECHPORTAL_PWA",
            "ORIGIN_ID": origin_id,
        }
        if transaction.email:
            fields["EMAIL"] = [self._email_value(transaction.email)]
        if use_transaction_address and transaction.address_id is not None:
            fields.update({
                "PARENT_ID_1032": transaction.address_id,
                "ADDRESS": transaction.address_text or "",
                CONTACT_APARTMENT_FIELD: transaction.apartment or "",
            })
        elif lead_id is not None:
            lead = await self._bitrix.get_lead(lead_id)
            parent_id = lead.get("PARENT_ID_1032")
            if parent_id not in (None, "", 0, "0"):
                fields["PARENT_ID_1032"] = parent_id
        return await self._bitrix.create_contact(fields)

    async def _ensure_email(self, entity_type: str, entity_id: int, email: str | None) -> None:
        """Append a submitted e-mail to the CRM multi-field without replacing values."""
        if not email:
            return
        entity = await (self._bitrix.get_contact(entity_id) if entity_type == "contact" else self._bitrix.get_lead(entity_id))
        current = entity.get("EMAIL", [])
        emails = [dict(item) for item in current if isinstance(item, dict)] if isinstance(current, list) else []
        if any(str(item.get("VALUE", "")).strip().casefold() == email.casefold() for item in emails):
            return
        emails.append(self._email_value(email))
        if entity_type == "contact":
            await self._bitrix.update_contact(entity_id, {"EMAIL": emails})
        else:
            await self._bitrix.update_lead(entity_id, {"EMAIL": emails})

    async def _ensure_phone(self, entity_type: str, entity_id: int, phone: str) -> None:
        entity = await (self._bitrix.get_contact(entity_id) if entity_type == "contact" else self._bitrix.get_lead(entity_id))
        current = entity.get("PHONE", [])
        phones = [dict(item) for item in current if isinstance(item, dict)] if isinstance(current, list) else []
        normalized = self._phone_key(phone)
        if any(self._phone_key(str(item.get("VALUE", ""))) == normalized for item in phones):
            return
        phones.append({"VALUE": phone, "VALUE_TYPE": "WORK"})
        if entity_type == "contact":
            await self._bitrix.update_contact(entity_id, {"PHONE": phones})
        else:
            await self._bitrix.update_lead(entity_id, {"PHONE": phones})

    @staticmethod
    def _phone_key(phone: str) -> str:
        digits = "".join(char for char in phone if char.isdecimal())
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        return digits

    @staticmethod
    def _email_value(email: str) -> dict[str, str]:
        return {"VALUE": email, "VALUE_TYPE": "WORK"}

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
