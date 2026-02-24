"""
Add previous_rank and previous_consistency_rank columns to challenge_participants

Revision ID: 0004_add_previous_rank
Revises: 0003_add_challenge_description
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_add_previous_rank'
down_revision = '0003_add_challenge_description'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('challenge_participants', sa.Column('previous_rank', sa.Integer(), nullable=True))
    op.add_column('challenge_participants', sa.Column('previous_consistency_rank', sa.Integer(), nullable=True))

def downgrade():
    op.drop_column('challenge_participants', 'previous_consistency_rank')
    op.drop_column('challenge_participants', 'previous_rank')