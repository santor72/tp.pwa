import uuid
from datetime import datetime

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, func
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
