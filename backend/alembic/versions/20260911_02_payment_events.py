"""Durable payment jobs, transactional outbox and execution ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260911_02"
down_revision = "20260911_01"
branch_labels = None
depends_on = None


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def payment_id(primary=False):
    return sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False, primary_key=primary)


def upgrade():
    op.create_table("payment_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True), payment_id(),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("last_safe_error", sa.String(128)),
        sa.Column("details", sa.JSON(), nullable=False), created(), timestamp("started_at", True), timestamp("finished_at", True),
        sa.UniqueConstraint("transaction_id", "kind", "generation", name="uq_payment_job_generation"))
    op.create_index("ix_payment_jobs_due", "payment_jobs", ["kind", "state", "available_at"])
    op.create_table("payment_outbox",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("payment_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("event_type", sa.String(64), nullable=False), sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.Column("next_publish_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("publisher_token", UUID(as_uuid=True)), timestamp("publisher_lease_until", True),
        timestamp("last_published_at", True), sa.Column("stream_message_id", sa.String(64)),
        timestamp("completed_at", True), created())
    op.create_index("ix_payment_outbox_due", "payment_outbox", ["completed_at", "last_published_at", "next_publish_at"])
    op.create_table("payment_execution_leases", payment_id(True),
        sa.Column("owner", sa.String(128), nullable=False), sa.Column("fencing_token", sa.BigInteger(), nullable=False), timestamp("lease_until"))
    op.create_index("ix_payment_execution_lease_expiry", "payment_execution_leases", ["lease_until"])
    op.create_table("payment_external_writes", sa.Column("id", UUID(as_uuid=True), primary_key=True), payment_id(),
        sa.Column("marker", sa.String(128), nullable=False), sa.Column("method", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False), sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("remote_id", sa.String(128)), created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("transaction_id", "marker", name="uq_payment_write_marker"))
    op.create_table("payment_request_budgets", sa.Column("integration_key", sa.String(128), primary_key=True),
        timestamp("next_slot_at"), timestamp("cooldown_until"))
    op.create_table("payment_event_quarantine", sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("stream", sa.String(128), nullable=False), sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False), created(),
        sa.UniqueConstraint("stream", "message_id", name="uq_payment_quarantine_message"))
    op.create_table("payment_callback_inbox", sa.Column("payment_id", sa.BigInteger(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), timestamp("expires_at"))


def downgrade():
    for name in ("payment_callback_inbox", "payment_event_quarantine", "payment_request_budgets",
                 "payment_external_writes", "payment_execution_leases", "payment_outbox", "payment_jobs"):
        op.drop_table(name)
