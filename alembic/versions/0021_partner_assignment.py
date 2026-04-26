"""add assignment fields to accountability_partners

Revision ID: 0021_partner_assignment
Revises: 0020_push_logs_error_detail
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0021_partner_assignment'
down_revision = '0020_push_logs_error_detail'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        ALTER TABLE accountability_partners
          ADD COLUMN IF NOT EXISTS assigned_by     UUID REFERENCES users(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS assignment_type TEXT NOT NULL DEFAULT 'manual',
          ADD COLUMN IF NOT EXISTS week_start      DATE,
          ADD COLUMN IF NOT EXISTS requester_keep  BOOLEAN,
          ADD COLUMN IF NOT EXISTS partner_keep    BOOLEAN,
          ADD COLUMN IF NOT EXISTS keep_deadline   TIMESTAMPTZ
    """))

    # status constraint: add completed + reshuffled to allowed values (advisory, not enforced)
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partners_week_start "
        "ON accountability_partners (week_start) WHERE week_start IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_partners_assignment_type "
        "ON accountability_partners (assignment_type, status)"
    ))


def downgrade():
    op.execute(sa.text("""
        ALTER TABLE accountability_partners
          DROP COLUMN IF EXISTS assigned_by,
          DROP COLUMN IF EXISTS assignment_type,
          DROP COLUMN IF EXISTS week_start,
          DROP COLUMN IF EXISTS requester_keep,
          DROP COLUMN IF EXISTS partner_keep,
          DROP COLUMN IF EXISTS keep_deadline
    """))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_partners_week_start"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_partners_assignment_type"))
