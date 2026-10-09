"""Hide legacy per-user plans from school subscriptions."""

from alembic import op


revision = "20260628_0005"
down_revision = "20260628_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE subscription_plans
        SET is_active = FALSE
        WHERE name IN ('Student Plus', 'Teacher Pro')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE subscription_plans
        SET is_active = TRUE
        WHERE name IN ('Student Plus', 'Teacher Pro')
        """
    )
