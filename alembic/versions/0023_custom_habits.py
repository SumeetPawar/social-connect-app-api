"""add user_habits table and custom habit support in commitments

Revision ID: 0023_custom_habits
Revises: 0022_partner_messages
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_custom_habits"
down_revision = "0022_partner_messages"
branch_labels = None
depends_on = None


def upgrade():
    # 1. New user_habits table
    op.create_table(
        "user_habits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("emoji", sa.String(8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_user_habits_user_name"),
    )
    op.create_index("ix_user_habits_user_id", "user_habits", ["user_id"])

    # 2. Add user_habit_id FK to habit_commitments + make habit_id nullable
    op.add_column(
        "habit_commitments",
        sa.Column("user_habit_id", sa.Integer, sa.ForeignKey("user_habits.id", ondelete="CASCADE"), nullable=True),
    )
    op.alter_column("habit_commitments", "habit_id", nullable=True)

    # 3. New unique constraint for custom habit per challenge
    op.create_unique_constraint(
        "uq_commitment_user_habit",
        "habit_commitments",
        ["challenge_id", "user_habit_id"],
    )

    # 4. CHECK: exactly one of habit_id / user_habit_id must be set
    op.create_check_constraint(
        "ck_one_habit_source",
        "habit_commitments",
        "(habit_id IS NULL) != (user_habit_id IS NULL)",
    )


def downgrade():
    op.drop_constraint("ck_one_habit_source", "habit_commitments", type_="check")
    op.drop_constraint("uq_commitment_user_habit", "habit_commitments", type_="unique")
    op.alter_column("habit_commitments", "habit_id", nullable=False)
    op.drop_column("habit_commitments", "user_habit_id")
    op.drop_index("ix_user_habits_user_id", table_name="user_habits")
    op.drop_table("user_habits")
