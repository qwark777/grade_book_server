"""Synchronize the canonical achievement catalog.

Revision ID: 20260726_0010
Revises: 20260630_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0010"
down_revision = "20260630_0009"
branch_labels = None
depends_on = None


CATALOG = (
    (
        "class_president",
        "Староста",
        "Назначается администрацией школы.",
        "/static/achievements/class_president.png",
        "rare",
    ),
    (
        "first_place",
        "Первое место",
        "Первое место в рейтинге своего класса.",
        "/static/achievements/first_place.png",
        "legendary",
    ),
    (
        "heart_of_class",
        "Сердце класса",
        "Отправлено 100 сообщений в школьных чатах.",
        "/static/achievements/heart_of_class.png",
        "heart",
    ),
    (
        "olimpiad_coach",
        "Олимпиадный наставник",
        "Выдаётся школой за подтверждённые олимпиадные достижения.",
        "/static/achievements/olimpiad_coach.png",
        "rare",
    ),
    (
        "politeness",
        "Вежливость",
        "Получено 20 положительных отзывов об уроках.",
        "/static/achievements/politeness.png",
        "common",
    ),
    (
        "second_place",
        "Второе место",
        "Второе место в рейтинге своего класса.",
        "/static/achievements/second_place.png",
        "common",
    ),
    (
        "third_place",
        "Третье место",
        "Третье место в рейтинге своего класса.",
        "/static/achievements/third_place.png",
        "common",
    ),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("achievements")
    }
    if "description" not in columns:
        op.add_column(
            "achievements",
            sa.Column("description", sa.Text(), nullable=True),
        )
    if "rarity" not in columns:
        op.add_column(
            "achievements",
            sa.Column("rarity", sa.String(length=50), nullable=True),
        )

    statement = sa.text(
        """
        INSERT INTO achievements
            (code, title, description, image_url, rarity)
        VALUES
            (:code, :title, :description, :image_url, :rarity)
        AS incoming
        ON DUPLICATE KEY UPDATE
            title = incoming.title,
            description = incoming.description,
            image_url = incoming.image_url,
            rarity = incoming.rarity
        """
    )
    connection = op.get_bind()
    for code, title, description, image_url, rarity in CATALOG:
        connection.execute(
            statement,
            {
                "code": code,
                "title": title,
                "description": description,
                "image_url": image_url,
                "rarity": rarity,
            },
        )


def downgrade() -> None:
    # Catalog rows may already be referenced by user_achievements.
    pass
