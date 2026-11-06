"""Durable background ML runs without changing existing datasets or models."""
from alembic import op
revision='web0003'
down_revision='web0002'
branch_labels=None
depends_on=None
UP_SQL="""
CREATE TABLE web_training_runs (
 id uuid PRIMARY KEY,
 project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 source_dataset_id bigint NOT NULL,
 created_by bigint NOT NULL REFERENCES users(id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
 request_key varchar(100) NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','interrupted','cancelled')),
 params jsonb NOT NULL CHECK(jsonb_typeof(params)='object'),
 input_snapshot jsonb NOT NULL CHECK(jsonb_typeof(input_snapshot)='object'),
 progress text NOT NULL DEFAULT 'В очереди',
 output jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(output)='object'),
 error text,
 cancel_requested boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz,
 FOREIGN KEY(project_id,source_dataset_id) REFERENCES datasets(project_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,created_by,request_key),
 CHECK((status IN ('succeeded','failed','interrupted','cancelled'))=(finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_web_training_active ON web_training_runs(project_id) WHERE status IN ('queued','running');
CREATE INDEX ix_web_training_queue ON web_training_runs(status,created_at);
CREATE FUNCTION guard_web_training_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.id,NEW.project_id,NEW.source_dataset_id,NEW.created_by,NEW.request_key,NEW.params,NEW.input_snapshot,NEW.created_at)
    IS DISTINCT FROM (OLD.id,OLD.project_id,OLD.source_dataset_id,OLD.created_by,OLD.request_key,OLD.params,OLD.input_snapshot,OLD.created_at) THEN
  RAISE EXCEPTION 'Training input identity is immutable';
 END IF;
 IF OLD.status NOT IN ('queued','running') THEN RAISE EXCEPTION 'Finished training run is immutable'; END IF;
 IF (OLD.status='queued' AND NEW.status NOT IN ('queued','running','cancelled','interrupted')) OR
    (OLD.status='running' AND NEW.status NOT IN ('running','succeeded','failed','interrupted','cancelled')) THEN
  RAISE EXCEPTION 'Invalid training status transition';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_web_training_guard BEFORE UPDATE ON web_training_runs FOR EACH ROW EXECUTE FUNCTION guard_web_training_run();
"""
DOWN_SQL="""
DO $$ BEGIN IF EXISTS(SELECT 1 FROM web_training_runs) THEN
 RAISE EXCEPTION 'Refusing to remove nonempty training history'; END IF; END $$;
DROP TABLE web_training_runs;
DROP FUNCTION guard_web_training_run();
"""
def upgrade():op.execute(UP_SQL)
def downgrade():op.execute(DOWN_SQL)
