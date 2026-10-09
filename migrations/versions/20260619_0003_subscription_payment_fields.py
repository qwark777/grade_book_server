"""Add payment metadata to subscription invoices."""

from alembic import op
import sqlalchemy as sa


revision = "20260619_0003"
down_revision = "20260618_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("subscription_invoices")
    }
    for name, column in (
        ("payment_method", sa.Column("payment_method", sa.String(length=50))),
        (
            "provider_payment_id",
            sa.Column("provider_payment_id", sa.String(length=255)),
        ),
        ("payment_note", sa.Column("payment_note", sa.Text())),
        (
            "confirmed_by_user_id",
            sa.Column("confirmed_by_user_id", sa.Integer()),
        ),
    ):
        if name not in columns:
            op.add_column("subscription_invoices", column)

    confirmed_by_foreign_key_exists = any(
        foreign_key.get("constrained_columns") == ["confirmed_by_user_id"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(
            "subscription_invoices"
        )
    )
    if not confirmed_by_foreign_key_exists:
        op.create_foreign_key(
            "fk_subscription_invoices_confirmed_by_user",
            "subscription_invoices",
            "users",
            ["confirmed_by_user_id"],
            ["id"],
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_subscription_invoices_confirmed_by_user",
        "subscription_invoices",
        type_="foreignkey",
    )
    op.drop_column("subscription_invoices", "confirmed_by_user_id")
    op.drop_column("subscription_invoices", "payment_note")
    op.drop_column("subscription_invoices", "provider_payment_id")
    op.drop_column("subscription_invoices", "payment_method")
