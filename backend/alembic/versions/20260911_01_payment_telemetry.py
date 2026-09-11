"""Payment formation timing, separate from business events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260911_01"
down_revision = "20260901_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("payment_operation_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False))
    op.create_index("ix_payment_spans_transaction_started", "payment_operation_spans", ["transaction_id", "started_at"])
    op.create_index("ix_payment_transactions_created", "payment_transactions", ["created_at"])


def downgrade():
    op.drop_index("ix_payment_transactions_created", table_name="payment_transactions")
    op.drop_table("payment_operation_spans")
