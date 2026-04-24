"""add error_detail column to push_logs for ERR diagnosis

Revision ID: 0020_push_logs_error_detail
Revises: 0019_accountability_partners
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0020_push_logs_error_detail'
down_revision = '0019_accountability_partners'
branch_labels = None
depends_on = None


def upgrade():
    # error_detail stores HTTP status + short error text for failed pushes
    op.execute(sa.text("""
        ALTER TABLE push_logs
        ADD COLUMN IF NOT EXISTS error_detail TEXT
    """))


def downgrade():
    op.execute(sa.text("""
        ALTER TABLE push_logs
        DROP COLUMN IF EXISTS error_detail
    """))
