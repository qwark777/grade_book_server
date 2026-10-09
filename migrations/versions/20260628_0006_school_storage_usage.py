"""Add transactional school storage usage counters."""

from alembic import op
import sqlalchemy as sa


revision = "20260628_0006"
down_revision = "20260628_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "school_storage_usage" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "school_storage_usage",
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column(
            "used_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("school_id"),
    )


def downgrade() -> None:
    op.drop_table("school_storage_usage")
