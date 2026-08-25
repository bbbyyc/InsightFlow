"""Create the original InsightFlow schema on an empty database.

Revision ID: 0001_baseline
Revises: None
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    tables = _tables()
    uuid_type = sa.Uuid()
    json_type = sa.JSON()
    embedding_type = Vector(384) if bind.dialect.name == "postgresql" else sa.JSON()

    if "documents" not in tables:
        op.create_table("documents",
            sa.Column("id", uuid_type, primary_key=True), sa.Column("title", sa.String(500), nullable=False),
            sa.Column("file_type", sa.String(20), nullable=False), sa.Column("file_path", sa.String(1000), nullable=False),
            sa.Column("status", sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", name="documentstatus"), nullable=False), sa.Column("chunk_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    if "chunks" not in tables:
        op.create_table("chunks",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("document_id", uuid_type, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("content", sa.Text(), nullable=False), sa.Column("embedding", embedding_type),
            sa.Column("embedding_model", sa.String(255)), sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("page_number", sa.Integer()), sa.Column("section_title", sa.String(500)),
            sa.Column("token_count", sa.Integer(), server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
        op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    if "conversations" not in tables:
        op.create_table("conversations", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("title", sa.String(500), server_default="New Conversation"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    if "messages" not in tables:
        op.create_table("messages", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("conversation_id", uuid_type, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(20), nullable=False), sa.Column("content", sa.Text(), nullable=False),
            sa.Column("citations", json_type, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    if "conversation_documents" not in tables:
        op.create_table("conversation_documents", sa.Column("id", uuid_type, primary_key=True),
            sa.Column("conversation_id", uuid_type, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", uuid_type, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))

    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    for table in ("conversation_documents", "messages", "conversations", "chunks", "documents"):
        if table in _tables():
            op.drop_table(table)
