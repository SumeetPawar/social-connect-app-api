"""add partner queue and opt-out fields to users

Revision ID: 0024_partner_queue
Revises: 0023_custom_habits
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_partner_queue"
down_revision = "0023_custom_habits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("partner_opt_out",  sa.Boolean,                  nullable=False, server_default="false"))
    op.add_column("users", sa.Column("seeking_partner",  sa.Boolean,                  nullable=False, server_default="false"))
    op.add_column("users", sa.Column("seeking_since",    sa.DateTime(timezone=True),  nullable=True))
    op.create_index("ix_users_seeking_partner", "users", ["seeking_partner", "seeking_since"])


def downgrade():
    op.drop_index("ix_users_seeking_partner", table_name="users")
    op.drop_column("users", "seeking_since")
    op.drop_column("users", "seeking_partner")
    op.drop_column("users", "partner_opt_out")
