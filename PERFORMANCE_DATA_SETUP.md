# Настройка данных успеваемости

## 🚀 Быстрый старт

### 1. Запуск сервера с новыми данными

```bash
cd /Users/qwark/Documents/GitHub/grade_book_server

# Активируем виртуальное окружение
source venv/bin/activate

# Инициализируем данные успеваемости
python init_performance_data.py

# Запускаем сервер
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### 2. Проверка API

После запуска сервера доступны новые эндпоинты:

- `GET /api/v1/attendance-stats/{student_id}` - статистика посещаемости
- `GET /api/v1/attendance-heatmap/{student_id}` - данные для тепловой карты
- `GET /api/v1/grade-distribution/{student_id}` - распределение оценок
- `GET /api/v1/performance-trends/{student_id}` - тренды успеваемости
- `GET /api/v1/subject-performance/{student_id}/{subject_id}` - данные по предмету

### 3. Тестирование

```bash
# Получить статистику посещаемости для студента с ID=2
curl -H "Authorization: Bearer YOUR_TOKEN" \
     http://localhost:8001/api/v1/attendance-stats/2

# Получить данные тепловой карты
curl -H "Authorization: Bearer YOUR_TOKEN" \
     http://localhost:8001/api/v1/attendance-heatmap/2
```

## 📊 Что было реализовано

### Серверная часть:
- ✅ Новые модели данных (AttendanceStats, AttendanceHeatmapData, etc.)
- ✅ API эндпоинты для получения данных успеваемости
- ✅ Таблица `attendance` для хранения посещаемости
- ✅ Тестовые данные за последние 30 дней
- ✅ SQL запросы для агрегации данных

### Android приложение:
- ✅ Новые модели данных в AuthManager
- ✅ API методы для загрузки данных с сервера
- ✅ Обновленный PerformanceAttendanceFragment
- ✅ Загрузка реальных данных с fallback на демо-данные
- ✅ Тепловая карта с реальными данными

## 🔧 Структура данных

### Таблица attendance:
```sql
CREATE TABLE attendance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    date DATE NOT NULL,
    status ENUM('present', 'late', 'absent') NOT NULL,
    lesson_number TINYINT,
    subject_id INT,
    teacher_id INT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Модели данных:
- `AttendanceStats` - статистика посещаемости
- `AttendanceHeatmapData` - данные для тепловой карты
- `GradeDistribution` - распределение оценок
- `PerformanceTrend` - тренды успеваемости
- `SubjectPerformance` - данные по предмету

## 🎯 Следующие шаги

1. **PerformanceOverviewFragment** - заменить демо-данные оценок
2. **PerformanceTrendsFragment** - заменить демо-данные трендов
3. **SubjectDetailsFragment** - заменить демо-данные по предметам
4. **ProfileFragment** - исправить значения по умолчанию
5. **TimetableFragment** - реализовать получение classId

## 🐛 Отладка

Если данные не загружаются:

1. Проверьте, что сервер запущен на порту 8001
2. Убедитесь, что токен авторизации валиден
3. Проверьте логи сервера на наличие ошибок
4. Убедитесь, что в базе есть студенты с соответствующими ID

## 📝 Логи

Логи сервера покажут:
- Создание таблиц
- Заполнение тестовыми данными
- API запросы и ответы
- Ошибки базы данных
