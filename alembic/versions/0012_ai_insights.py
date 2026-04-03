"""add ai_insights cache table

Revision ID: 0012_ai_insights
Revises: 0011_daily_push_counts
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0012_ai_insights'
down_revision = '0011_daily_push_counts'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_insights (
            id           SERIAL PRIMARY KEY,
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            insight_date DATE NOT NULL,
            provider     TEXT NOT NULL,
            badge        TEXT NOT NULL,
            segments     JSONB NOT NULL,
            detail       JSONB NOT NULL,
            hook         TEXT NOT NULL,
            raw_stats    JSONB NOT NULL,
            created_at   TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_ai_insight_user_date UNIQUE (user_id, insight_date)
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ai_insights_user_date ON ai_insights (user_id, insight_date)"
    ))


def downgrade():
    op.drop_table('ai_insights')
