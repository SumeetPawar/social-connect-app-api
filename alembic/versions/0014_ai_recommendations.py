"""add ai_recommendations table

Revision ID: 0014_ai_recommendations
Revises: 0013_ai_coach_reports
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0014_ai_recommendations'
down_revision = '0013_ai_coach_reports'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_recommendations (
            id          SERIAL PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type        TEXT NOT NULL,
            provider    TEXT NOT NULL,
            payload     JSONB NOT NULL,
            raw_stats   JSONB NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ai_recommendations_user_type_created "
        "ON ai_recommendations (user_id, type, created_at DESC)"
    ))


def downgrade():
    op.drop_table('ai_recommendations')
