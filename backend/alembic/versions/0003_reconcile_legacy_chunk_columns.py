"""Reconcile columns omitted by legacy pre-Alembic chunk tables.

Revision ID: 0003_legacy_chunks
Revises: 0002_product_persistence
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_legacy_chunks"
down_revision = "0002_product_persistence"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "embedding_model" not in _columns("chunks"):
        op.add_column("chunks", sa.Column("embedding_model", sa.String(255), nullable=True))


def downgrade():
    if "embedding_model" in _columns("chunks"):
        op.drop_column("chunks", "embedding_model")
