"""store payment reconciliation metadata

Revision ID: 20260901_02
Revises: 20260901_01
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_02"
down_revision = "20260901_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_transactions", sa.Column("bitrix_payment_account_number", sa.String(128)))
    op.add_column("payment_transactions", sa.Column("bitrix_pay_system_id", sa.Integer()))
    op.add_column("payment_transactions", sa.Column("bitrix_pay_system_name", sa.String(255)))


def downgrade() -> None:
    op.drop_column("payment_transactions", "bitrix_pay_system_name")
    op.drop_column("payment_transactions", "bitrix_pay_system_id")
    op.drop_column("payment_transactions", "bitrix_payment_account_number")
