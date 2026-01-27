"""
Миграция для добавления баланса пользователей и истории покупок занятий
"""
import asyncio
from app.db.connection import get_db_connection


async def add_user_balance():
    """Добавляет таблицы для баланса пользователей и покупок занятий"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Таблица баланса пользователей
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_balance (
                    user_id INT PRIMARY KEY,
                    balance DECIMAL(10,2) DEFAULT 0.00,
                    currency VARCHAR(3) DEFAULT 'RUB',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            
            # Таблица истории транзакций баланса
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance_transactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    transaction_type ENUM('deposit', 'withdrawal', 'payment', 'refund', 'bonus', 'adjustment') NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    balance_before DECIMAL(10,2) NOT NULL,
                    balance_after DECIMAL(10,2) NOT NULL,
                    currency VARCHAR(3) DEFAULT 'RUB',
                    description TEXT,
                    reference_type VARCHAR(50) NULL, -- 'lesson_purchase', 'subscription', 'admin_add', etc.
                    reference_id INT NULL, -- ID связанной записи (lesson_id, subscription_id, etc.)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INT NULL, -- ID пользователя/админа, который создал транзакцию
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
                    INDEX idx_user_date (user_id, created_at),
                    INDEX idx_user_type (user_id, transaction_type)
                )
            ''')
            
            # Обновляем таблицу lesson_enrollments - добавляем поля для покупок
            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN purchase_price DECIMAL(10,2) NULL,
                    ADD COLUMN purchase_date TIMESTAMP NULL,
                    ADD COLUMN payment_method VARCHAR(50) NULL,
                    ADD COLUMN transaction_id INT NULL
                ''')
            except Exception:
                pass  # Колонки уже существуют
            
            # Добавляем внешний ключ для transaction_id, если колонка была добавлена
            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD FOREIGN KEY (transaction_id) REFERENCES balance_transactions(id) ON DELETE SET NULL
                ''')
            except Exception:
                pass  # Внешний ключ уже существует или колонка не существует
            
            await conn.commit()
            print("✅ Таблицы для баланса и покупок занятий созданы успешно")
            
    except Exception as e:
        print(f"❌ Ошибка при создании таблиц баланса: {e}")
        await conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_user_balance())
