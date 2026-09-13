"""Payment processing mode interlock and runtime heartbeats."""
from alembic import op
import sqlalchemy as sa

revision = '20260911_03'
down_revision = '20260911_02'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table('payment_runtime_control', sa.Column('id', sa.Integer(), primary_key=True),
                           sa.Column('mode', sa.String(16), nullable=False))
    op.bulk_insert(table, [{'id': 1, 'mode': 'legacy'}])
    op.create_table('payment_runtime_members', sa.Column('owner', sa.String(128), primary_key=True),
        sa.Column('role', sa.String(32), nullable=False), sa.Column('host', sa.String(128), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False), sa.Column('healthy_until', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('payment_runtime_members')
    op.drop_table('payment_runtime_control')
