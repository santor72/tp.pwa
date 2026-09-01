"""create payment transaction persistence

Revision ID: 20260901_01
Revises: 20260731_01
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260901_01"
down_revision = "20260731_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("employee_external_id", sa.String(128)),
        sa.Column("employee_display_name", sa.String(255)),
        sa.Column("address_id", sa.Integer()),
        sa.Column("address_text", sa.Text()),
        sa.Column("apartment", sa.String(64)),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_title", sa.String(255), nullable=False),
        sa.Column("catalog_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("actual_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("first_name", sa.String(255), nullable=False),
        sa.Column("second_name", sa.String(255)),
        sa.Column("last_name", sa.String(255), nullable=False),
        sa.Column("phone_normalized", sa.String(32), nullable=False),
        sa.Column("bitrix_lead_id", sa.Integer()),
        sa.Column("bitrix_contact_id", sa.Integer()),
        sa.Column("bitrix_invoice_id", sa.Integer(), unique=True),
        sa.Column("bitrix_product_row_id", sa.Integer()),
        sa.Column("bitrix_payment_id", sa.Integer(), unique=True),
        sa.Column("payment_product_linked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_url", sa.Text()),
        sa.Column("payment_short_url", sa.Text()),
        sa.Column("payment_qr", sa.Text()),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("current_step", sa.String(64), nullable=False),
        sa.Column("send_status", sa.String(64)),
        sa.Column("candidate_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("formation_timeline_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("formation_activity_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paid_timeline_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paid_activity_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payment_transactions_status_next_attempt", "payment_transactions", ["status", "next_attempt_at"])
    op.create_index("ix_payment_transactions_user_created", "payment_transactions", ["user_id", "created_at"])
    op.create_index("ix_payment_transactions_expires", "payment_transactions", ["expires_at"])
    op.create_table(
        "payment_transaction_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("external_request_id", sa.String(255)),
        sa.Column("safe_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_payment_events_transaction_created", "payment_transaction_events", ["transaction_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_events_transaction_created", table_name="payment_transaction_events")
    op.drop_table("payment_transaction_events")
    op.drop_index("ix_payment_transactions_expires", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_user_created", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_status_next_attempt", table_name="payment_transactions")
    op.drop_table("payment_transactions")
