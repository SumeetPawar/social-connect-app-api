"""create habit challenge tables

Revision ID: 0010_habit_challenge_tables
Revises: 0009_add_subcutaneous_fat_pct
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_habit_challenge_tables'
down_revision = '0009_add_subcutaneous_fat_pct'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # enums — create only if missing
    conn.execute(sa.text("DO $$ BEGIN CREATE TYPE habitcategory AS ENUM ('Body','Mind','Lifestyle'); EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
    conn.execute(sa.text("DO $$ BEGIN CREATE TYPE habittier AS ENUM ('core','growth','avoid'); EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
    conn.execute(sa.text("DO $$ BEGIN CREATE TYPE challengestatus AS ENUM ('active','completed','abandoned'); EXCEPTION WHEN duplicate_object THEN NULL; END $$"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS habits (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(64) NOT NULL UNIQUE,
            label VARCHAR(255) NOT NULL,
            description VARCHAR(512) NOT NULL,
            why TEXT NOT NULL,
            impact VARCHAR(64) NOT NULL,
            category habitcategory NOT NULL,
            tier habittier NOT NULL,
            has_counter BOOLEAN NOT NULL DEFAULT false,
            unit VARCHAR(32),
            target INTEGER
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_habits_slug ON habits (slug)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS habit_challenges (
            id SERIAL PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pack_id VARCHAR(64),
            status challengestatus NOT NULL DEFAULT 'active',
            started_at DATE NOT NULL,
            ends_at DATE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_habit_challenges_user_id ON habit_challenges (user_id)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS habit_commitments (
            id SERIAL PRIMARY KEY,
            challenge_id INTEGER NOT NULL REFERENCES habit_challenges(id) ON DELETE CASCADE,
            habit_id INTEGER NOT NULL REFERENCES habits(id),
            sort_order INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_commitment_habit UNIQUE (challenge_id, habit_id)
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS daily_logs (
            id SERIAL PRIMARY KEY,
            commitment_id INTEGER NOT NULL REFERENCES habit_commitments(id) ON DELETE CASCADE,
            logged_date DATE NOT NULL,
            completed BOOLEAN NOT NULL DEFAULT false,
            value INTEGER,
            logged_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_log_commitment_date UNIQUE (commitment_id, logged_date)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_daily_logs_commitment_date ON daily_logs (commitment_id, logged_date)"))


def downgrade():
    op.drop_table('daily_logs')
    op.drop_table('habit_commitments')
    op.drop_table('habit_challenges')
    op.drop_table('habits')
    op.execute('DROP TYPE IF EXISTS challengestatus')
    op.execute('DROP TYPE IF EXISTS habittier')
    op.execute('DROP TYPE IF EXISTS habitcategory')
    op.execute('DROP TYPE IF EXISTS habitcategory')