"""
Миграция для добавления таблицы интеграций CRM для школ
Каждая школа может настроить свою CRM (1С, Битрикс24, AmoCRM и т.д.)
"""
import asyncio
from app.db.connection import get_db_connection


async def add_crm_integrations():
    """Добавляет таблицу для интеграций CRM школ"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Таблица интеграций CRM для школ
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_crm_integrations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    crm_type ENUM('1c', 'bitrix24', 'amocrm', 'custom', 'other') NOT NULL DEFAULT 'custom',
                    crm_name VARCHAR(255) NOT NULL,
                    
                    -- Настройки подключения
                    api_url VARCHAR(500),
                    api_key VARCHAR(500),
                    api_secret VARCHAR(500),
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expires_at TIMESTAMP NULL,
                    
                    -- Webhook настройки (для получения данных из CRM)
                    webhook_url VARCHAR(500),
                    webhook_secret VARCHAR(255),
                    
                    -- Настройки синхронизации
                    is_active BOOLEAN DEFAULT FALSE,
                    sync_direction ENUM('app_to_crm', 'crm_to_app', 'bidirectional') DEFAULT 'app_to_crm',
                    sync_frequency ENUM('realtime', 'hourly', 'daily', 'weekly', 'manual') DEFAULT 'daily',
                    last_sync_at TIMESTAMP NULL,
                    next_sync_at TIMESTAMP NULL,
                    
                    -- Данные для маппинга (JSON для хранения соответствий полей)
                    field_mapping JSON,
                    
                    -- Метаданные
                    metadata JSON,
                    notes TEXT,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_school_crm (school_id, crm_type, crm_name)
                )
            ''')
            
            # Таблица логов синхронизации
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS crm_sync_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    integration_id INT NOT NULL,
                    sync_type ENUM('lessons', 'enrollments', 'financial', 'students', 'teachers', 'full') NOT NULL,
                    direction ENUM('app_to_crm', 'crm_to_app') NOT NULL,
                    status ENUM('success', 'failed', 'partial') NOT NULL,
                    records_synced INT DEFAULT 0,
                    records_failed INT DEFAULT 0,
                    error_message TEXT,
                    sync_data JSON,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP NULL,
                    duration_seconds INT,
                    
                    FOREIGN KEY (integration_id) REFERENCES school_crm_integrations(id) ON DELETE CASCADE,
                    INDEX idx_integration_status (integration_id, status, started_at)
                )
            ''')
            
            await conn.commit()
            print("✅ Таблицы CRM интеграций созданы успешно")
    except Exception as e:
        print(f"❌ Ошибка при создании таблиц CRM интеграций: {e}")
        await conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_crm_integrations())
