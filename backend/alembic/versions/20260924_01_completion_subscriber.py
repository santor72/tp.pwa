"""Store subscriber data for durable GIS report delivery."""
from alembic import op
import sqlalchemy as sa


revision = '20260924_01'
down_revision = '20260923_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'connection_completion_operations',
        sa.Column('subscriber', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column('connection_completion_operations', 'subscriber', server_default=None)


def downgrade():
    op.drop_column('connection_completion_operations', 'subscriber')
