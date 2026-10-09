"""Store the last time a user was active.

Revision ID: 20260727_0014
Revises: 20260727_0013
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0014"
down_revision = "20260727_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {
        column["name"] for column in inspector.get_columns("users")
    }
    if "last_seen_at" not in user_columns:
        op.add_column(
            "users",
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        )

    if "login_log" in set(inspector.get_table_names()):
        op.execute(
            """
            UPDATE users u
            JOIN (
                SELECT user_id, MAX(created_at) AS last_seen_at
                FROM login_log
                GROUP BY user_id
            ) recent ON recent.user_id = u.id
            SET u.last_seen_at = recent.last_seen_at
            WHERE u.last_seen_at IS NULL
            """
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    user_columns = {
        column["name"] for column in inspector.get_columns("users")
    }
    if "last_seen_at" in user_columns:
        op.drop_column("users", "last_seen_at")
