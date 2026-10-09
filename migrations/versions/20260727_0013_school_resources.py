"""Scope subjects and rooms through school resource registries.

Revision ID: 20260727_0013
Revises: 20260726_0012
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0013"
down_revision = "20260726_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "school_subjects" not in tables:
        op.create_table(
            "school_subjects",
            sa.Column("school_id", sa.Integer(), nullable=False),
            sa.Column("subject_id", sa.Integer(), nullable=False),
            sa.Column(
                "coefficient",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="1.00",
            ),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["subject_id"], ["subjects.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("school_id", "subject_id"),
        )
        op.create_index(
            "idx_school_subjects_subject",
            "school_subjects",
            ["subject_id", "school_id"],
        )

    if "school_rooms" not in tables:
        op.create_table(
            "school_rooms",
            sa.Column("school_id", sa.Integer(), nullable=False),
            sa.Column("room_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["room_id"], ["rooms.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("school_id", "room_id"),
        )

    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT DISTINCT c.school_id, cs.subject_id, s.coefficient
        FROM class_subjects cs
        JOIN classes c ON c.id = cs.class_id
        JOIN subjects s ON s.id = cs.subject_id
        WHERE c.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT DISTINCT su.school_id, ts.subject_id, s.coefficient
        FROM teacher_subjects ts
        JOIN school_users su ON su.user_id = ts.teacher_id
        JOIN subjects s ON s.id = ts.subject_id
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT DISTINCT c.school_id, cu.subject_id, s.coefficient
        FROM curriculum cu
        JOIN classes c ON c.id = cu.class_id
        JOIN subjects s ON s.id = cu.subject_id
        WHERE c.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT DISTINCT c.school_id, g.subject_id, s.coefficient
        FROM grades g
        JOIN class_students cs ON cs.student_id = g.student_id
        JOIN classes c ON c.id = cs.class_id
        JOIN subjects s ON s.id = g.subject_id
        WHERE c.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT DISTINCT c.school_id, t.subject_id, s.coefficient
        FROM timetable_templates t
        JOIN classes c ON c.id = t.class_id
        JOIN subjects s ON s.id = t.subject_id
        WHERE c.school_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_subjects
            (school_id, subject_id, coefficient)
        SELECT sc.id, s.id, s.coefficient
        FROM schools sc
        CROSS JOIN subjects s
        WHERE (SELECT COUNT(*) FROM schools) = 1
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_rooms (school_id, room_id)
        SELECT DISTINCT c.school_id, t.room_id
        FROM timetable_templates t
        JOIN classes c ON c.id = t.class_id
        WHERE c.school_id IS NOT NULL AND t.room_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO school_rooms (school_id, room_id)
        SELECT sc.id, r.id
        FROM schools sc
        CROSS JOIN rooms r
        WHERE (SELECT COUNT(*) FROM schools) = 1
        """
    )


def downgrade() -> None:
    op.drop_table("school_rooms")
    op.drop_index(
        "idx_school_subjects_subject",
        table_name="school_subjects",
    )
    op.drop_table("school_subjects")
