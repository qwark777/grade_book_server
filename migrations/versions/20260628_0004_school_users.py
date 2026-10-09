"""Add direct school membership registry for licensed seats."""

from alembic import op
import sqlalchemy as sa


revision = "20260628_0004"
down_revision = "20260619_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "school_users" not in inspector.get_table_names():
        op.create_table(
            "school_users",
            sa.Column("school_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=50), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("school_id", "user_id"),
        )
        op.create_index(
            "idx_school_users_role",
            "school_users",
            ["school_id", "role"],
        )
    elif "idx_school_users_role" not in {
        index["name"]
        for index in inspector.get_indexes("school_users")
    }:
        op.create_index(
            "idx_school_users_role",
            "school_users",
            ["school_id", "role"],
        )

    op.execute(
        """
        INSERT IGNORE INTO school_users (school_id, user_id, role)
        SELECT school_id, admin_user_id, 'admin' FROM school_admins
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_users (school_id, user_id, role)
        SELECT DISTINCT c.school_id, cs.student_id, 'student'
        FROM class_students cs
        JOIN classes c ON c.id = cs.class_id
        WHERE c.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_users (school_id, user_id, role)
        SELECT DISTINCT c.school_id, ct.teacher_id, 'teacher'
        FROM class_teachers ct
        JOIN classes c ON c.id = ct.class_id
        WHERE c.school_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("idx_school_users_role", table_name="school_users")
    op.drop_table("school_users")
