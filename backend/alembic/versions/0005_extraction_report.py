"""Store page-level PDF extraction provenance."""
from alembic import op
import sqlalchemy as sa

revision = "0005_extraction_report"
down_revision = "0004_bm25_revision"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("extraction_report", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("documents", "extraction_report")
