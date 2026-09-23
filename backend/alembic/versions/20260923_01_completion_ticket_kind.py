"""Allow the completion workflow to handle repairs as well as connections."""
from alembic import op
import sqlalchemy as sa


revision = '20260923_01'
down_revision = '20260918_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'connection_completion_operations',
        sa.Column('ticket_kind', sa.String(length=16), nullable=False, server_default='connection'),
    )
    op.alter_column('connection_completion_operations', 'ticket_kind', server_default=None)


def downgrade():
    op.drop_column('connection_completion_operations', 'ticket_kind')
