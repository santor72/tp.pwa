import asyncio
import json
from typing import Any

import httpx

from app.config import Settings
from app.errors import Bitrix24Error, PaymentsNotConfiguredError
from app.payment_telemetry import span, current_trace
from app.payment_execution import current_execution


READ_METHOD_PREFIXES = (
    "crm.duplicate.", "crm.lead.get", "crm.lead.list", "crm.contact.get", "crm.contact.list",
    "crm.lead.contact.items.get", "crm.item.get", "crm.item.list",
    "crm.item.productrow.list", "crm.item.payment.get", "crm.item.payment.list",
    "crm.item.payment.product.list", "catalog.product.get", "catalog.price.list",
    "crm.timeline.comment.list", "crm.activity.list", "salescenter.payment.getPublicUrl",
)


class Bitrix24Client:
    """Small safe REST adapter. Secret webhook URLs never enter exceptions or logs."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.bx24_timeout_seconds)
        self.limiter = None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def call(self, method: str, params: dict[str, Any] | None = None, *, read: bool | None = None) -> Any:
        execution = current_execution.get()
        is_read = method.startswith(READ_METHOD_PREFIXES) if read is None else read
        params = params or {}
        if execution is not None:
            await execution.check()
            if not is_read:
                cached = await execution.before_write(method, params)
                if cached is not None:
                    return json.loads(cached)
        try:
            result = await self._call(method, params, read=is_read)
        except Bitrix24Error as exc:
            if execution is not None and not is_read and exc.code in {
                "BX24_RATE_LIMITED", "BX24_VALIDATION_FAILED", "BX24_ACCESS_DENIED", "BX24_AUTH_FAILED", "BX24_HTTP_ERROR",
            }:
                await execution.repository.reject_write(execution.claim, execution.marker(method, params))
            raise
        if execution is not None and not is_read:
            await execution.after_write(method, params, result)
        return result

    async def _call(self, method: str, params: dict[str, Any] | None = None, *, read: bool | None = None) -> Any:
        base = self._settings.bx24_webhook_url
        if not base:
            raise PaymentsNotConfiguredError()
        is_read = method.startswith(READ_METHOD_PREFIXES) if read is None else read
        attempts = 3 if is_read else 1
        for attempt in range(attempts):
            if self.limiter is not None:
                await self.limiter.acquire()
            execution = current_execution.get()
            if execution is not None:
                await execution.check()
            try:
                with span(method, "rest", attempt=attempt + 1) as timing:
                    response = await self._client.post(f"{base}/{method}.json", json=params or {})
                    if current_trace.get() is not None:
                        timing["http_status"] = response.status_code
                        try:
                            body = response.json()
                            timing["api_error"] = isinstance(body, dict) and bool(body.get("error"))
                        except ValueError:
                            timing["invalid_json"] = True
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if is_read and attempt + 1 < attempts:
                    with span("rest_backoff", "wait", attempt=attempt + 1):
                        await asyncio.sleep(0.2 * (2 ** attempt))
                    continue
                raise Bitrix24Error() from exc
            if response.status_code == 429 or response.status_code >= 500:
                if response.status_code == 429 and self.limiter is not None:
                    await self.limiter.limited()
                if is_read and attempt + 1 < attempts:
                    with span("rest_backoff", "wait", attempt=attempt + 1):
                        await asyncio.sleep(0.2 * (2 ** attempt))
                    continue
                code = "BX24_RATE_LIMITED" if response.status_code == 429 else "BX24_UNAVAILABLE"
                raise Bitrix24Error(code)
            if response.status_code in {401, 404}:
                raise Bitrix24Error("BX24_AUTH_FAILED", "Не удалось авторизоваться в Битрикс24")
            if response.status_code == 403:
                raise Bitrix24Error("BX24_ACCESS_DENIED", "Битрикс24 отклонил операцию", 403)
            if not response.is_success:
                raise Bitrix24Error("BX24_HTTP_ERROR", "Битрикс24 отклонил запрос")
            try:
                payload = response.json()
            except ValueError as exc:
                raise Bitrix24Error("BX24_RESPONSE_INVALID", "Битрикс24 вернул некорректный ответ") from exc
            if not isinstance(payload, dict):
                raise Bitrix24Error("BX24_RESPONSE_INVALID", "Битрикс24 вернул некорректный ответ")
            if payload.get("error"):
                error = str(payload.get("error"))
                if error in {"INVALID_CREDENTIALS", "NO_AUTH_FOUND"}:
                    raise Bitrix24Error("BX24_AUTH_FAILED", "Не удалось авторизоваться в Битрикс24")
                if error in {"ACCESS_DENIED", "ERROR_METHOD_NOT_FOUND"}:
                    raise Bitrix24Error("BX24_ACCESS_DENIED", "Недостаточно прав интеграции Битрикс24", 403)
                raise Bitrix24Error("BX24_VALIDATION_FAILED", "Битрикс24 отклонил данные операции", 422)
            return payload.get("result")
        raise Bitrix24Error()

    async def duplicate_ids(self, entity_type: str, phones: str | list[str]) -> list[int]:
        values = [phones] if isinstance(phones, str) else phones
        result = await self.call("crm.duplicate.findbycomm", {"entity_type": entity_type, "type": "PHONE", "values": values})
        values = result.get(entity_type.upper(), []) if isinstance(result, dict) else []
        return list(dict.fromkeys(int(value) for value in values))

    async def leads_by_address(self, address_id: int, apartment: str) -> list[dict[str, Any]]:
        result = await self.call("crm.lead.list", {
            "filter": {"=PARENT_ID_1032": address_id, "=UF_CRM_1737982006": apartment},
            "select": ["ID", "TITLE", "NAME", "SECOND_NAME", "LAST_NAME", "PHONE"],
        })
        return result if isinstance(result, list) else []

    async def lead_contacts(self, lead_id: int) -> list[dict[str, Any]]:
        result = await self.call("crm.lead.contact.items.get", {"id": lead_id})
        return result if isinstance(result, list) else []

    async def find_by_origin(self, entity_type: str, origin_id: str) -> list[dict[str, Any]]:
        method = "crm.lead.list" if entity_type == "lead" else "crm.contact.list"
        result = await self.call(method, {"filter": {"=ORIGINATOR_ID": "TECHPORTAL_PWA", "=ORIGIN_ID": origin_id}, "select": ["ID"]})
        return result if isinstance(result, list) else []

    async def create_contact(self, fields: dict[str, Any]) -> int:
        return int(await self.call("crm.contact.add", {"fields": fields}, read=False))

    async def create_lead(self, fields: dict[str, Any]) -> int:
        return int(await self.call("crm.lead.add", {"fields": fields}, read=False))

    async def get_contact(self, contact_id: int) -> dict[str, Any]:
        result = await self.call("crm.contact.get", {"id": contact_id})
        return result if isinstance(result, dict) else {}

    async def get_lead(self, lead_id: int) -> dict[str, Any]:
        result = await self.call("crm.lead.get", {"id": lead_id})
        return result if isinstance(result, dict) else {}

    async def update_contact(self, contact_id: int, fields: dict[str, Any]) -> None:
        await self.call("crm.contact.update", {"id": contact_id, "fields": fields}, read=False)

    async def update_lead(self, lead_id: int, fields: dict[str, Any]) -> None:
        await self.call("crm.lead.update", {"id": lead_id, "fields": fields}, read=False)

    async def add_contact_to_lead(self, lead_id: int, contact_id: int) -> None:
        await self.call("crm.lead.contact.add", {"id": lead_id, "fields": {"CONTACT_ID": contact_id, "IS_PRIMARY": "Y"}}, read=False)

    async def ensure_contact_on_lead(self, lead_id: int, contact_id: int) -> None:
        contacts = await self.lead_contacts(lead_id)
        linked_ids = {int(item.get("CONTACT_ID", item.get("ID", 0))) for item in contacts}
        if contact_id not in linked_ids:
            await self.add_contact_to_lead(lead_id, contact_id)

    async def create_invoice(self, fields: dict[str, Any]) -> int:
        result = await self.call("crm.item.add", {"entityTypeId": 31, "fields": fields}, read=False)
        return self._id(result, "item")

    async def find_invoice_by_xml_id(self, xml_id: str) -> int | None:
        result = await self.call("crm.item.list", {"entityTypeId": 31, "filter": {"=xmlId": xml_id}, "select": ["id", "xmlId"]})
        items = result.get("items", []) if isinstance(result, dict) else []
        return int(items[0]["id"]) if isinstance(items, list) and items else None

    async def add_product_row(self, invoice_id: int, fields: dict[str, Any]) -> int:
        result = await self.call("crm.item.productrow.add", {"fields": {"ownerId": invoice_id, "ownerType": "SI", **fields}}, read=False)
        return self._id(result, "productRow")

    async def find_product_row(self, invoice_id: int, product_id: int) -> int | None:
        result = await self.call("crm.item.productrow.list", {"filter": {"=ownerId": invoice_id, "=ownerType": "SI"}})
        rows = result.get("productRows", result.get("items", [])) if isinstance(result, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if int(row.get("productId", row.get("PRODUCT_ID", 0))) == product_id:
                return int(row.get("id", row.get("ID")))
        return None

    async def create_payment(self, invoice_id: int) -> int:
        result = await self.call("crm.item.payment.add", {"entityTypeId": 31, "entityId": invoice_id}, read=False)
        return self._id(result, "payment")

    async def delete_payment(self, payment_id: int) -> None:
        await self.call("crm.item.payment.delete", {"id": payment_id}, read=False)

    async def find_payment(self, invoice_id: int) -> int | None:
        result = await self.call("crm.item.payment.list", {"entityTypeId": 31, "entityId": invoice_id})
        payments = result if isinstance(result, list) else result.get("payments", result.get("items", [])) if isinstance(result, dict) else []
        return int(payments[0].get("id", payments[0].get("ID"))) if isinstance(payments, list) and payments else None

    async def payment_product_linked(self, payment_id: int, row_id: int) -> bool:
        result = await self.call("crm.item.payment.product.list", {"paymentId": payment_id, "filter": {}})
        items = result if isinstance(result, list) else result.get("products", result.get("items", [])) if isinstance(result, dict) else []
        return any(
            int(item.get("entityId", item.get("rowId", item.get("ROW_ID", 0)))) == row_id
            for item in items if isinstance(item, dict)
        )

    async def add_payment_product(self, payment_id: int, row_id: int) -> int:
        return int(await self.call("crm.item.payment.product.add", {"paymentId": payment_id, "rowId": row_id, "quantity": 1}, read=False))

    async def payment_public_url(self, payment_id: int) -> dict[str, str | None]:
        result = await self.call("salescenter.payment.getPublicUrl", {"id": payment_id})
        if not isinstance(result, dict):
            raise Bitrix24Error("BX24_RESPONSE_INVALID", "Битрикс24 не вернул платёжную ссылку")
        nested = result.get("payment") if isinstance(result.get("payment"), dict) else result
        url = nested.get("url") or nested.get("URL")
        short = nested.get("shortUrl") or nested.get("shortURL") or nested.get("SHORT_URL")
        qr = nested.get("qr") or nested.get("qrCode") or nested.get("QR")
        if not url and not short:
            raise Bitrix24Error("BX24_RESPONSE_INVALID", "Битрикс24 не вернул платёжную ссылку")
        qr_value = str(qr) if qr else None
        if qr_value and not qr_value.startswith(("data:", "http://", "https://")):
            qr_value = f"data:image/png;base64,{qr_value}"
        return {"url": str(url) if url else None, "short_url": str(short) if short else None, "qr": qr_value}

    async def get_payment(self, payment_id: int) -> dict[str, Any]:
        result = await self.call("crm.item.payment.get", {"id": payment_id})
        return result if isinstance(result, dict) else {}

    async def update_invoice(self, invoice_id: int, fields: dict[str, Any]) -> None:
        await self.call("crm.item.update", {"entityTypeId": 31, "id": invoice_id, "fields": fields}, read=False)

    async def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        result = await self.call("crm.item.get", {"entityTypeId": 31, "id": invoice_id})
        item = result.get("item", result) if isinstance(result, dict) else {}
        return item if isinstance(item, dict) else {}

    async def timeline_comment(self, entity_type: str, entity_id: int, comment: str) -> None:
        await self.call("crm.timeline.comment.add", {"fields": {"ENTITY_TYPE": entity_type, "ENTITY_ID": entity_id, "COMMENT": comment}}, read=False)

    async def timeline_has_marker(self, entity_type: str, entity_id: int, marker: str) -> bool:
        result = await self.call("crm.timeline.comment.list", {
            "filter": {"ENTITY_TYPE": entity_type, "ENTITY_ID": entity_id},
            "select": ["ID", "COMMENT"],
            "order": {"ID": "DESC"},
        })
        comments = result if isinstance(result, list) else result.get("items", []) if isinstance(result, dict) else []
        return any(marker in str(item.get("COMMENT", item.get("comment", ""))) for item in comments if isinstance(item, dict))

    async def add_activity(self, fields: dict[str, Any]) -> int | None:
        """Create a CRM activity.

        Some Bitrix24 portals return ``null`` for a successfully created
        activity, so an ID is useful when supplied but is not a success
        criterion for this write-only operation.
        """
        result = await self.call("crm.activity.add", {"fields": fields}, read=False)
        return int(result) if result is not None else None

    async def activity_id_by_marker(self, marker: str) -> int | None:
        result = await self.call("crm.activity.list", {
            "filter": {"%DESCRIPTION": marker}, "select": ["ID", "DESCRIPTION"], "order": {"ID": "DESC"},
        })
        items = result if isinstance(result, list) else []
        for item in items:
            if isinstance(item, dict) and marker in str(item.get("DESCRIPTION", "")):
                return int(item["ID"])
        return None

    async def complete_activity(self, activity_id: int) -> None:
        await self.call("crm.activity.update", {"id": activity_id, "fields": {"COMPLETED": "Y"}}, read=False)

    async def activity_has_marker(self, marker: str) -> bool:
        return await self.activity_id_by_marker(marker) is not None

    @staticmethod
    def _id(result: Any, key: str) -> int:
        if isinstance(result, (int, str)):
            return int(result)
        if isinstance(result, dict):
            nested = result.get(key)
            if isinstance(nested, dict) and nested.get("id") is not None:
                return int(nested["id"])
            if result.get("id") is not None:
                return int(result["id"])
        raise Bitrix24Error("BX24_RESPONSE_INVALID", "Битрикс24 не вернул ID созданной сущности")
