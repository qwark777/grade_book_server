"""
Скрипт для добавления достижений в каталог и выдачи их пользователям
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.connection import get_db_connection


async def add_achievement_to_catalog(code: str, title: str, description: str = None, image_url: str = None):
    """Добавить достижение в каталог"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO achievements (code, title, description, image_url)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    title = VALUES(title),
                    description = COALESCE(VALUES(description), description),
                    image_url = COALESCE(VALUES(image_url), image_url)
            """, (code, title, description, image_url))
            await conn.commit()
            print(f"✓ Добавлено достижение: {title} (code: {code})")
    finally:
        conn.close()


async def award_achievement_to_user(user_id: int, achievement_code: str):
    """Выдать достижение пользователю"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Находим ID достижения по коду
            await cursor.execute("SELECT id FROM achievements WHERE code = %s", (achievement_code,))
            row = await cursor.fetchone()
            
            if not row:
                print(f"✗ Достижение с кодом '{achievement_code}' не найдено")
                return False
            
            achievement_id = row['id']
            
            # Выдаем достижение пользователю
            await cursor.execute("""
                INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                VALUES (%s, %s, NOW())
            """, (user_id, achievement_id))
            
            if cursor.rowcount > 0:
                await conn.commit()
                print(f"✓ Достижение '{achievement_code}' выдано пользователю с ID {user_id}")
                return True
            else:
                print(f"⚠ Пользователь {user_id} уже имеет достижение '{achievement_code}'")
                return False
    finally:
        conn.close()


async def revoke_achievement_from_user(user_id: int, achievement_code: str = None, title: str = None):
    """Забрать достижение у пользователя по коду или названию"""
    if not achievement_code and not title:
        print("✗ Нужно указать --code или --title для отзыва достижения")
        return False

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if achievement_code:
                await cursor.execute("SELECT id, code, title FROM achievements WHERE code = %s", (achievement_code,))
            else:
                await cursor.execute("SELECT id, code, title FROM achievements WHERE title = %s", (title,))
            row = await cursor.fetchone()

            if not row:
                target = achievement_code or title
                print(f"✗ Достижение '{target}' не найдено")
                return False

            achievement_id = row['id']
            await cursor.execute(
                "DELETE FROM user_achievements WHERE user_id = %s AND achievement_id = %s",
                (user_id, achievement_id),
            )

            if cursor.rowcount > 0:
                await conn.commit()
                print(f"✓ Достижение '{row['title']}' удалено у пользователя {user_id}")
                return True

            print(f"⚠ У пользователя {user_id} нет достижения '{row['title']}'")
            return False
    finally:
        conn.close()

async def seed_default_achievements():
    """Добавить стандартные достижения в каталог"""
    achievements = [
        {
            'code': 'first_grade',
            'title': 'Первая оценка',
            'description': 'Получена первая оценка в системе',
            'image_url': '/static/achievements/first_grade.png'
        },
        {
            'code': 'excellent_student',
            'title': 'Отличник',
            'description': 'Все оценки за период - отлично',
            'image_url': '/static/achievements/excellent_student.png'
        },
        {
            'code': 'perfect_attendance',
            'title': 'Идеальная посещаемость',
            'description': '100% посещаемость за месяц',
            'image_url': '/static/achievements/perfect_attendance.png'
        },
        {
            'code': 'homework_master',
            'title': 'Мастер домашних заданий',
            'description': 'Выполнено 50 домашних заданий',
            'image_url': '/static/achievements/homework_master.png'
        },
        {
            'code': 'week_warrior',
            'title': 'Воин недели',
            'description': 'Активность всю неделю подряд',
            'image_url': '/static/achievements/week_warrior.png'
        },
        {
            'code': 'social_butterfly',
            'title': 'Социальная бабочка',
            'description': 'Отправлено 100 сообщений',
            'image_url': '/static/achievements/social_butterfly.png'
        },
    ]
    
    for ach in achievements:
        await add_achievement_to_catalog(
            ach['code'],
            ach['title'],
            ach['description'],
            ach['image_url']
        )


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Управление достижениями')
    parser.add_argument(
        'action',
        choices=['seed', 'add', 'award', 'revoke'],
        help='Действие: seed - заполнить каталог, add - добавить достижение, award - выдать достижение, revoke - забрать достижение',
    )
    parser.add_argument('--code', help='Код достижения')
    parser.add_argument('--title', help='Название достижения')
    parser.add_argument('--description', help='Описание достижения')
    parser.add_argument('--image-url', help='URL изображения')
    parser.add_argument('--user-id', type=int, help='ID пользователя (для award/revoke)')
    
    args = parser.parse_args()
    
    if args.action == 'seed':
        print("Добавление стандартных достижений в каталог...")
        await seed_default_achievements()
        print("\nГотово!")
    
    elif args.action == 'add':
        if not args.code or not args.title:
            print("Ошибка: для добавления достижения нужны --code и --title")
            return
        await add_achievement_to_catalog(
            args.code,
            args.title,
            args.description,
            args.image_url
        )
    
    elif args.action == 'award':
        if not args.user_id or not args.code:
            print("Ошибка: для выдачи достижения нужны --user-id и --code")
            return
        await award_achievement_to_user(args.user_id, args.code)

    elif args.action == 'revoke':
        if not args.user_id:
            print("Ошибка: для отзыва достижения нужен --user-id и --code или --title")
            return
        await revoke_achievement_from_user(args.user_id, args.code, args.title)


if __name__ == '__main__':
    asyncio.run(main())
