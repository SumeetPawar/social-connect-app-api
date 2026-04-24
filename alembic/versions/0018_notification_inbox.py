"""add notification_inbox table for user-facing in-app notifications

Revision ID: 0018_notification_inbox
Revises: 0017_google_fit_tokens
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa

revision = '0018_notification_inbox'
down_revision = '0017_google_fit_tokens'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notification_inbox (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type            TEXT        NOT NULL,
            actor_user_id   UUID        REFERENCES users(id) ON DELETE SET NULL,
            actor_name      TEXT,
            template_key    TEXT        NOT NULL,
            payload         JSONB       NOT NULL DEFAULT '{}',
            action_url      TEXT,
            push_title      TEXT,
            push_body       TEXT,
            is_read         BOOLEAN     NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ
        )
    """))
    # Fast fetch for inbox bell + unread count
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_inbox_user_read_created "
        "ON notification_inbox (user_id, is_read, created_at DESC)"
    ))
    # Nightly cleanup — only index rows that have an expiry
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_inbox_expires "
        "ON notification_inbox (expires_at) WHERE expires_at IS NOT NULL"
    ))


def downgrade():
    op.drop_table('notification_inbox')
