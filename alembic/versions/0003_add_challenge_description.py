"""Add description to challenges

Revision ID: 0003_add_challenge_description
Revises: 0002_add_push_subscriptions
Create Date: 2026-01-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_add_challenge_description"
down_revision = "0002_add_push_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "challenges",
        sa.Column("description", sa.Text(), nullable=True)
    )


def downgrade():
    op.drop_column("challenges", "description")
