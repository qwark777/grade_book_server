"""
Скрипт для создания достижений из файлов в папке achievements/ и выдачи их пользователю
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.connection import get_db_connection
from app.core.config import settings


async def create_achievements_from_folder(user_id: int = 123):
    """Создать достижения из файлов в папке achievements/ и выдать их пользователю"""
    achievements_dir = settings.ACHIEVEMENTS_DIR
    
    if not os.path.exists(achievements_dir):
        print(f"✗ Папка {achievements_dir} не найдена")
        return
    
    # Получаем все файлы изображений
    image_files = [
        f for f in os.listdir(achievements_dir)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
    ]
    
    if not image_files:
        print(f"✗ В папке {achievements_dir} нет файлов изображений")
        return
    
    print(f"Найдено {len(image_files)} файлов изображений")
    print("-" * 50)
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            created_count = 0
            awarded_count = 0
            
            for filename in image_files:
                # Получаем код и расширение
                base_name = os.path.splitext(filename)[0]
                ext = os.path.splitext(filename)[1].lower()
                
                # Генерируем название из имени файла
                title = base_name.replace('_', ' ').title()
                
                # Генерируем описание
                description = f"Достижение: {title}"
                
                # URL изображения
                image_url = f"/static/achievements/{filename}"
                
                # Создаем или обновляем достижение в каталоге
                await cursor.execute("""
                    INSERT INTO achievements (code, title, description, image_url)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        title = VALUES(title),
                        description = COALESCE(VALUES(description), description),
                        image_url = COALESCE(VALUES(image_url), image_url)
                """, (base_name, title, description, image_url))
                
                created_count += 1
                print(f"✓ Создано достижение: {title} (code: {base_name})")
                
                # Получаем ID созданного достижения
                await cursor.execute("SELECT id FROM achievements WHERE code = %s", (base_name,))
                row = await cursor.fetchone()
                
                if row:
                    achievement_id = row['id']
                    
                    # Выдаем достижение пользователю
                    await cursor.execute("""
                        INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                        VALUES (%s, %s, NOW())
                    """, (user_id, achievement_id))
                    
                    if cursor.rowcount > 0:
                        awarded_count += 1
                        print(f"  → Выдано пользователю {user_id}")
                    else:
                        print(f"  → Пользователь {user_id} уже имеет это достижение")
            
            await conn.commit()
            
            print("-" * 50)
            print(f"✓ Готово!")
            print(f"  Создано достижений: {created_count}")
            print(f"  Выдано пользователю {user_id}: {awarded_count}")
            
    finally:
        conn.close()


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Создать достижения из папки и выдать пользователю')
    parser.add_argument('--user-id', type=int, default=123, help='ID пользователя (по умолчанию: 123)')
    
    args = parser.parse_args()
    
    print(f"Создание достижений из папки {settings.ACHIEVEMENTS_DIR}...")
    print(f"Пользователь ID: {args.user_id}")
    print()
    
    await create_achievements_from_folder(args.user_id)


if __name__ == '__main__':
    asyncio.run(main())
