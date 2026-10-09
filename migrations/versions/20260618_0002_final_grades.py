"""add final grades table

Revision ID: 20260618_0002
Revises: 20260611_0001
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260618_0002"
down_revision = "20260611_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "final_grades" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "final_grades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("academic_period_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("source_average", sa.Numeric(4, 2), nullable=True),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.ForeignKeyConstraint(["academic_period_id"], ["academic_periods.id"]),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"]),
        sa.UniqueConstraint(
            "student_id",
            "class_id",
            "subject_id",
            "academic_period_id",
            name="unique_final_grade",
        ),
    )


def downgrade() -> None:
    op.drop_table("final_grades")
