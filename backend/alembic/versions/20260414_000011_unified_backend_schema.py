"""Add auth, billing, and session tables for unified backend

Revision ID: 20260414_000011
Revises: 20260414_000010
"""
from alembic import op
import sqlalchemy as sa

revision = "20260414_000011"
down_revision = "20260414_000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend users table ────────────────────────────────
    # Columns may already exist if a previous run partially succeeded
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c['name'] for c in inspector.get_columns('users')}
    if "display_name" not in existing_cols:
        op.add_column("users", sa.Column("display_name", sa.String(128), nullable=True))
    if "email_verified" not in existing_cols:
        op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=True, server_default=sa.text("false")))
    if "deleted_at" not in existing_cols:
        op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "updated_at" not in existing_cols:
        op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    # Back-fill email_verified for existing rows
    op.execute("UPDATE users SET email_verified = false WHERE email_verified IS NULL")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("email_verified", nullable=False)

    # ── oauth_accounts ────────────────────────────────────
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False, index=True),
        sa.Column("provider_account_id", sa.String(255), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("id_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_account_id"),
    )

    # ── user_sessions ─────────────────────────────────────
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("refresh_token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── email_verifications ───────────────────────────────
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── billing_customers ─────────────────────────────────
    op.create_table(
        "billing_customers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_customer_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider"),
        sa.UniqueConstraint("provider", "provider_customer_id"),
    )

    # ── billing_subscriptions ─────────────────────────────
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_subscription_id", sa.String(255), nullable=False),
        sa.Column("provider_customer_id", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("price_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_subscription_id"),
    )

    # ── billing_purchases ─────────────────────────────────
    op.create_table(
        "billing_purchases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_payment_intent_id", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("price_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_payment_intent_id"),
    )

    # ── billing_checkout_sessions ─────────────────────────
    op.create_table(
        "billing_checkout_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_session_id", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("price_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_session_id"),
    )

    # ── billing_events (webhook idempotency) ──────────────
    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_event_id"),
    )


def downgrade() -> None:
    op.drop_table("billing_events")
    op.drop_table("billing_checkout_sessions")
    op.drop_table("billing_purchases")
    op.drop_table("billing_subscriptions")
    op.drop_table("billing_customers")
    op.drop_table("email_verifications")
    op.drop_table("user_sessions")
    op.drop_table("oauth_accounts")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "display_name")
