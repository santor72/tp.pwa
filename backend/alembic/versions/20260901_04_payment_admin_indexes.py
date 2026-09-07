"""indexes for payment admin registry"""
from alembic import op

revision = "20260901_04"
down_revision = "20260901_03"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_index("ix_payment_transactions_status_paid_at", "payment_transactions", ["status", "paid_at"])
    op.create_index("ix_payment_transactions_employee_paid_at", "payment_transactions", ["employee_external_id", "paid_at"])

def downgrade() -> None:
    op.drop_index("ix_payment_transactions_employee_paid_at", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_status_paid_at", table_name="payment_transactions")
