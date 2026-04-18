"""add push_logs table for notification delivery tracking

Revision ID: 0016_push_logs
Revises: 0015_user_feedback
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0016_push_logs'
down_revision = '0015_user_feedback'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS push_logs (
            id          BIGSERIAL PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job         TEXT NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            result      TEXT NOT NULL,          -- 'ok' | 'expired' | 'error'
            title       TEXT,
            endpoint_hash TEXT                  -- last 12 chars of endpoint, for debugging
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_push_logs_user_sent "
        "ON push_logs (user_id, sent_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_push_logs_sent "
        "ON push_logs (sent_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_push_logs_job "
        "ON push_logs (job, sent_at DESC)"
    ))


def downgrade():
    op.drop_table('push_logs')
