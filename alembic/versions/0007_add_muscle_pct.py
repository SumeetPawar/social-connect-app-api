"""Add muscle_pct to body_metrics

Revision ID: 0007_add_muscle_pct
Revises: 0006_daily_target_fix
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_add_muscle_pct'
down_revision = '0006_daily_target_fix'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('body_metrics', sa.Column('muscle_pct', sa.Numeric(5,2), nullable=True))

def downgrade():
    op.drop_column('body_metrics', 'muscle_pct')
