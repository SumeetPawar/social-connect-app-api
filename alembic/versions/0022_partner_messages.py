"""create partner_messages table

Revision ID: 0022_partner_messages
Revises: 0021_partner_assignment
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0022_partner_messages'
down_revision = '0021_partner_assignment'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS partner_messages (
            id          BIGSERIAL   PRIMARY KEY,
            pair_id     BIGINT      NOT NULL REFERENCES accountability_partners(id) ON DELETE CASCADE,
            sender_id   UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            receiver_id UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body        TEXT        NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            read_at     TIMESTAMPTZ,
            expires_at  TIMESTAMPTZ
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partner_messages_pair_sent "
        "ON partner_messages (pair_id, sent_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partner_messages_receiver_read "
        "ON partner_messages (receiver_id, read_at) WHERE read_at IS NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partner_messages_expires "
        "ON partner_messages (expires_at) WHERE expires_at IS NOT NULL"
    ))


def downgrade():
    op.drop_table('partner_messages')
