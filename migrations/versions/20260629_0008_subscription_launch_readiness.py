"""Harden school subscription billing for launch."""

from alembic import op
import sqlalchemy as sa


revision = "20260629_0008"
down_revision = "20260628_0007"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "unique_subscription_plan_name" not in _index_names("subscription_plans"):
        op.create_index(
            "unique_subscription_plan_name",
            "subscription_plans",
            ["name"],
            unique=True,
        )

    if "paid_at" not in _column_names("subscription_invoices"):
        op.add_column(
            "subscription_invoices",
            sa.Column("paid_at", sa.TIMESTAMP(), nullable=True),
        )

    if (
        "unique_subscription_provider_payment"
        not in _index_names("subscription_invoices")
    ):
        op.create_index(
            "unique_subscription_provider_payment",
            "subscription_invoices",
            ["provider_payment_id"],
            unique=True,
        )

    if "subscription_webhook_events" not in _table_names():
        op.create_table(
            "subscription_webhook_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("event_id", sa.String(length=255), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "provider",
                "event_id",
                name="unique_subscription_webhook_event",
            ),
        )

    op.execute(
        """
        DELETE FROM plan_entitlements
        WHERE plan_id IN (
            SELECT id FROM subscription_plans
            WHERE name IN ('Student Plus', 'Teacher Pro')
              AND id NOT IN (SELECT plan_id FROM school_subscriptions)
              AND id NOT IN (SELECT plan_id FROM subscription_invoices)
        )
        """
    )
    op.execute(
        """
        DELETE FROM subscription_plans
        WHERE name IN ('Student Plus', 'Teacher Pro')
          AND id NOT IN (SELECT plan_id FROM school_subscriptions)
          AND id NOT IN (SELECT plan_id FROM subscription_invoices)
        """
    )


def downgrade() -> None:
    if "subscription_webhook_events" in _table_names():
        op.drop_table("subscription_webhook_events")
    op.drop_index(
        "unique_subscription_provider_payment",
        table_name="subscription_invoices",
    )
    op.drop_index(
        "unique_subscription_plan_name",
        table_name="subscription_plans",
    )
    op.drop_column("subscription_invoices", "paid_at")
