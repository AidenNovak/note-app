"""Personal Access Tokens for CLI / automation.

Adds:
  - api_tokens (user_id, name, token_prefix, token_hash, scopes, last_used_at,
    expires_at, revoked_at, created_at)

Tokens are plaintext `atl_<base32>` presented once at creation; only the
sha256 hash is stored. Scopes: space-separated subset of {read, write, admin}.

Revision ID: 20260417_000015
Revises: 20260417_000014
"""
import sqlalchemy as sa
from alembic import op


revision = "20260417_000015"
down_revision = "20260417_000014"


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column(
            "scopes",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'read'"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_prefix", "api_tokens", ["token_prefix"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("ix_api_tokens_token_prefix", table_name="api_tokens")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
