"""drop nudge unique constraint — no per-sender daily limit

Revision ID: 0026_drop_nudge_unique
Revises: 0025_nudge_limit_5
Create Date: 2026-04-26
"""
from alembic import op
from sqlalchemy import text

revision = "0026_drop_nudge_unique"
down_revision = "0025_nudge_limit_5"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    exists = conn.execute(text("""
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_nudge_sender_receiver_day'
          AND conrelid = 'partner_nudge_events'::regclass
    """)).scalar()
    if exists:
        op.drop_constraint("uq_nudge_sender_receiver_day", "partner_nudge_events", type_="unique")


def downgrade():
    op.create_unique_constraint(
        "uq_nudge_sender_receiver_day", "partner_nudge_events",
        ["sender_id", "receiver_id", "local_day"]
    )
