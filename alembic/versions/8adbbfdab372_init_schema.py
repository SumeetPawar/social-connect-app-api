from alembic import op

revision = "8adbbfdab372"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
  
    # ---- extensions ----
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

    # ---- users ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name TEXT,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      is_email_verified BOOLEAN NOT NULL DEFAULT FALSE,
      role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
      timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

    # ---- refresh tokens ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS refresh_tokens (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      token_hash TEXT NOT NULL,
      issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      expires_at TIMESTAMPTZ NOT NULL,
      revoked_at TIMESTAMPTZ
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_expires ON refresh_tokens(expires_at);")

    # ---- goal definitions (future-proof metrics) ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS goal_definitions (
      key TEXT PRIMARY KEY,                     -- 'steps', 'weight', 'bmi', etc.
      label TEXT NOT NULL,                      -- 'Steps'
      unit TEXT NOT NULL,                       -- 'steps'
      value_type TEXT NOT NULL DEFAULT 'int'
        CHECK (value_type IN ('int','float','json')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)

    # seed steps metric
    op.execute("""
    INSERT INTO goal_definitions (key, label, unit, value_type)
    VALUES ('steps', 'Steps', 'steps', 'int')
    ON CONFLICT (key) DO NOTHING;
    """)

    # ---- goals (LOCKED per period; UI daily, DB period total) ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS goals (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

      metric_key TEXT NOT NULL REFERENCES goal_definitions(key), -- 'steps' now
      period TEXT NOT NULL CHECK (period IN ('week','month')),   -- month later

      daily_target NUMERIC(12,2) NOT NULL CHECK (daily_target > 0),
      period_target NUMERIC(12,2) NOT NULL CHECK (period_target > 0),

      -- Actual active window (for join mid-week/month later)
      period_start DATE NOT NULL,
      period_end   DATE NOT NULL,

      -- Anchor window start (for lock & uniqueness)
      -- week: Monday of that week; month: 1st of month
      anchor_start DATE NOT NULL,

      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

      CONSTRAINT chk_goal_dates CHECK (period_end >= period_start),
      CONSTRAINT chk_goal_anchor CHECK (anchor_start <= period_start)
    );
    """)

    # 🔒 lock: only 1 goal per user per metric per period per anchor window
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_goals_user_metric_period_anchor
    ON goals(user_id, metric_key, period, anchor_start);
    """)
    
    op.execute("""
    INSERT INTO goal_definitions (key, label, unit, value_type)
    VALUES ('water', 'Water', 'liters', 'float')
    ON CONFLICT (key) DO NOTHING;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_goals_user_metric ON goals(user_id, metric_key);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_goals_user_period_anchor ON goals(user_id, period, anchor_start);")

    # ---- steps ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS step_logs (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      log_date DATE NOT NULL,
      steps INTEGER NOT NULL CHECK (steps > 0),
      source TEXT NOT NULL DEFAULT 'manual',
      note TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_step_logs_user_date ON step_logs(user_id, log_date);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_step_logs_created ON step_logs(user_id, created_at DESC);")

    # ---- daily totals ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS daily_totals (
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      day DATE NOT NULL,
      total_steps INTEGER NOT NULL DEFAULT 0 CHECK (total_steps >= 0),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (user_id, day)
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_daily_totals_user_day ON daily_totals(user_id, day);")

    # ---- ai summaries ----
    op.execute("""
    CREATE TABLE IF NOT EXISTS ai_summaries (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      scope TEXT NOT NULL CHECK (scope IN ('day','week','month')),
      start_date DATE NOT NULL,
      end_date DATE NOT NULL,
      summary TEXT NOT NULL,
      model TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT chk_ai_dates CHECK (end_date >= start_date)
    );
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_ai_summaries_user_scope_dates
    ON ai_summaries(user_id, scope, start_date, end_date);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_summaries;")
    op.execute("DROP TABLE IF EXISTS daily_totals;")
    op.execute("DROP TABLE IF EXISTS step_logs;")
    op.execute("DROP TABLE IF EXISTS goals;")
    op.execute("DROP TABLE IF EXISTS goal_definitions;")
    op.execute("DROP TABLE IF EXISTS refresh_tokens;")
    op.execute("DROP TABLE IF EXISTS users;")
