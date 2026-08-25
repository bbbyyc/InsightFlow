"""Add product persistence, task tracking, citations and agent events.

Revision ID: 0002_product_persistence
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_product_persistence"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add_column(table, column):
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    uuid_type = sa.Uuid()
    json_type = sa.JSON()
    _add_column("documents", sa.Column("content_sha256", sa.String(64)))
    _add_column("documents", sa.Column("error_message", sa.Text()))
    _add_column("documents", sa.Column("processed_at", sa.DateTime(timezone=True)))
    _add_column("documents", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _add_column("chunks", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.execute("UPDATE chunks SET content_sha256 = '' WHERE content_sha256 IS NULL")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("chunks") as batch:
            batch.alter_column("content_sha256", existing_type=sa.String(64), nullable=False)
    else:
        op.alter_column("chunks", "content_sha256", existing_type=sa.String(64), nullable=False)
    _add_column("conversations", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))

    tables = _tables()
    if "task_records" not in tables:
        op.create_table("task_records",
            sa.Column("id", uuid_type, primary_key=True), sa.Column("task_type", sa.String(50), nullable=False),
            sa.Column("status", sa.Enum("PENDING", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED", "CANCELLED", name="taskstatus"), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
            sa.Column("celery_task_id", sa.String(255), unique=True), sa.Column("resource_type", sa.String(50)),
            sa.Column("resource_id", uuid_type), sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result_summary", json_type, nullable=False, server_default="{}"),
            sa.Column("error_type", sa.String(255)), sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        op.create_index("ix_task_records_status", "task_records", ["status"])
        op.create_index("ix_task_records_task_type", "task_records", ["task_type"])
        op.create_index("ix_task_records_resource_id", "task_records", ["resource_id"])
    if "retrieval_records" not in tables:
        op.create_table("retrieval_records",
            sa.Column("id", uuid_type, primary_key=True), sa.Column("conversation_id", uuid_type),
            sa.Column("query", sa.Text(), nullable=False), sa.Column("rewritten_query", sa.Text()),
            sa.Column("mode", sa.String(20), nullable=False), sa.Column("document_ids", json_type, nullable=False, server_default="[]"),
            sa.Column("result_chunk_ids", json_type, nullable=False, server_default="[]"),
            sa.Column("diagnostics", json_type, nullable=False, server_default="{}"), sa.Column("top_k", sa.Integer(), nullable=False),
            sa.Column("latency_ms", sa.Float()), sa.Column("status", sa.String(20), nullable=False),
            sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)))
        op.create_index("ix_retrieval_records_conversation_id", "retrieval_records", ["conversation_id"])
    if "retrieval_record_id" not in _columns("messages"):
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("messages") as batch:
                batch.add_column(sa.Column("retrieval_record_id", uuid_type, nullable=True))
                batch.create_foreign_key("fk_messages_retrieval_record", "retrieval_records", ["retrieval_record_id"], ["id"], ondelete="SET NULL")
        else:
            op.add_column("messages", sa.Column("retrieval_record_id", uuid_type, sa.ForeignKey("retrieval_records.id", ondelete="SET NULL")))
    tables = _tables()
    if "citations" not in tables:
        op.create_table("citations", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("message_id", uuid_type, sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("retrieval_record_id", uuid_type, sa.ForeignKey("retrieval_records.id", ondelete="SET NULL")),
            sa.Column("chunk_id", uuid_type, sa.ForeignKey("chunks.id", ondelete="SET NULL")),
            sa.Column("document_id", uuid_type, sa.ForeignKey("documents.id", ondelete="SET NULL")),
            sa.Column("citation_number", sa.Integer(), nullable=False), sa.Column("document_title", sa.String(500), nullable=False),
            sa.Column("section_title", sa.String(500)), sa.Column("content_snippet", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("message_id", "citation_number", name="uq_citations_message_number"))
        op.create_index("ix_citations_message_id", "citations", ["message_id"])
    if "agent_runs" not in tables:
        op.create_table("agent_runs", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("conversation_id", uuid_type, sa.ForeignKey("conversations.id", ondelete="SET NULL")),
            sa.Column("query", sa.Text(), nullable=False), sa.Column("document_ids", json_type, nullable=False, server_default="[]"),
            sa.Column("status", sa.String(20), nullable=False), sa.Column("current_node", sa.String(50)),
            sa.Column("answer_summary", sa.Text()), sa.Column("error_message", sa.Text()),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)))
        op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    if "agent_node_events" not in tables:
        op.create_table("agent_node_events", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("agent_run_id", uuid_type, sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("node", sa.String(50), nullable=False),
            sa.Column("status", sa.String(20), nullable=False), sa.Column("duration_ms", sa.Float()),
            sa.Column("input_summary", json_type, nullable=False, server_default="{}"),
            sa.Column("result_summary", json_type, nullable=False, server_default="{}"), sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_node_events_run_sequence"))
        op.create_index("ix_agent_node_events_agent_run_id", "agent_node_events", ["agent_run_id"])
    if "evaluation_runs" not in tables:
        op.create_table("evaluation_runs", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("task_id", uuid_type, sa.ForeignKey("task_records.id", ondelete="SET NULL")),
            sa.Column("dataset_path", sa.String(1000), nullable=False), sa.Column("dataset_sha256", sa.String(64)),
            sa.Column("status", sa.String(20), nullable=False), sa.Column("parameters", json_type, nullable=False, server_default="{}"),
            sa.Column("output_dir", sa.String(1000), nullable=False), sa.Column("summary", json_type, nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)))
        op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])

    # Deduplicate before applying unique constraints to an existing prototype database.
    op.execute("DELETE FROM conversation_documents a USING conversation_documents b WHERE a.id > b.id AND a.conversation_id = b.conversation_id AND a.document_id = b.document_id" if op.get_bind().dialect.name == "postgresql" else "DELETE FROM conversation_documents WHERE rowid NOT IN (SELECT MIN(rowid) FROM conversation_documents GROUP BY conversation_id, document_id)")
    existing_uq = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints("conversation_documents")}
    if "uq_conversation_documents_pair" not in existing_uq:
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("conversation_documents") as batch:
                batch.create_unique_constraint("uq_conversation_documents_pair", ["conversation_id", "document_id"])
        else:
            op.create_unique_constraint("uq_conversation_documents_pair", "conversation_documents", ["conversation_id", "document_id"])
    existing_chunk_uq = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints("chunks")}
    if "uq_chunks_document_index" not in existing_chunk_uq:
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("chunks") as batch:
                batch.create_unique_constraint("uq_chunks_document_index", ["document_id", "chunk_index"])
        else:
            op.create_unique_constraint("uq_chunks_document_index", "chunks", ["document_id", "chunk_index"])
    existing_chunk_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("chunks")}
    if "ix_chunks_document_id" not in existing_chunk_indexes:
        op.create_index("ix_chunks_document_id", "chunks", ["document_id"])


def downgrade():
    for table in ("evaluation_runs", "agent_node_events", "agent_runs", "citations"):
        if table in _tables():
            op.drop_table(table)
    if "retrieval_record_id" in _columns("messages"):
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("messages") as batch:
                batch.drop_constraint("fk_messages_retrieval_record", type_="foreignkey")
                batch.drop_column("retrieval_record_id")
        else:
            op.drop_constraint("messages_retrieval_record_id_fkey", "messages", type_="foreignkey")
            op.drop_column("messages", "retrieval_record_id")
    for table in ("retrieval_records", "task_records"):
        if table in _tables():
            op.drop_table(table)

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("conversation_documents") as batch:
            batch.drop_constraint("uq_conversation_documents_pair", type_="unique")
        with op.batch_alter_table("chunks") as batch:
            batch.drop_constraint("uq_chunks_document_index", type_="unique")
            batch.drop_column("content_sha256")
        with op.batch_alter_table("conversations") as batch:
            batch.drop_column("updated_at")
        with op.batch_alter_table("documents") as batch:
            batch.drop_column("updated_at")
            batch.drop_column("processed_at")
            batch.drop_column("error_message")
            batch.drop_column("content_sha256")
    else:
        op.drop_constraint("uq_conversation_documents_pair", "conversation_documents", type_="unique")
        op.drop_constraint("uq_chunks_document_index", "chunks", type_="unique")
        op.drop_column("chunks", "content_sha256")
        op.drop_column("conversations", "updated_at")
        for column in ("updated_at", "processed_at", "error_message", "content_sha256"):
            op.drop_column("documents", column)
        if dialect == "postgresql":
            op.execute("DROP TYPE IF EXISTS taskstatus")
