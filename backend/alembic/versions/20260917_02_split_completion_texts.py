"""Separate TechPortal and GIS completion report texts."""
from alembic import op
import sqlalchemy as sa


revision = '20260917_02'
down_revision = '20260917_01'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('connection_completion_operations', 'report_text', new_column_name='gis_text')
    op.add_column(
        'connection_completion_operations',
        sa.Column('techportal_text', sa.Text(), nullable=False, server_default=''),
    )
    # Preserve operations created by the previous one-field implementation.
    op.execute(sa.text(
        'UPDATE connection_completion_operations SET techportal_text = gis_text WHERE techportal_text = :empty'
    ).bindparams(empty=''))
    op.alter_column('connection_completion_operations', 'techportal_text', server_default=None)


def downgrade():
    op.drop_column('connection_completion_operations', 'techportal_text')
    op.alter_column('connection_completion_operations', 'gis_text', new_column_name='report_text')
