"""
Миграция для добавления групповых чатов
"""
import asyncio
from app.db.connection import get_db_connection


async def add_group_chats():
    """Добавляет таблицы для групповых чатов"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Таблица групповых чатов
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS group_chats (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT NULL,
                    created_by INT NOT NULL,
                    photo_url VARCHAR(500) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            
            # Таблица участников групповых чатов
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS group_chat_members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_chat_id INT NOT NULL,
                    user_id INT NOT NULL,
                    role ENUM('admin', 'member') DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_chat_id) REFERENCES group_chats(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_member (group_chat_id, user_id),
                    INDEX idx_user_id (user_id),
                    INDEX idx_group_chat_id (group_chat_id)
                )
            ''')
            
            # Модифицируем таблицу messages - добавляем group_chat_id и делаем conversation_id nullable
            try:
                await cursor.execute('''
                    ALTER TABLE messages
                    ADD COLUMN group_chat_id INT NULL,
                    MODIFY COLUMN conversation_id INT NULL
                ''')
                await cursor.execute('''
                    ALTER TABLE messages
                    ADD FOREIGN KEY (group_chat_id) REFERENCES group_chats(id) ON DELETE CASCADE
                ''')
            except Exception as e:
                # Колонки уже существуют или другая ошибка
                print(f"Note: columns might already exist: {e}")
                pass
            
            # Добавляем проверку, что хотя бы один из conversation_id или group_chat_id должен быть заполнен
            # Это делается на уровне приложения, так как MySQL не поддерживает CHECK с OR
            
            # Создаем индекс для быстрого поиска сообщений в групповом чате
            try:
                await cursor.execute('''
                    CREATE INDEX idx_group_chat_messages ON messages(group_chat_id, created_at)
                ''')
            except Exception:
                pass  # Индекс уже существует
            
            # Таблица для отслеживания прочитанных сообщений в групповых чатах
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS group_chat_read_status (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_chat_id INT NOT NULL,
                    user_id INT NOT NULL,
                    last_read_message_id INT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_chat_id) REFERENCES group_chats(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (last_read_message_id) REFERENCES messages(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_user_group (group_chat_id, user_id),
                    INDEX idx_user_group (user_id, group_chat_id)
                )
            ''')
            
            await conn.commit()
            print("✅ Таблицы для групповых чатов созданы успешно")
            
    except Exception as e:
        print(f"❌ Ошибка при создании таблиц групповых чатов: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_group_chats())
