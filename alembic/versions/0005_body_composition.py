"""Add body composition metrics

Revision ID: 0005_body_composition
Revises: 0004_add_previous_rank
Create Date: 2026-02-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_body_composition"
down_revision = "0004_add_previous_rank"
branch_labels = None
depends_on = None


def upgrade():
    # body_metrics — extend existing table with full scan fields
    op.add_column('body_metrics', sa.Column('visceral_fat',  sa.Numeric(5,1), nullable=True))
    op.add_column('body_metrics', sa.Column('bone_mass_kg',  sa.Numeric(5,2), nullable=True))
    op.add_column('body_metrics', sa.Column('hydration_pct', sa.Numeric(5,1), nullable=True))
    op.add_column('body_metrics', sa.Column('protein_pct',   sa.Numeric(5,1), nullable=True))
    op.add_column('body_metrics', sa.Column('bmr_kcal',      sa.Integer,      nullable=True))
    op.add_column('body_metrics', sa.Column('metabolic_age', sa.Integer,      nullable=True))

    # users — profile fields for personalised ideal ranges
    op.add_column('users', sa.Column('age',            sa.Integer,    nullable=True))
    op.add_column('users', sa.Column('gender',         sa.String(10), nullable=True))
    op.add_column('users', sa.Column('activity_level', sa.String(20), nullable=True))


def downgrade():
    for col in ['visceral_fat','bone_mass_kg','hydration_pct','protein_pct','bmr_kcal','metabolic_age']:
        op.drop_column('body_metrics', col)
    for col in ['age','gender','activity_level']:
        op.drop_column('users', col)