"""Track delivery of GIS report links to TechPortal comments."""
from alembic import op
import sqlalchemy as sa

revision = '20260930_01'
down_revision = '20260929_01'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('connection_completion_operations', sa.Column('techportal_gis_link_status', sa.String(32), nullable=False, server_default='not_requested'))
    op.add_column('connection_completion_operations', sa.Column('techportal_gis_link_next_attempt_at', sa.DateTime(timezone=True)))
    op.add_column('connection_completion_operations', sa.Column('techportal_gis_link_lease_until', sa.DateTime(timezone=True)))
    op.add_column('connection_completion_operations', sa.Column('techportal_gis_link_attempt_count', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_completion_tp_gis_link_due', 'connection_completion_operations', ['techportal_gis_link_status', 'techportal_gis_link_next_attempt_at'])
    op.alter_column('connection_completion_operations', 'techportal_gis_link_status', server_default=None)
    op.alter_column('connection_completion_operations', 'techportal_gis_link_attempt_count', server_default=None)

def downgrade():
    op.drop_index('ix_completion_tp_gis_link_due', table_name='connection_completion_operations')
    op.drop_column('connection_completion_operations', 'techportal_gis_link_attempt_count')
    op.drop_column('connection_completion_operations', 'techportal_gis_link_lease_until')
    op.drop_column('connection_completion_operations', 'techportal_gis_link_next_attempt_at')
    op.drop_column('connection_completion_operations', 'techportal_gis_link_status')
