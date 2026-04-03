"""add ai_coach_reports table

Revision ID: 0013_ai_coach_reports
Revises: 0012_ai_insights
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_ai_coach_reports'
down_revision = '0012_ai_insights'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_coach_reports (
            id          SERIAL PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider    TEXT NOT NULL,
            went_well   JSONB NOT NULL,
            improve     JSONB NOT NULL,
            focus       TEXT NOT NULL,
            summary     TEXT NOT NULL,
            raw_stats   JSONB NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ai_coach_reports_user_created "
        "ON ai_coach_reports (user_id, created_at DESC)"
    ))


def downgrade():
    op.drop_table('ai_coach_reports')
