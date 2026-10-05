"""Persist GIS work marks for ticket report delivery and retries."""
from alembic import op
import sqlalchemy as sa

revision = '20261005_01'
down_revision = '20260930_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('connection_completion_operations', sa.Column('closure_full', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('connection_completion_operations', sa.Column('from_scratch', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('connection_completion_operations', 'from_scratch')
    op.drop_column('connection_completion_operations', 'closure_full')
