"""add user_feedback table

Revision ID: 0015_user_feedback
Revises: 0014_ai_recommendations
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = '0015_user_feedback'
down_revision = '0014_ai_recommendations'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS user_feedback (
            id          SERIAL PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type        TEXT NOT NULL DEFAULT 'general',
            title       TEXT NOT NULL,
            body        TEXT,
            rating      SMALLINT CHECK (rating BETWEEN 1 AND 5),
            status      TEXT NOT NULL DEFAULT 'open',
            meta        JSONB,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_user_feedback_user_id "
        "ON user_feedback (user_id, created_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_user_feedback_status "
        "ON user_feedback (status, created_at DESC)"
    ))


def downgrade():
    op.drop_table('user_feedback')
