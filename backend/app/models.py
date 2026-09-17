import uuid
from datetime import datetime

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    techportal_user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(128))
    permissions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MessengerIdentity(Base):
    __tablename__ = "messenger_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_messenger_identity_active_provider_user", "provider", "provider_user_id", unique=True, postgresql_where=revoked_at.is_(None)),
        Index("uq_messenger_identity_active_user_provider", "user_id", "provider", unique=True, postgresql_where=revoked_at.is_(None)),
    )


class MessengerLinkToken(Base):
    __tablename__ = "messenger_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    employee_external_id: Mapped[str | None] = mapped_column(String(128))
    employee_display_name: Mapped[str | None] = mapped_column(String(255))
    address_id: Mapped[int | None] = mapped_column(Integer)
    address_text: Mapped[str | None] = mapped_column(Text)
    apartment: Mapped[str | None] = mapped_column(String(64))
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_title: Mapped[str] = mapped_column(String(255), nullable=False)
    catalog_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    second_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_normalized: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    bitrix_lead_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_contact_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_invoice_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    bitrix_product_row_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_payment_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    bitrix_payment_account_number: Mapped[str | None] = mapped_column(String(128))
    bitrix_pay_system_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_pay_system_name: Mapped[str | None] = mapped_column(String(255))
    payment_product_linked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_url: Mapped[str | None] = mapped_column(Text)
    payment_short_url: Mapped[str | None] = mapped_column(Text)
    payment_qr: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="draft")
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="draft")
    send_status: Mapped[str | None] = mapped_column(String(64))
    candidate_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    client_resolution: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    formation_timeline_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    formation_activity_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paid_timeline_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paid_activity_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_payment_transactions_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_payment_transactions_user_created", "user_id", "created_at"),
        Index("ix_payment_transactions_expires", "expires_at"),
        Index("ix_payment_transactions_created", "created_at"),
    )


class PaymentTransactionEvent(Base):
    __tablename__ = "payment_transaction_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    external_request_id: Mapped[str | None] = mapped_column(String(255))
    safe_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_payment_events_transaction_created", "transaction_id", "created_at"),)


class PaymentOperationSpan(Base):
    __tablename__ = "payment_operation_spans"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    __table_args__ = (Index("ix_payment_spans_transaction_started", "transaction_id", "started_at"),)


class PaymentJob(Base):
    __tablename__ = "payment_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_safe_error: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("transaction_id", "kind", "generation", name="uq_payment_job_generation"),
        Index("ix_payment_jobs_due", "kind", "state", "available_at"),
        Index("ix_payment_jobs_finished", "finished_at", "id"),
    )


class PaymentOutbox(Base):
    __tablename__ = "payment_outbox"
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    publisher_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    publisher_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stream_message_id: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (Index("ix_payment_outbox_due", "completed_at", "last_published_at", "next_publish_at"),)


class PaymentJobArchive(Base):
    """Compact immutable identity of completed work, not a runnable job."""
    __tablename__ = 'payment_job_archive'
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('payment_transactions.id', ondelete='CASCADE'), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentExecutionLease(Base):
    __tablename__ = "payment_execution_leases"
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_transactions.id", ondelete="CASCADE"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_payment_execution_lease_expiry", "lease_until"),)


class PaymentExternalWrite(Base):
    __tablename__ = "payment_external_writes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    marker: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remote_id: Mapped[str | None] = mapped_column(String(128))
    recovery: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("transaction_id", "marker", name="uq_payment_write_marker"),)


class PaymentRequestBudget(Base):
    __tablename__ = "payment_request_budgets"
    integration_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    next_slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentEventQuarantine(Base):
    __tablename__ = "payment_event_quarantine"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("stream", "message_id", name="uq_payment_quarantine_message"),)


class PaymentCallbackInbox(Base):
    __tablename__ = "payment_callback_inbox"
    payment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRuntimeControl(Base):
    __tablename__ = "payment_runtime_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)


class PaymentRuntimeMember(Base):
    __tablename__ = "payment_runtime_members"
    owner: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    healthy_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionCompletionOperation(Base):
    __tablename__ = 'connection_completion_operations'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    ticket_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    day: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    technician_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    technician_name: Mapped[str] = mapped_column(String(255), nullable=False)
    feature_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    feature_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    external_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    techportal_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    gis_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    photos: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    techportal_comment: Mapped[str | None] = mapped_column(Text)
    completion_status: Mapped[str] = mapped_column(String(32), nullable=False, default='prepared')
    gis_status: Mapped[str] = mapped_column(String(32), nullable=False, default='not_requested')
    gis_report_id: Mapped[str | None] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('ix_connection_completion_gis_due', 'gis_status', 'next_attempt_at'),
        Index('ix_connection_completion_ticket_created', 'ticket_id', 'created_at'),
    )
