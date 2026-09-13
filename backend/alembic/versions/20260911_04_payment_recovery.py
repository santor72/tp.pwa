"""Persist client resolution and safe external-write recovery descriptors."""
from alembic import op
import sqlalchemy as sa

revision = '20260911_04'
down_revision = '20260911_03'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payment_transactions', sa.Column('client_resolution', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column('payment_external_writes', sa.Column('recovery', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column('payment_external_writes', 'recovery')
    op.drop_column('payment_transactions', 'client_resolution')
