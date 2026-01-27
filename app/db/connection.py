import aiomysql
from typing import Optional
from app.core.config import settings


async def get_db_connection():
    """Get database connection"""
    return await aiomysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        cursorclass=aiomysql.DictCursor,
        charset="utf8mb4",
        use_unicode=True
    )


async def init_db():
    """Initialize database tables"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Users table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password VARCHAR(255) NOT NULL,
                    role VARCHAR(50) DEFAULT 'student'
                )
            ''')
            
            # Profiles table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    work_place TEXT,
                    location TEXT,
                    bio TEXT,
                    photo_url TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
            
            # Schools table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS schools (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    address TEXT
                )
            ''')

            # Classes table (linked to schools)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS classes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    academic_year VARCHAR(20) NOT NULL,
                    school_id INT NULL,
                    CONSTRAINT fk_classes_school FOREIGN KEY (school_id) REFERENCES schools(id)
                )
            ''')
            
            # Class students table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS class_students (
                    class_id INT NOT NULL,
                    student_id INT NOT NULL,
                    PRIMARY KEY (class_id, student_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (student_id) REFERENCES users(id)
                )
            ''')
            
            # Class teachers table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS class_teachers (
                    class_id INT NOT NULL,
                    teacher_id INT NOT NULL,
                    PRIMARY KEY (class_id, teacher_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
                )
            ''')
            
            # Subjects table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS subjects (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) UNIQUE NOT NULL,
                    coefficient DECIMAL(3,2) DEFAULT 1.00
                )
            ''')
            
            # Teacher subjects (what teacher can teach)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS teacher_subjects (
                    teacher_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    PRIMARY KEY (teacher_id, subject_id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id)
                )
            ''')

            # Class subjects (what subjects the class has)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS class_subjects (
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    PRIMARY KEY (class_id, subject_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id)
                )
            ''')

            # Who teaches subject to a class
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS class_subject_teachers (
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    teacher_id INT NOT NULL,
                    PRIMARY KEY (class_id, subject_id, teacher_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
                )
            ''')
            
            # Grades table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS grades (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    value INT NOT NULL,
                    date DATE NOT NULL,
                    teacher_id INT NOT NULL,
                    FOREIGN KEY (student_id) REFERENCES users(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
                )
            ''')
            
            # Homeworks table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS homeworks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    due_date DATE NOT NULL,
                    description TEXT NOT NULL,
                    teacher_id INT NOT NULL,
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
                )
            ''')
            
            # Conversations table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user1_id INT NOT NULL,
                    user2_id INT NOT NULL,
                    user_min_id INT NOT NULL,
                    user_max_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_pair (user_min_id, user_max_id),
                    FOREIGN KEY (user1_id) REFERENCES users(id),
                    FOREIGN KEY (user2_id) REFERENCES users(id)
                )
            ''')
            
            # Messages table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    conversation_id INT NOT NULL,
                    sender_id INT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    read_at TIMESTAMP NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                    FOREIGN KEY (sender_id) REFERENCES users(id)
                )
            ''')
            
            # Add read_at column if it doesn't exist (for existing databases)
            try:
                await cursor.execute('''
                    ALTER TABLE messages 
                    ADD COLUMN read_at TIMESTAMP NULL
                ''')
            except Exception:
                pass  # Column already exists
            
            # Add last_read_message_id columns to conversations if they don't exist
            try:
                await cursor.execute('''
                    ALTER TABLE conversations 
                    ADD COLUMN user1_last_read_message_id INT NULL,
                    ADD COLUMN user2_last_read_message_id INT NULL
                ''')
            except Exception:
                pass  # Columns already exist
            
            # Rooms table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS rooms (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    building VARCHAR(50),
                    capacity INT,
                    room_type ENUM('classroom', 'gym', 'lab', 'auditorium', 'library') DEFAULT 'classroom',
                    is_active BOOLEAN DEFAULT TRUE
                )
            ''')
            
            # Academic periods table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS academic_periods (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Timetable templates table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS timetable_templates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    teacher_id INT NOT NULL,
                    room_id INT,
                    day_of_week TINYINT NOT NULL,
                    lesson_number TINYINT NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    week_type ENUM('A', 'B', 'BOTH') DEFAULT 'BOTH',
                    academic_period_id INT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    FOREIGN KEY (room_id) REFERENCES rooms(id),
                    FOREIGN KEY (academic_period_id) REFERENCES academic_periods(id),
                    UNIQUE KEY unique_lesson (class_id, day_of_week, lesson_number, week_type, academic_period_id)
                )
            ''')
            
            # Timetable changes table
            await cursor.execute('''
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
                    reason TEXT,
                    created_by INT NOT NULL,
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
                )
            ''')
            
            # Holidays table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS holidays (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    type ENUM('holiday', 'weekend', 'vacation') DEFAULT 'holiday',
                    affects_classes BOOLEAN DEFAULT TRUE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_holiday (date)
                )
            ''')
            
            # Academic weeks table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS academic_weeks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    week_start_date DATE NOT NULL,
                    week_end_date DATE NOT NULL,
                    week_number INT NOT NULL,
                    week_type ENUM('A', 'B') NOT NULL,
                    academic_period_id INT NOT NULL,
                    is_holiday_week BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (academic_period_id) REFERENCES academic_periods(id),
                    UNIQUE KEY unique_week (week_start_date, academic_period_id)
                )
            ''')

            # Achievements catalog
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS achievements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    code VARCHAR(255) NOT NULL UNIQUE,
                    title VARCHAR(255) NOT NULL,
                    image_url VARCHAR(512) NOT NULL,
                    rarity VARCHAR(50) DEFAULT NULL
                )
            ''')

            # User achievements
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_achievements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    achievement_id INT NOT NULL,
                    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_user_ach (user_id, achievement_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (achievement_id) REFERENCES achievements(id)
                )
            ''')

            # User points
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_points (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    points INT NOT NULL DEFAULT 0,
                    reason VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    UNIQUE KEY unique_user_points (user_id)
                )
            ''')

            # School admins mapping
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_admins (
                    school_id INT NOT NULL,
                    admin_user_id INT NOT NULL,
                    PRIMARY KEY (school_id, admin_user_id),
                    FOREIGN KEY (school_id) REFERENCES schools(id),
                    FOREIGN KEY (admin_user_id) REFERENCES users(id)
                )
            ''')

            # Subscription plans table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    price_monthly DECIMAL(10,2) NOT NULL DEFAULT 0,
                    price_yearly DECIMAL(10,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(3) DEFAULT 'USD',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Plan entitlements table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS plan_entitlements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    plan_id INT NOT NULL,
                    entitlement_key VARCHAR(100) NOT NULL,
                    entitlement_value VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_plan_entitlement (plan_id, entitlement_key)
                )
            ''')

            # School subscriptions table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_subscriptions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    plan_id INT NOT NULL,
                    status ENUM('active', 'past_due', 'suspended', 'canceled', 'trial') DEFAULT 'trial',
                    current_period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    current_period_end TIMESTAMP NOT NULL,
                    seats_students INT DEFAULT 0,
                    seats_teachers INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id),
                    FOREIGN KEY (plan_id) REFERENCES subscription_plans(id),
                    UNIQUE KEY unique_school_subscription (school_id)
                )
            ''')

            # User subscriptions table (for Student Plus, Teacher Pro)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    plan_id INT NOT NULL,
                    status ENUM('active', 'past_due', 'suspended', 'canceled', 'trial') DEFAULT 'trial',
                    current_period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    current_period_end TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (plan_id) REFERENCES subscription_plans(id),
                    UNIQUE KEY unique_user_subscription (user_id)
                )
            ''')

            # Usage tracking table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    user_id INT,
                    event_key VARCHAR(100) NOT NULL,
                    event_value DECIMAL(10,2) DEFAULT 1,
                    event_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    INDEX idx_school_date (school_id, event_date),
                    INDEX idx_event_key (event_key)
                )
            ''')

            # Subscription invoices table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscription_invoices (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    plan_id INT NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    currency VARCHAR(3) DEFAULT 'USD',
                    status ENUM('pending', 'paid', 'failed', 'refunded') DEFAULT 'pending',
                    period_start TIMESTAMP NOT NULL,
                    period_end TIMESTAMP NOT NULL,
                    due_date TIMESTAMP NOT NULL,
                    paid_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id),
                    FOREIGN KEY (plan_id) REFERENCES subscription_plans(id)
                )
            ''')
            
            # Attendance table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS attendance (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    date DATE NOT NULL,
                    status ENUM('present', 'late', 'absent') NOT NULL,
                    lesson_number TINYINT,
                    subject_id INT,
                    teacher_id INT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (student_id) REFERENCES users(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    UNIQUE KEY unique_attendance (student_id, date, lesson_number)
                )
            ''')
            
            # Create indexes for attendance table
            try:
                await cursor.execute('''
                    CREATE INDEX idx_attendance_student_date ON attendance(student_id, date)
                ''')
            except Exception:
                pass  # Index already exists
            
            try:
                await cursor.execute('''
                    CREATE INDEX idx_attendance_date ON attendance(date)
                ''')
            except Exception:
                pass  # Index already exists
            
            try:
                await cursor.execute('''
                    CREATE INDEX idx_attendance_status ON attendance(status)
                ''')
            except Exception:
                pass  # Index already exists
            
            # User balance table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_balance (
                    user_id INT PRIMARY KEY,
                    balance DECIMAL(10,2) DEFAULT 0.00,
                    currency VARCHAR(3) DEFAULT 'RUB',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            
            # Balance transactions table
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
                    reference_type VARCHAR(50) NULL,
                    reference_id INT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
                    INDEX idx_user_date (user_id, created_at),
                    INDEX idx_user_type (user_id, transaction_type)
                )
            ''')
            
            # Add purchase fields to lesson_enrollments if they don't exist
            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN purchase_price DECIMAL(10,2) NULL
                ''')
            except Exception:
                pass  # Column already exists
            
            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN purchase_date TIMESTAMP NULL
                ''')
            except Exception:
                pass  # Column already exists
            
            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN payment_method VARCHAR(50) NULL
                ''')
            except Exception:
                pass  # Column already exists
            
            # Добавляем колонку description в существующую таблицу achievements, если её нет
            try:
                await cursor.execute('''
                    ALTER TABLE achievements
                    ADD COLUMN description TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists
            
            await conn.commit()
    finally:
        conn.close()

