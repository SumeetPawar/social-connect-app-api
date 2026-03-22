"""Rename muscle_pct to skeletal_muscle_pct

Revision ID: 0008_skeletal_muscle_pct
Revises: 0007_add_muscle_pct
Create Date: 2026-03-03
"""
from alembic import op

revision = '0008_skeletal_muscle_pct'
down_revision = '0007_add_muscle_pct'
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column('body_metrics', 'muscle_pct', new_column_name='skeletal_muscle_pct')

def downgrade():
    op.alter_column('body_metrics', 'skeletal_muscle_pct', new_column_name='muscle_pct')
