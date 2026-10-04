"""Transactional invalidation for process-local BM25 snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0004_bm25_revision"
down_revision = "0003_legacy_chunks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("corpus_revision", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("revision", sa.BigInteger(), nullable=False))
    op.execute("INSERT INTO corpus_revision (id, revision) VALUES (1, 0)")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION bump_corpus_revision() RETURNS trigger AS $$
            BEGIN UPDATE corpus_revision SET revision = revision + 1 WHERE id = 1;
            RETURN NULL; END; $$ LANGUAGE plpgsql""")
        for table in ("chunks", "documents"):
            op.execute(f"""CREATE TRIGGER {table}_corpus_revision
                AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION bump_corpus_revision()""")
    else:
        for table in ("chunks", "documents"):
            for event in ("INSERT", "UPDATE", "DELETE"):
                op.execute(f"""CREATE TRIGGER {table}_corpus_{event.lower()}
                    AFTER {event} ON {table} BEGIN
                    UPDATE corpus_revision SET revision = revision + 1 WHERE id = 1;
                    END""")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table in ("chunks", "documents"):
            op.execute(f"DROP TRIGGER {table}_corpus_revision ON {table}")
        op.execute("DROP FUNCTION bump_corpus_revision()")
    else:
        for table in ("chunks", "documents"):
            for event in ("insert", "update", "delete"):
                op.execute(f"DROP TRIGGER {table}_corpus_{event}")
    op.drop_table("corpus_revision")
