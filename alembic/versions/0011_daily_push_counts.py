"""add daily_push_counts table

Revision ID: 0011_daily_push_counts
Revises: 0010_habit_challenge_tables
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_daily_push_counts'
down_revision = '0010_habit_challenge_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS daily_push_counts (
            user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            push_date DATE NOT NULL,
            count     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, push_date)
        )
    """))


def downgrade():
    op.drop_table('daily_push_counts')
