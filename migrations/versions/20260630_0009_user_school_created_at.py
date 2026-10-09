"""Add creation timestamps used by owner analytics.

Revision ID: 20260630_0009
Revises: 20260629_0008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260630_0009"
down_revision = "20260629_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("users", "schools"):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "created_at" not in columns:
            op.add_column(
                table_name,
                sa.Column(
                    "created_at",
                    sa.TIMESTAMP(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("schools", "users"):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "created_at" in columns:
            op.drop_column(table_name, "created_at")
