"""Keep the technician surname with a connection completion operation."""
from alembic import op
import sqlalchemy as sa


revision = '20260918_01'
down_revision = '20260917_02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'connection_completion_operations',
        sa.Column('technician_last_name', sa.String(length=255), nullable=False, server_default=''),
    )
    op.alter_column('connection_completion_operations', 'technician_last_name', server_default=None)


def downgrade():
    op.drop_column('connection_completion_operations', 'technician_last_name')
