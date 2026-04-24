"""add accountability_partners and partner_nudge_events tables

Revision ID: 0019_accountability_partners
Revises: 0018_notification_inbox
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa

revision = '0019_accountability_partners'
down_revision = '0018_notification_inbox'
branch_labels = None
depends_on = None


def upgrade():
    # ── Accountability partner relationships ──────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS accountability_partners (
            id              BIGSERIAL   PRIMARY KEY,
            requester_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            partner_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status          TEXT        NOT NULL DEFAULT 'pending',
            approved_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (requester_id, partner_id),
            CHECK  (requester_id != partner_id)
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partners_partner_status "
        "ON accountability_partners (partner_id, status)"
    ))

    # ── One-per-day nudge rate-limit + audit log ───────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS partner_nudge_events (
            id              BIGSERIAL   PRIMARY KEY,
            sender_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            receiver_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            local_day       DATE        NOT NULL,
            sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (receiver_id, local_day),
            UNIQUE (sender_id, receiver_id, local_day)
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_nudge_events_sent_at "
        "ON partner_nudge_events (sent_at DESC)"
    ))


def downgrade():
    op.drop_table('partner_nudge_events')
    op.drop_table('accountability_partners')
