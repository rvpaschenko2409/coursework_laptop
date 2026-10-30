"""Initial PostgreSQL schema, variant 27. Frozen SQL revision."""
from pathlib import Path
from alembic import op
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))

def downgrade():
    op.execute(Path(__file__).with_name('0001_down.sql').read_text(encoding='utf-8'))
