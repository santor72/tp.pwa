"""Store the optional payment-client e-mail for durable retry processing."""

from alembic import op
import sqlalchemy as sa


revision = "20260901_03"
down_revision = "20260901_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_transactions", sa.Column("email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_transactions", "email")
