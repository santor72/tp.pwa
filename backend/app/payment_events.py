"""Versioned, PII-free Redis transport; Postgres decides whether work may run."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import ResponseError


class PaymentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: UUID
    job_id: UUID
    transaction_id: UUID
    event_type: Literal["payment.formation_requested", "payment.reconciliation_requested"]
    schema_version: Literal[1] = 1
    generation: int = Field(ge=1)


class PaymentStream:
    def __init__(self, redis, prefix: str, kind: str):
        if kind not in {"formation", "reconciliation"}:
            raise ValueError("unknown payment stream")
        if not prefix or len(prefix) > 64 or not all(c.isascii() and (c.isalnum() or c in '-_:') for c in prefix):
            raise ValueError("invalid payment stream prefix")
        self.redis = redis
        self.key = f"{prefix}:payments:{kind}"
        self.group = f"{kind}-workers-v1"
        self.kind = kind

    async def ensure_group(self):
        try:
            await self.redis.xgroup_create(self.key, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise

    async def publish(self, event: PaymentEvent):
        if event.event_type != f"payment.{self.kind}_requested":
            raise ValueError("event routed to wrong stream")
        # No MAXLEN: deleting unprocessed entries requires coordinated recovery.
        return await self.redis.xadd(self.key, {"event": event.model_dump_json()})

    async def read(self, consumer: str, *, block_ms=1000):
        if block_ms <= 0:
            raise ValueError("blocking read must have a finite timeout")
        try:
            rows = await self.redis.xreadgroup(self.group, consumer, {self.key: ">"}, count=1, block=block_ms)
        except ResponseError as exc:
            if str(exc).startswith("NOGROUP"):
                await self.ensure_group()
                return []
            raise
        return rows[0][1] if rows else []

    async def reclaim(self, consumer: str, *, idle_ms: int, cursor="0-0"):
        try:
            result = await self.redis.xautoclaim(self.key, self.group, consumer, idle_ms, start_id=cursor, count=10)
        except ResponseError as exc:
            if str(exc).startswith("NOGROUP"):
                await self.ensure_group()
                return "0-0", []
            raise
        # Redis may report deleted pending IDs separately. DB recovery covers them.
        return result[0], result[1]

    async def ack(self, message_id):
        return await self.redis.xack(self.key, self.group, message_id)

    def decode(self, fields):
        if set(fields) != {"event"} or len(fields["event"]) > 2048:
            raise ValueError("invalid event envelope")
        event = PaymentEvent.model_validate_json(fields["event"])
        if event.event_type != f"payment.{self.kind}_requested":
            raise ValueError("event routed to wrong stream")
        return event
