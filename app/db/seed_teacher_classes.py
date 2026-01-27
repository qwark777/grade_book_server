"""
Скрипт для добавления тестовых данных: классы и связь учителя с классами
"""

import asyncio
from app.db.connection import get_db_connection


async def seed_teacher_classes():
    """Добавить тестовые классы и связать их с учителем"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # 1. Получаем ID учителя с логином "qweqwe"
            await cursor.execute("""
                SELECT id FROM users WHERE username = 'qweqwe' AND role = 'teacher'
            """)
            teacher_row = await cursor.fetchone()
            
            if not teacher_row:
                print("❌ Не найден учитель с логином 'qweqwe' в базе данных.")
                print("   Проверяю, есть ли пользователь с таким логином...")
                await cursor.execute("""
                    SELECT id, username, role FROM users WHERE username = 'qweqwe'
                """)
                user_row = await cursor.fetchone()
                if user_row:
                    print(f"   Найден пользователь: ID={user_row['id']}, username={user_row['username']}, role={user_row['role']}")
                    if user_row['role'] != 'teacher':
                        print(f"   ⚠️  Роль пользователя: '{user_row['role']}', но нужна 'teacher'")
                        print(f"   Обновляю роль на 'teacher'...")
                        await cursor.execute("""
                            UPDATE users SET role = 'teacher' WHERE username = 'qweqwe'
                        """)
                        teacher_id = user_row['id']
                        print(f"✅ Роль обновлена. Учитель с ID: {teacher_id}")
                    else:
                        teacher_id = user_row['id']
                        print(f"✅ Найден учитель с логином 'qweqwe', ID: {teacher_id}")
                else:
                    print("   ❌ Пользователь с логином 'qweqwe' не найден.")
                    print("   Создаю пользователя...")
                    from app.core.security import get_password_hash
                    await cursor.execute("""
                        INSERT INTO users (username, hashed_password, role)
                        VALUES ('qweqwe', %s, 'teacher')
                    """, (get_password_hash('qweqwe'),))
                    teacher_id = cursor.lastrowid
                    print(f"✅ Создан учитель с логином 'qweqwe', ID: {teacher_id}")
            else:
                teacher_id = teacher_row['id']
                print(f"✅ Найден учитель с логином 'qweqwe', ID: {teacher_id}")
            
            # 2. Создаем классы (если их еще нет)
            # Используем AUTO_INCREMENT для id, чтобы избежать конфликтов
            # Формат: (parallel, class_name, academic_year, display_name)
            classes_data = [
                ('10', 'А', '2024/2025', '10А'),
                ('10', 'Б', '2024/2025', '10Б'),
                ('11', 'А', '2024/2025', '11А'),
                ('9', 'А', '2024/2025', '9А'),
                ('9', 'Б', '2024/2025', '9Б'),
            ]
            
            class_ids = []
            for parallel, class_name, academic_year, display_name in classes_data:
                # Проверяем, существует ли класс (по разным возможным полям)
                await cursor.execute("""
                    SELECT id FROM classes 
                    WHERE (name = %s OR class_name = %s) AND academic_year = %s
                    LIMIT 1
                """, (display_name, class_name, academic_year))
                existing = await cursor.fetchone()
                
                if existing:
                    class_ids.append(existing['id'])
                    print(f"  - Класс {display_name} уже существует (ID: {existing['id']})")
                else:
                    # Пытаемся создать класс с разными вариантами полей
                    try:
                        # Вариант 1: с parallel и class_name
                        await cursor.execute("""
                            INSERT INTO classes (`parallel`, class_name, academic_year, name) 
                            VALUES (%s, %s, %s, %s)
                        """, (parallel, class_name, academic_year, display_name))
                        class_id = cursor.lastrowid
                        class_ids.append(class_id)
                        print(f"  - Создан класс {display_name} (ID: {class_id})")
                    except Exception as e1:
                        try:
                            # Вариант 2: только name и academic_year
                            await cursor.execute("""
                                INSERT INTO classes (name, academic_year) 
                                VALUES (%s, %s)
                            """, (display_name, academic_year))
                            class_id = cursor.lastrowid
                            class_ids.append(class_id)
                            print(f"  - Создан класс {display_name} (ID: {class_id})")
                        except Exception as e2:
                            print(f"  ⚠️  Не удалось создать класс {display_name}: {e1}, {e2}")
                            continue
            
            print("✅ Классы созданы/обновлены")
            
            # 3. Добавляем студентов в классы (если их еще нет)
            # Сначала получаем всех студентов
            await cursor.execute("""
                SELECT id FROM users WHERE role = 'student' LIMIT 20
            """)
            students = await cursor.fetchall()
            
            if not students:
                print("⚠️  Не найдены студенты. Создаю тестовых студентов...")
                # Создаем несколько тестовых студентов
                test_students = [
                    ('student1', 'Иванов Иван Иванович'),
                    ('student2', 'Петров Петр Петрович'),
                    ('student3', 'Сидоров Сидор Сидорович'),
                    ('student4', 'Козлов Козел Козлович'),
                    ('student5', 'Смирнов Смирн Смирнович'),
                    ('student6', 'Васильев Василий Васильевич'),
                    ('student7', 'Николаев Николай Николаевич'),
                    ('student8', 'Александров Александр Александрович'),
                    ('student9', 'Дмитриев Дмитрий Дмитриевич'),
                    ('student10', 'Андреев Андрей Андреевич'),
                ]
                
                from app.core.security import get_password_hash
                
                for username, full_name in test_students:
                    # Создаем пользователя
                    await cursor.execute("""
                        INSERT IGNORE INTO users (username, hashed_password, role)
                        VALUES (%s, %s, 'student')
                    """, (username, get_password_hash('password')))
                    
                    # Получаем ID созданного пользователя
                    await cursor.execute("""
                        SELECT id FROM users WHERE username = %s
                    """, (username,))
                    user_row = await cursor.fetchone()
                    
                    if user_row:
                        user_id = user_row['id']
                        # Создаем профиль
                        await cursor.execute("""
                            INSERT IGNORE INTO profiles (user_id, full_name)
                            VALUES (%s, %s)
                        """, (user_id, full_name))
                
                # Получаем всех студентов снова
                await cursor.execute("""
                    SELECT id FROM users WHERE role = 'student'
                """)
                students = await cursor.fetchall()
            
            print(f"✅ Найдено студентов: {len(students)}")
            
            # Распределяем студентов по классам
            students_per_class = len(students) // len(class_ids) if class_ids else 0
            student_index = 0
            
            for idx, class_id in enumerate(class_ids):
                display_name = classes_data[idx][3] if idx < len(classes_data) else f"Класс {class_id}"
                # Добавляем студентов в класс
                for i in range(students_per_class):
                    if student_index < len(students):
                        student_id = students[student_index]['id']
                        await cursor.execute("""
                            INSERT IGNORE INTO class_students (class_id, student_id)
                            VALUES (%s, %s)
                        """, (class_id, student_id))
                        student_index += 1
                
                # Получаем количество студентов в классе для проверки
                await cursor.execute("""
                    SELECT COUNT(*) as count FROM class_students WHERE class_id = %s
                """, (class_id,))
                count_row = await cursor.fetchone()
                count = count_row['count'] if count_row else 0
                print(f"  - Класс {class_name}: {count} студентов")
            
            # 4. Связываем учителя с классами
            for class_id in class_ids:
                await cursor.execute("""
                    INSERT IGNORE INTO class_teachers (class_id, teacher_id)
                    VALUES (%s, %s)
                """, (class_id, teacher_id))
            
            print(f"✅ Учитель (ID: {teacher_id}) связан с {len(classes_data)} классами")
            
            # 5. Проверяем результат
            await cursor.execute("""
                SELECT c.id, c.name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM class_teachers ct
                JOIN classes c ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
                ORDER BY c.name
            """, (teacher_id,))
            result = await cursor.fetchall()
            
            print("\n📊 Результат:")
            for row in result:
                print(f"  - {row['name']}: {row['student_count']} студентов")
            
            await conn.commit()
            print("\n✅ Тестовые данные успешно добавлены!")
            
    except Exception as e:
        print(f"❌ Ошибка при добавлении тестовых данных: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(seed_teacher_classes())
