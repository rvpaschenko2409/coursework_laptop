"""Server-side sessions and persistent authentication throttling."""
from alembic import op
revision='web0001'
down_revision=None
branch_labels=None
depends_on=None

UP_SQL="""
CREATE TABLE web_sessions (
 token_hash char(64) PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 user_id bigint REFERENCES users(id) ON DELETE CASCADE,
 csrf_token char(64) NOT NULL CHECK(csrf_token ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 CHECK(expires_at>created_at)
);
CREATE INDEX ix_web_sessions_expires ON web_sessions(expires_at);
CREATE INDEX ix_web_sessions_user ON web_sessions(user_id) WHERE user_id IS NOT NULL;
CREATE TABLE web_auth_attempts (
 key_hash char(64) PRIMARY KEY CHECK(key_hash ~ '^[0-9a-f]{64}$'),
 failures integer NOT NULL CHECK(failures>=0),
 window_started_at timestamptz NOT NULL DEFAULT now(),
 blocked_until timestamptz
);
CREATE INDEX ix_web_attempts_window ON web_auth_attempts(window_started_at);
"""
DOWN_SQL='DROP TABLE web_auth_attempts; DROP TABLE web_sessions;'

def upgrade():op.execute(UP_SQL)
def downgrade():op.execute(DOWN_SQL)
