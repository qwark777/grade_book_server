-- Схема базы данных для системы расписания

-- Таблица для хранения учебных периодов (семестры, четверти)
CREATE TABLE IF NOT EXISTS academic_periods (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL, -- "1 семестр 2024/2025", "2 четверть"
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Таблица для хранения шаблонов расписания (основное расписание)
CREATE TABLE IF NOT EXISTS timetable_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    subject_id INT NOT NULL,
    teacher_id INT NOT NULL,
    room_id INT,
    day_of_week TINYINT NOT NULL, -- 1=Понедельник, 2=Вторник, ..., 7=Воскресенье
    lesson_number TINYINT NOT NULL, -- 1, 2, 3, 4, 5, 6, 7
    start_time TIME NOT NULL, -- "08:00:00"
    end_time TIME NOT NULL,   -- "08:45:00"
    week_type ENUM('A', 'B', 'BOTH') DEFAULT 'BOTH', -- A, B или обе недели
    academic_period_id INT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id),
    FOREIGN KEY (teacher_id) REFERENCES users(id),
    FOREIGN KEY (academic_period_id) REFERENCES academic_periods(id),
    UNIQUE KEY unique_lesson (class_id, day_of_week, lesson_number, week_type, academic_period_id)
);

-- Таблица для хранения комнат/кабинетов
CREATE TABLE IF NOT EXISTS rooms (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL, -- "301", "Спортзал", "Лаборатория"
    building VARCHAR(50), -- "Главный корпус", "Спортивный комплекс"
    capacity INT,
    room_type ENUM('classroom', 'gym', 'lab', 'auditorium', 'library') DEFAULT 'classroom',
    is_active BOOLEAN DEFAULT TRUE
);

-- Таблица для хранения замен (временных изменений)
CREATE TABLE IF NOT EXISTS timetable_changes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    class_id INT NOT NULL,
    lesson_number TINYINT NOT NULL,
    change_type ENUM('replace', 'cancel', 'move', 'add') NOT NULL,
    original_teacher_id INT,
    new_teacher_id INT,
    original_subject_id INT,
    new_subject_id INT,
    original_room_id INT,
    new_room_id INT,
    reason TEXT, -- "Учитель на совещании", "Замена по болезни"
    created_by INT NOT NULL, -- кто создал замену
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id),
    FOREIGN KEY (original_teacher_id) REFERENCES users(id),
    FOREIGN KEY (new_teacher_id) REFERENCES users(id),
    FOREIGN KEY (original_subject_id) REFERENCES subjects(id),
    FOREIGN KEY (new_subject_id) REFERENCES subjects(id),
    FOREIGN KEY (original_room_id) REFERENCES rooms(id),
    FOREIGN KEY (new_room_id) REFERENCES rooms(id),
    FOREIGN KEY (created_by) REFERENCES users(id),
    UNIQUE KEY unique_change (date, class_id, lesson_number)
);

-- Таблица для хранения праздников и выходных
CREATE TABLE IF NOT EXISTS holidays (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    name VARCHAR(100) NOT NULL, -- "Новый год", "День учителя"
    type ENUM('holiday', 'weekend', 'vacation') DEFAULT 'holiday',
    affects_classes BOOLEAN DEFAULT TRUE, -- влияет ли на учебный процесс
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_holiday (date)
);

-- Таблица для хранения календаря учебных недель
CREATE TABLE IF NOT EXISTS academic_weeks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    week_start_date DATE NOT NULL,
    week_end_date DATE NOT NULL,
    week_number INT NOT NULL, -- 1, 2, 3, 4, 5, 6, 7, 8, 9, 10...
    week_type ENUM('A', 'B') NOT NULL, -- A или B
    academic_period_id INT NOT NULL,
    is_holiday_week BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (academic_period_id) REFERENCES academic_periods(id),
    UNIQUE KEY unique_week (week_start_date, academic_period_id)
);

-- Индексы для оптимизации запросов
CREATE INDEX idx_timetable_templates_class_date ON timetable_templates(class_id, day_of_week, week_type);
CREATE INDEX idx_timetable_changes_date_class ON timetable_changes(date, class_id);
CREATE INDEX idx_holidays_date ON holidays(date);
CREATE INDEX idx_academic_weeks_date ON academic_weeks(week_start_date, week_end_date);

