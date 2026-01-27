"""Fresh complete schema

Revision ID: 0001_fresh_complete
Revises: 
Create Date: 2026-01-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_fresh_complete'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Drop all tables individually (asyncpg limitation)
    op.execute("DROP TABLE IF EXISTS ai_summaries CASCADE")
    op.execute("DROP TABLE IF EXISTS user_task_completions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS task_completions CASCADE")
    op.execute("DROP TABLE IF EXISTS task_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_badge_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS badge_awards CASCADE")
    op.execute("DROP TABLE IF EXISTS badges CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_participants CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_departments CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS challenges CASCADE")
    op.execute("DROP TABLE IF EXISTS body_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS daily_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS daily_steps CASCADE")
    op.execute("DROP TABLE IF EXISTS goal_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS refresh_tokens CASCADE")
    op.execute("DROP TABLE IF EXISTS team_members CASCADE")
    op.execute("DROP TABLE IF EXISTS teams CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS departments CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_team_leaderboard CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_weekly_leaderboard CASCADE")
    op.execute("DROP FUNCTION IF EXISTS refresh_leaderboard CASCADE")
    op.execute("DROP FUNCTION IF EXISTS auto_complete_challenges CASCADE")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column CASCADE")
    op.execute("DROP FUNCTION IF EXISTS calculate_bmi CASCADE")

    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # Departments
    op.create_table(
        'departments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['departments.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_departments_parent_id', 'departments', ['parent_id'])
    op.create_index('ux_departments_name_parent', 'departments', ['name', 'parent_id'], unique=True)

    # Users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('is_email_verified', sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column('role', sa.Text(), nullable=False, server_default=sa.text("'user'")),
        sa.Column('timezone', sa.Text(), nullable=False, server_default=sa.text("'Asia/Kolkata'")),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_pic_url', sa.Text(), nullable=True),
        sa.Column('global_current_streak', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('global_longest_streak', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('global_perfect_days', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('last_streak_update', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('user','admin')", name='chk_users_role'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('email', name='ux_users_email'),
    )
    op.create_index('ix_users_department_id', 'users', ['department_id'])
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_current_streak', 'users', ['global_current_streak'])
    op.create_index('ix_users_last_streak_update', 'users', ['last_streak_update'])

    # Teams
    op.create_table(
        'teams',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('department_id', 'name', name='ux_teams_department_name'),
    )
    op.create_index('ix_teams_department_id', 'teams', ['department_id'])

    # Team members
    op.create_table(
        'team_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.Text(), nullable=False, server_default=sa.text("'member'")),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('member','lead','admin')", name='chk_team_members_role'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_team_members_team_id', 'team_members', ['team_id'])
    op.create_index('ix_team_members_user_id', 'team_members', ['user_id'])
    op.create_index('ix_team_members_left_at', 'team_members', ['left_at'])

    # Refresh tokens
    op.create_table(
        'refresh_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_refresh_tokens_user_id', 'refresh_tokens', ['user_id'])
    op.create_index('ix_refresh_tokens_expires_at', 'refresh_tokens', ['expires_at'])

    # Goal definitions
    op.create_table(
        'goal_definitions',
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('unit', sa.Text(), nullable=False),
        sa.Column('value_type', sa.Text(), nullable=False, server_default=sa.text("'int'")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("value_type IN ('int','float','bool')", name='chk_goal_definitions_value_type'),
    )
    
    # Seed goal definitions
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('steps', 'Steps', 'Track your daily steps to stay active and healthy. Walking is one of the easiest ways to improve fitness.', 'steps', 'int')""")
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('water', 'Water', 'Stay hydrated by drinking enough water throughout the day. Aim for 7 glasses (2 liters).', 'bottles', 'int')""")
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('stretching', 'Stretching', 'Spend time stretching to improve flexibility and prevent injury. Even 5-10 minutes makes a difference.', 'done', 'bool')""")
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('no_junk', 'No Junk', 'Avoid junk food and sugary snacks. Choose healthier alternatives to fuel your body properly.', 'done', 'bool')""")
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('deep_work', 'Deep Work', 'Complete a focused deep work session without distractions. Boost your productivity and mental clarity.', 'done', 'bool')""")
    op.execute("""INSERT INTO goal_definitions (key, label, description, unit, value_type) VALUES ('plan_day', 'Plan Day', 'Take time to plan your day ahead. Organization leads to better time management and less stress.', 'done', 'bool')""")

    # Daily steps
    op.create_table(
        'daily_steps',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('steps', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'day', name='pk_daily_steps'),
        sa.CheckConstraint('steps >= 0', name='chk_daily_steps_nonneg'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_daily_steps_day', 'daily_steps', ['day'])

    # Daily metrics
    op.create_table(
        'daily_metrics',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('metric_key', sa.Text(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('value_num', sa.Numeric(12, 2), nullable=True),
        sa.Column('value_bool', sa.Boolean(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'metric_key', 'day', name='pk_daily_metrics'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['metric_key'], ['goal_definitions.key'], ondelete='RESTRICT'),
    )
    op.create_index('ix_daily_metrics_day', 'daily_metrics', ['day'])
    op.create_index('ix_daily_metrics_metric_key', 'daily_metrics', ['metric_key'])

    # Body metrics
    op.create_table(
        'body_metrics',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recorded_date', sa.Date(), nullable=False),
        sa.Column('weight_kg', sa.Numeric(5, 2), nullable=True),
        sa.Column('height_cm', sa.Numeric(5, 2), nullable=True),
        sa.Column('body_fat_pct', sa.Numeric(4, 2), nullable=True),
        sa.Column('muscle_mass_kg', sa.Numeric(5, 2), nullable=True),
        sa.Column('bmi', sa.Numeric(4, 2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint('weight_kg IS NULL OR weight_kg > 0', name='chk_body_metrics_weight_positive'),
        sa.CheckConstraint('height_cm IS NULL OR height_cm > 0', name='chk_body_metrics_height_positive'),
        sa.CheckConstraint('body_fat_pct IS NULL OR (body_fat_pct >= 0 AND body_fat_pct <= 100)', name='chk_body_metrics_bf_range'),
        sa.CheckConstraint('muscle_mass_kg IS NULL OR muscle_mass_kg > 0', name='chk_body_metrics_muscle_positive'),
        sa.CheckConstraint('bmi IS NULL OR bmi > 0', name='chk_body_metrics_bmi_positive'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_body_metrics_user_date', 'body_metrics', ['user_id', 'recorded_date'])
    op.create_index('ix_body_metrics_user_id', 'body_metrics', ['user_id'])

    # BMI calculation function
    op.execute("""
    CREATE OR REPLACE FUNCTION calculate_bmi()
    RETURNS TRIGGER AS $$
    BEGIN
        IF NEW.weight_kg IS NOT NULL AND NEW.height_cm IS NOT NULL AND NEW.height_cm > 0 THEN
            NEW.bmi = ROUND((NEW.weight_kg / POWER(NEW.height_cm / 100, 2))::numeric, 2);
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER trigger_calculate_bmi BEFORE INSERT OR UPDATE ON body_metrics FOR EACH ROW EXECUTE FUNCTION calculate_bmi()")

    # Challenges
    op.create_table(
        'challenges',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('period', sa.Text(), nullable=False),
        sa.Column('scope', sa.Text(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column('min_goals_required', sa.Integer(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("period IN ('week','month')", name='chk_challenges_period'),
        sa.CheckConstraint("scope IN ('individual','team','department')", name='chk_challenges_scope'),
        sa.CheckConstraint("status IN ('draft','active','completed','archived')", name='chk_challenges_status'),
        sa.CheckConstraint('end_date >= start_date', name='chk_challenges_dates'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_challenges_start_end', 'challenges', ['start_date', 'end_date'])
    op.create_index('ix_challenges_status', 'challenges', ['status'])

    # Challenge metrics
    op.create_table(
        'challenge_metrics',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('challenge_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('metric_key', sa.Text(), nullable=False),
        sa.Column('target_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('rule_type', sa.Text(), nullable=False, server_default=sa.text("'daily'")),
        sa.CheckConstraint("rule_type IN ('daily','weekly')", name='chk_challenge_metrics_rule_type'),
        sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['metric_key'], ['goal_definitions.key'], ondelete='RESTRICT'),
        sa.UniqueConstraint('challenge_id', 'metric_key', name='ux_challenge_metrics_challenge_metric'),
    )
    op.create_index('ix_challenge_metrics_challenge_id', 'challenge_metrics', ['challenge_id'])
    op.create_index('ix_challenge_metrics_metric_key', 'challenge_metrics', ['metric_key'])

    # Challenge departments
    op.create_table(
        'challenge_departments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('challenge_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('challenge_id', 'department_id', name='ux_challenge_departments_unique'),
    )
    op.create_index('ix_challenge_departments_challenge_id', 'challenge_departments', ['challenge_id'])
    op.create_index('ix_challenge_departments_department_id', 'challenge_departments', ['department_id'])

    # Challenge participants
    op.create_table(
        'challenge_participants',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('challenge_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('selected_daily_target', sa.Integer(), nullable=True),
        sa.Column('challenge_current_streak', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('challenge_longest_streak', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('challenge_perfect_days', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('challenge_total_score', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('last_activity_date', sa.Date(), nullable=True),
        sa.CheckConstraint('(selected_daily_target IS NULL) OR (selected_daily_target IN (3000,5000,7500,10000))', name='chk_challenge_participants_daily_target_allowed'),
        sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('challenge_id', 'user_id', name='ux_challenge_participants_challenge_user'),
    )
    op.create_index('ix_challenge_participants_challenge_id', 'challenge_participants', ['challenge_id'])
    op.create_index('ix_challenge_participants_user_id', 'challenge_participants', ['user_id'])
    op.create_index('ix_challenge_participants_streak', 'challenge_participants', ['challenge_current_streak'])
    op.create_index('ix_challenge_participants_activity', 'challenge_participants', ['last_activity_date'])

    # Badges
    op.create_table(
        'badges',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('icon', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("category IN ('daily','weekly','lifetime')", name='chk_badges_category'),
        sa.UniqueConstraint('key', name='ux_badges_key'),
    )
    
    # Seed badges
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('perfect_week', 'Perfect Week', 'weekly')""")
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('weekly_winner', 'Weekly Winner', 'weekly')""")
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('ten_k_day', '10K Day', 'daily')""")
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('streak_7', '7-Day Streak', 'lifetime')""")
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('streak_30', '30-Day Streak', 'lifetime')""")
    op.execute("""INSERT INTO badges (key, name, category) VALUES ('team_mvp', 'Team MVP', 'weekly')""")

    # Badge awards
    op.create_table(
        'badge_awards',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('badge_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('challenge_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('window_start', sa.Date(), nullable=True),
        sa.Column('window_end', sa.Date(), nullable=True),
        sa.Column('earned_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint('(window_start IS NULL AND window_end IS NULL) OR (window_start IS NOT NULL AND window_end IS NOT NULL)', name='chk_badge_awards_window_pair'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['badge_id'], ['badges.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_badge_awards_user_id', 'badge_awards', ['user_id'])
    op.create_index('ix_badge_awards_badge_id', 'badge_awards', ['badge_id'])
    op.create_index('ix_badge_awards_challenge_id', 'badge_awards', ['challenge_id'])
    op.create_index('ix_badge_awards_earned_at', 'badge_awards', ['earned_at'])

    # User badge progress
    op.create_table(
        'user_badge_progress',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('badge_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('times_earned', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('best_streak', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('last_earned_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('user_id', 'badge_id', name='pk_user_badge_progress'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['badge_id'], ['badges.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_user_badge_progress_user_id', 'user_badge_progress', ['user_id'])

    # Task definitions
    op.create_table(
        'task_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('metric_key', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(['metric_key'], ['goal_definitions.key'], ondelete='SET NULL'),
        sa.UniqueConstraint('key', name='ux_task_definitions_key'),
    )
    
    # Seed tasks
    op.execute("""INSERT INTO task_definitions (key, label, metric_key) VALUES ('deep_work', 'Deep Work Session', 'deep_work')""")
    op.execute("""INSERT INTO task_definitions (key, label, metric_key) VALUES ('plan_day', 'Plan Your Day', 'plan_day')""")
    op.execute("""INSERT INTO task_definitions (key, label, metric_key) VALUES ('stretching', 'Stretching', 'stretching')""")
    op.execute("""INSERT INTO task_definitions (key, label, metric_key) VALUES ('no_junk', 'No Junk Food', 'no_junk')""")

    # Task completions
    op.create_table(
        'task_completions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['task_definitions.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'task_id', 'day', name='ux_task_completions_user_task_day'),
    )
    op.create_index('ix_task_completions_user_day', 'task_completions', ['user_id', 'day'])

    # User tasks
    op.create_table(
        'user_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('recurrence_type', sa.Text(), nullable=False),
        sa.Column('recurrence_interval', sa.Integer(), nullable=True),
        sa.Column('recurrence_unit', sa.Text(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('next_due_date', sa.Date(), nullable=False),
        sa.Column('reminder_enabled', sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column('reminder_days_before', sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column('reminder_time', sa.Time(), nullable=False, server_default=sa.text("'09:00:00'")),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("recurrence_type IN ('once','daily','weekly','monthly','yearly','custom')", name='chk_user_tasks_recurrence_type'),
        sa.CheckConstraint("category IN ('health','vehicle','home','personal','work')", name='chk_user_tasks_category'),
        sa.CheckConstraint("recurrence_unit IS NULL OR recurrence_unit IN ('days','weeks','months','years')", name='chk_user_tasks_recurrence_unit'),
        sa.CheckConstraint('reminder_days_before >= 0', name='chk_user_tasks_reminder_days'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_user_tasks_user_id', 'user_tasks', ['user_id'])
    op.create_index('ix_user_tasks_next_due_date', 'user_tasks', ['next_due_date'])
    op.create_index('ix_user_tasks_category', 'user_tasks', ['category'])
    op.create_index('ix_user_tasks_active', 'user_tasks', ['is_active'])
    op.create_index('ix_user_tasks_recurrence_type', 'user_tasks', ['recurrence_type'])

    # User task completions
    op.create_table(
        'user_task_completions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['user_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('task_id', 'due_date', name='ux_user_task_completions_task_due'),
    )
    op.create_index('ix_user_task_completions_task_id', 'user_task_completions', ['task_id'])
    op.create_index('ix_user_task_completions_user_id', 'user_task_completions', ['user_id'])
    op.create_index('ix_user_task_completions_due_date', 'user_task_completions', ['due_date'])
    op.create_index('ix_user_task_completions_completed_at', 'user_task_completions', ['completed_at'])

    # AI summaries
    op.create_table(
        'ai_summaries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope', sa.Text(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("scope IN ('day','week','month')", name='chk_ai_summaries_scope'),
        sa.CheckConstraint('end_date >= start_date', name='chk_ai_summaries_dates'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_ai_summaries_user_scope_dates', 'ai_summaries', ['user_id', 'scope', 'start_date', 'end_date'])

    # Updated_at trigger function
    op.execute("""
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    
    op.execute("CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()")
    op.execute("CREATE TRIGGER update_daily_steps_updated_at BEFORE UPDATE ON daily_steps FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()")
    op.execute("CREATE TRIGGER update_daily_metrics_updated_at BEFORE UPDATE ON daily_metrics FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()")
    op.execute("CREATE TRIGGER update_user_tasks_updated_at BEFORE UPDATE ON user_tasks FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()")

    # Auto-complete challenges function
    op.execute("""
    CREATE OR REPLACE FUNCTION auto_complete_challenges()
    RETURNS TRIGGER AS $$
    BEGIN
        UPDATE challenges SET status = 'completed' WHERE status = 'active' AND end_date < CURRENT_DATE;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER trigger_auto_complete_challenges AFTER INSERT OR UPDATE ON challenges FOR EACH STATEMENT EXECUTE FUNCTION auto_complete_challenges()")

    # Materialized views
    op.execute("""
    CREATE MATERIALIZED VIEW mv_weekly_leaderboard AS
    SELECT c.id as challenge_id, cp.team_id, cp.user_id, u.name as user_name, u.profile_pic_url, t.name as team_name, cp.selected_daily_target,
           COALESCE(SUM(ds.steps), 0) as total_steps, COUNT(DISTINCT ds.day) FILTER (WHERE ds.steps > 0) as days_logged,
           COALESCE(AVG(ds.steps) FILTER (WHERE ds.steps > 0), 0) as avg_daily_steps,
           CASE WHEN cp.selected_daily_target IS NOT NULL AND cp.selected_daily_target > 0
                THEN (COALESCE(SUM(ds.steps), 0)::numeric / (cp.selected_daily_target * (c.end_date - c.start_date + 1))) * 100
                ELSE NULL END as target_achievement_pct,
           COUNT(DISTINCT ds.day) FILTER (WHERE cp.selected_daily_target IS NOT NULL AND ds.steps >= cp.selected_daily_target) as days_target_met
    FROM challenge_participants cp
    JOIN users u ON cp.user_id = u.id
    LEFT JOIN teams t ON cp.team_id = t.id
    JOIN challenges c ON cp.challenge_id = c.id
    LEFT JOIN daily_steps ds ON ds.user_id = cp.user_id AND ds.day BETWEEN c.start_date AND c.end_date
    WHERE cp.left_at IS NULL AND c.status IN ('active', 'completed')
    GROUP BY c.id, cp.team_id, cp.user_id, u.name, u.profile_pic_url, t.name, cp.selected_daily_target, c.start_date, c.end_date
    """)
    op.execute("CREATE UNIQUE INDEX ux_mv_weekly_leaderboard ON mv_weekly_leaderboard(challenge_id, user_id)")
    op.execute("CREATE INDEX ix_mv_weekly_leaderboard_team ON mv_weekly_leaderboard(challenge_id, team_id)")
    op.execute("CREATE INDEX ix_mv_weekly_leaderboard_total_steps ON mv_weekly_leaderboard(challenge_id, total_steps DESC)")

    op.execute("""
    CREATE MATERIALIZED VIEW mv_team_leaderboard AS
    SELECT c.id as challenge_id, t.id as team_id, t.name as team_name, COUNT(DISTINCT cp.user_id) as member_count,
           COALESCE(SUM(ds.steps), 0) as total_steps, COALESCE(AVG(ds.steps), 0) as avg_steps_per_member,
           COUNT(DISTINCT ds.day) FILTER (WHERE ds.steps > 0) as active_days
    FROM teams t
    JOIN challenge_participants cp ON cp.team_id = t.id
    JOIN challenges c ON cp.challenge_id = c.id
    LEFT JOIN daily_steps ds ON ds.user_id = cp.user_id AND ds.day BETWEEN c.start_date AND c.end_date
    WHERE cp.left_at IS NULL AND c.status IN ('active', 'completed')
    GROUP BY c.id, t.id, t.name
    """)
    op.execute("CREATE UNIQUE INDEX ux_mv_team_leaderboard ON mv_team_leaderboard(challenge_id, team_id)")
    op.execute("CREATE INDEX ix_mv_team_leaderboard_total_steps ON mv_team_leaderboard(challenge_id, total_steps DESC)")

    # Refresh leaderboard function
    op.execute("""
    CREATE OR REPLACE FUNCTION refresh_leaderboard()
    RETURNS TRIGGER AS $$
    BEGIN
        REFRESH MATERIALIZED VIEW CONCURRENTLY mv_weekly_leaderboard;
        REFRESH MATERIALIZED VIEW CONCURRENTLY mv_team_leaderboard;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER trigger_refresh_leaderboard AFTER INSERT OR UPDATE OR DELETE ON daily_steps FOR EACH STATEMENT EXECUTE FUNCTION refresh_leaderboard()")


def downgrade():
    op.execute("DROP TABLE IF EXISTS ai_summaries CASCADE")
    op.execute("DROP TABLE IF EXISTS user_task_completions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS task_completions CASCADE")
    op.execute("DROP TABLE IF EXISTS task_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_badge_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS badge_awards CASCADE")
    op.execute("DROP TABLE IF EXISTS badges CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_participants CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_departments CASCADE")
    op.execute("DROP TABLE IF EXISTS challenge_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS challenges CASCADE")
    op.execute("DROP TABLE IF EXISTS body_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS daily_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS daily_steps CASCADE")
    op.execute("DROP TABLE IF EXISTS goal_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS refresh_tokens CASCADE")
    op.execute("DROP TABLE IF EXISTS team_members CASCADE")
    op.execute("DROP TABLE IF EXISTS teams CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS departments CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_team_leaderboard CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_weekly_leaderboard CASCADE")
    op.execute("DROP FUNCTION IF EXISTS refresh_leaderboard CASCADE")
    op.execute("DROP FUNCTION IF EXISTS auto_complete_challenges CASCADE")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column CASCADE")
    op.execute("DROP FUNCTION IF EXISTS calculate_bmi CASCADE")