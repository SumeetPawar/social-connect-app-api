"""Expand daily target constraint to include 8000 and 9000

Revision ID: 0006_expand_daily_target_constraint
Revises: 0005_body_composition
Create Date: 2026-03-02

"""
from alembic import op

revision = '0006_daily_target_fix'
down_revision = '0005_body_composition'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE challenge_participants
        DROP CONSTRAINT IF EXISTS chk_challenge_participants_daily_target_allowed
    """)
    op.execute("""
        ALTER TABLE challenge_participants
        ADD CONSTRAINT chk_challenge_participants_daily_target_allowed
        CHECK ((selected_daily_target IS NULL) OR (selected_daily_target IN (3000,5000,7500,8000,9000,10000)))
    """)


def downgrade():
    op.execute("""
        ALTER TABLE challenge_participants
        DROP CONSTRAINT IF EXISTS chk_challenge_participants_daily_target_allowed
    """)
    op.execute("""
        ALTER TABLE challenge_participants
        ADD CONSTRAINT chk_challenge_participants_daily_target_allowed
        CHECK ((selected_daily_target IS NULL) OR (selected_daily_target IN (3000,5000,7500,10000)))
    """)
