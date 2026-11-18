"""Persistent campaigns, stream drafts and idempotent operations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '2s'")
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schema_name", sa.String(32), nullable=False),
        sa.Column("offers", postgresql.JSONB(), nullable=False),
        sa.Column("remote_signature", sa.String(64), nullable=False),
        sa.Column("draft_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_streams_campaign_id", "streams", ["campaign_id"], postgresql_concurrently=True
        )
    op.create_table(
        "operations",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '2s'")
    op.drop_table("operations")
    op.drop_table("streams")
    op.drop_table("campaigns")
