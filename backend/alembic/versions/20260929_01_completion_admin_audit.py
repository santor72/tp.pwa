"""Store completion identity snapshots and integration responses for admin audit."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20260929_01'
down_revision = '20260924_01'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('connection_completion_operations', sa.Column('subscriber_login', sa.String(255)))
    op.add_column('connection_completion_operations', sa.Column('subscriber_address', sa.Text()))
    op.add_column('connection_completion_operations', sa.Column('techportal_completed_at', sa.DateTime(timezone=True)))
    op.create_table('completion_integration_attempts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('operation_id', UUID(as_uuid=True), sa.ForeignKey('connection_completion_operations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('system', sa.String(32), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('http_status', sa.Integer()),
        sa.Column('response_body', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('error_code', sa.String(128)), sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')))
    op.create_index('ix_completion_attempt_operation_created', 'completion_integration_attempts', ['operation_id', 'created_at'])

def downgrade():
    op.drop_table('completion_integration_attempts')
    op.drop_column('connection_completion_operations', 'techportal_completed_at')
    op.drop_column('connection_completion_operations', 'subscriber_address')
    op.drop_column('connection_completion_operations', 'subscriber_login')
