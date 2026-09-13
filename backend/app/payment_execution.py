"""Execution ownership shared by event consumers and database fallback."""
import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass


class PaymentLeaseLost(Exception):
    pass


class PaymentWriteUnknown(Exception):
    """Do not repeat a write whose remote outcome has not been reconciled."""


current_execution = ContextVar("payment_execution", default=None)


@dataclass
class PaymentExecution:
    repository: object
    claim: object
    scope: str = "formation"

    async def check(self):
        if not await self.repository.owns(self.claim):
            raise PaymentLeaseLost()

    def marker(self, method, params):
        encoded = json.dumps([self.scope, method, params], sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def before_write(self, method, params):
        from app.payment_write_recovery import recovery_spec
        return await self.repository.begin_write(self.claim, self.marker(method, params), method, recovery_spec(method, params))

    async def after_write(self, method, params, result):
        await self.repository.confirm_write(self.claim, self.marker(method, params), safe_write_result(result))


def safe_write_result(result):
    """Only IDs/booleans/null, never CRM response bodies containing client data."""
    if result is None or type(result) in {int, bool}:
        return json.dumps(result)
    if isinstance(result, str) and result.isdecimal():
        return json.dumps(result)
    if isinstance(result, dict):
        if str(result.get("id", "")).isdecimal():
            return json.dumps({"id": result["id"]})
        for key in ("item", "payment", "productRow"):
            nested = result.get(key)
            if isinstance(nested, dict) and str(nested.get("id", "")).isdecimal():
                return json.dumps({key: {"id": nested["id"]}})
    # Successful but nonstandard results must not be stored verbatim.
    return "null"
