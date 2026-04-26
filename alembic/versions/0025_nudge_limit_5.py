"""raise nudge limit to 5 per pair per day

Revision ID: 0025_nudge_limit_5
Revises: 0024_partner_queue
Create Date: 2026-04-26
"""
from alembic import op

revision = "0025_nudge_limit_5"
down_revision = "0024_partner_queue"
branch_labels = None
depends_on = None


def upgrade():
    # uq_nudge_receiver_day may not exist if the DB was created after it was removed.
    # Drop it only if present — now allowing up to 5 nudges/day (counted in app logic).
    from sqlalchemy import text
    conn = op.get_bind()
    exists = conn.execute(text("""
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_nudge_receiver_day'
          AND conrelid = 'partner_nudge_events'::regclass
    """)).scalar()
    if exists:
        op.drop_constraint("uq_nudge_receiver_day", "partner_nudge_events", type_="unique")


def downgrade():
    op.create_unique_constraint(
        "uq_nudge_receiver_day", "partner_nudge_events", ["receiver_id", "local_day"]
    )
