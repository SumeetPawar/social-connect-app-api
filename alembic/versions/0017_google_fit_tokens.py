"""add user_google_fit_tokens table

Revision ID: 0017_google_fit_tokens
Revises: 0016_push_logs
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa

revision = '0017_google_fit_tokens'
down_revision = '0016_push_logs'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS user_google_fit_tokens (
            user_id       UUID        NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            access_token  TEXT        NOT NULL,
            refresh_token TEXT        NOT NULL,
            expires_at    TIMESTAMPTZ NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))


def downgrade():
    op.drop_table('user_google_fit_tokens')
