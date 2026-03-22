"""add subcutaneous_fat_pct to body_metrics

Revision ID: 0009_add_subcutaneous_fat_pct
Revises: 0008_skeletal_muscle_pct
down_revision = '0008_skeletal_muscle_pct'
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_add_subcutaneous_fat_pct'
down_revision = '0008_skeletal_muscle_pct'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('body_metrics', sa.Column('subcutaneous_fat_pct', sa.Numeric(4, 2), nullable=True))

def downgrade():
    op.drop_column('body_metrics', 'subcutaneous_fat_pct')
