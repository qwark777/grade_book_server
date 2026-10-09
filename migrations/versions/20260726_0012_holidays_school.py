"""Scope holidays to a school.

Revision ID: 20260726_0012
Revises: 20260726_0011
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0012"
down_revision = "20260726_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("holidays")
    }
    if "school_id" not in columns:
        op.add_column(
            "holidays",
            sa.Column("school_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_holidays_school",
            "holidays",
            "schools",
            ["school_id"],
            ["id"],
            ondelete="CASCADE",
        )

    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(
            "holidays"
        )
    }
    if "unique_holiday" in indexes:
        op.drop_index("unique_holiday", table_name="holidays")
    if "unique_holiday_school_date" not in indexes:
        op.create_index(
            "unique_holiday_school_date",
            "holidays",
            ["school_id", "date"],
            unique=True,
        )

    op.execute(
        """
        UPDATE holidays
        SET school_id = (SELECT MIN(id) FROM schools)
        WHERE school_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("unique_holiday_school_date", table_name="holidays")
    op.create_index("unique_holiday", "holidays", ["date"], unique=True)
    op.drop_constraint(
        "fk_holidays_school",
        "holidays",
        type_="foreignkey",
    )
    op.drop_column("holidays", "school_id")
