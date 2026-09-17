"""Durable connection completion reports and conditional GIS delivery."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = '20260917_01'
down_revision = '20260912_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'connection_completion_operations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('idempotency_key', UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.String(16), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('technician_external_id', sa.String(128), nullable=False),
        sa.Column('technician_name', sa.String(255), nullable=False),
        sa.Column('feature_id', UUID(as_uuid=True)),
        sa.Column('feature_snapshot', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('external_report_id', UUID(as_uuid=True), unique=True),
        sa.Column('report_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('photos', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('techportal_comment', sa.Text()),
        sa.Column('completion_status', sa.String(32), nullable=False, server_default='prepared'),
        sa.Column('gis_status', sa.String(32), nullable=False, server_default='not_requested'),
        sa.Column('gis_report_id', sa.String(128)),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('last_error_code', sa.String(128)),
        sa.Column('last_error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_connection_completion_gis_due', 'connection_completion_operations', ['gis_status', 'next_attempt_at'])
    op.create_index('ix_connection_completion_ticket_created', 'connection_completion_operations', ['ticket_id', 'created_at'])


def downgrade():
    op.drop_table('connection_completion_operations')
