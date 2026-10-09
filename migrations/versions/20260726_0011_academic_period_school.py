"""Scope academic periods to a school.

Revision ID: 20260726_0011
Revises: 20260726_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0011"
down_revision = "20260726_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("academic_periods")
    }
    if "school_id" not in columns:
        op.add_column(
            "academic_periods",
            sa.Column("school_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_academic_periods_school",
            "academic_periods",
            "schools",
            ["school_id"],
            ["id"],
            ondelete="CASCADE",
        )

    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(
            "academic_periods"
        )
    }
    if "idx_academic_periods_school_dates" not in indexes:
        op.create_index(
            "idx_academic_periods_school_dates",
            "academic_periods",
            ["school_id", "start_date", "end_date"],
        )

    op.execute(
        """
        UPDATE academic_periods
        SET school_id = (SELECT MIN(id) FROM schools)
        WHERE school_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "idx_academic_periods_school_dates",
        table_name="academic_periods",
    )
    op.drop_constraint(
        "fk_academic_periods_school",
        "academic_periods",
        type_="foreignkey",
    )
    op.drop_column("academic_periods", "school_id")
