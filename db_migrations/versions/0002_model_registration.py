"""Distinguish known training time from registration of existing artifacts."""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

UP_SQL = """
ALTER TABLE ml_models ALTER COLUMN trained_at DROP NOT NULL;
ALTER TABLE ml_models ADD COLUMN registered_at timestamptz NOT NULL DEFAULT now();
COMMENT ON COLUMN ml_models.trained_at IS 'Actual training timestamp when known; NULL for imported artifacts with unknown training date.';
COMMENT ON COLUMN ml_models.registered_at IS 'Time this model version was registered in this database.';
"""
DOWN_SQL = """
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml_models WHERE trained_at IS NULL) THEN
  RAISE EXCEPTION 'Cannot revert 0002 while models with unknown training dates exist';
 END IF;
END $$;
ALTER TABLE ml_models DROP COLUMN registered_at;
ALTER TABLE ml_models ALTER COLUMN trained_at SET NOT NULL;
COMMENT ON COLUMN ml_models.trained_at IS NULL;
"""


def upgrade():
    op.execute(UP_SQL)


def downgrade():
    op.execute(DOWN_SQL)
