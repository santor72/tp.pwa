"""Compact completed-job identities for safe retention and duplicate delivery."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20260912_01'
down_revision = '20260911_04'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_payment_jobs_finished', 'payment_jobs', ['finished_at', 'id'])
    op.create_table('payment_job_archive',
        sa.Column('job_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('event_id', UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('transaction_id', UUID(as_uuid=True), sa.ForeignKey('payment_transactions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(32), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_payment_job_archive_transaction_id', 'payment_job_archive', ['transaction_id'])


def downgrade():
    op.drop_table('payment_job_archive')
    op.drop_index('ix_payment_jobs_finished', table_name='payment_jobs')
