"""Bounded, best-effort payment traces. Never collect request/response bodies."""
import asyncio
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger(__name__)
current_trace = ContextVar("payment_trace", default=None)
current_parent = ContextVar("payment_span_parent", default=None)


def elapsed(started_at, name, *, duration_ms=None, **metadata):
    trace = current_trace.get()
    if trace is None or len(trace.rows) >= 500:
        return
    started_at = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at
    if duration_ms is None:
        duration_ms = (datetime.now(UTC) - started_at).total_seconds() * 1000
    trace.rows.append(dict(id=uuid4(), parent_id=current_parent.get(), name=name, kind="interval",
        started_at=started_at, duration_ms=max(0, duration_ms), outcome="ok", details=metadata))


class Trace:
    def __init__(self):
        self.run_id = uuid4()
        self.transaction_id = None
        self.rows = []

    async def save(self, repository):
        if not self.transaction_id or not self.rows:
            return
        try:
            async with asyncio.timeout(2):
                await repository.save_spans(self.transaction_id, self.run_id, self.rows)
        except Exception:
            # No exception text: drivers can include SQL parameters and secrets.
            logger.warning("payment.telemetry.write_failed")


@contextmanager
def span(name, kind="step", **metadata):
    trace = current_trace.get()
    if trace is None:
        yield metadata
        return
    row = dict(id=uuid4(), parent_id=current_parent.get(), name=name, kind=kind,
               started_at=datetime.now(UTC), outcome="ok", details=metadata)
    token = current_parent.set(row["id"])
    started = perf_counter()
    try:
        yield metadata
    except BaseException as exc:
        row["outcome"] = "error"
        metadata["error_type"] = type(exc).__name__
        from app.errors import ApiError
        if isinstance(exc, ApiError):
            metadata["error_code"] = exc.code
        raise
    finally:
        row["duration_ms"] = round((perf_counter() - started) * 1000, 3)
        if metadata.get("http_status", 200) >= 400 or metadata.get("api_error") or metadata.get("invalid_json"):
            row["outcome"] = "error"
        if metadata.get("skipped"):
            row["outcome"] = "skipped"
        current_parent.reset(token)
        if len(trace.rows) < 500:
            trace.rows.append(row)


def measured(name, kind="step"):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with span(name, kind):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate


def operation(name):
    """Independent trace per create/process/selection; context never stored on client."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(self, *args, **kwargs):
            if not self._settings.payment_telemetry_enabled:
                return await fn(self, *args, **kwargs)
            if current_trace.get() is not None:
                with span(name, "operation"):
                    return await fn(self, *args, **kwargs)
            trace = Trace()
            token = current_trace.set(trace)
            parent_token = current_parent.set(None)
            if name in {"worker", "retry_schedule"}:
                trace.transaction_id = args[0] if args else kwargs["transaction_id"]
            try:
                with span(name, "operation"):
                    return await fn(self, *args, **kwargs)
            finally:
                current_trace.reset(token)
                current_parent.reset(parent_token)
                await trace.save(self._repository)
        return wrapped
    return decorate
