import aiomysql
from typing import Optional
from app.core.config import settings
import asyncio

_db_pool: Optional[aiomysql.Pool] = None


class PooledConnectionProxy:
    def __init__(self, pool: aiomysql.Pool, conn: aiomysql.Connection):
        self._pool = pool
        self._conn = conn
        self._released = False

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self):
        if not self._released:
            try:
                if not self._conn.get_autocommit():
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        loop.create_task(self._conn.rollback())
            except Exception:
                pass
            self._pool.release(self._conn)
            self._released = True


async def init_db_pool():
    """Initialize shared database connection pool."""
    global _db_pool
    if _db_pool is not None:
        return _db_pool

    _db_pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        minsize=settings.DB_POOL_MIN_SIZE,
        maxsize=settings.DB_POOL_MAX_SIZE,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        cursorclass=aiomysql.DictCursor,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=True,
    )
    return _db_pool


async def close_db_pool():
    """Close shared database pool on shutdown."""
    global _db_pool
    if _db_pool is not None:
        _db_pool.close()
        await _db_pool.wait_closed()
        _db_pool = None


async def get_db_connection():
    """Get database connection"""
    pool = await init_db_pool()
    conn = await pool.acquire()
    return PooledConnectionProxy(pool, conn)


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
                    role VARCHAR(50) DEFAULT 'student',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    address TEXT,
                    email VARCHAR(255) NULL,
                    phone VARCHAR(255) NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            for table_name in ("users", "schools"):
                try:
                    await cursor.execute(
                        f"""
                        ALTER TABLE {table_name}
                        ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        """
                    )
                except Exception as exc:
                    if getattr(exc, "args", [None])[0] != 1060:
                        raise
            try:
                await cursor.execute(
                    "ALTER TABLE users ADD COLUMN email VARCHAR(320) NULL"
                )
            except Exception as exc:
                if getattr(exc, "args", [None])[0] != 1060:
                    raise
            try:
                await cursor.execute(
                    "ALTER TABLE users ADD COLUMN last_seen_at DATETIME NULL"
                )
            except Exception as exc:
                if getattr(exc, "args", [None])[0] != 1060:
                    raise
            try:
                await cursor.execute(
                    "CREATE UNIQUE INDEX uq_users_email ON users (email)"
                )
            except Exception as exc:
                if getattr(exc, "args", [None])[0] not in (1061, 1831):
                    raise
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    token_hash CHAR(64) NOT NULL UNIQUE,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_password_reset_user (user_id),
                    INDEX idx_password_reset_expiry (expires_at),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_settings (
                    school_id INT PRIMARY KEY,
                    feature_flags JSON NULL,
                    workflow_settings JSON NULL,
                    updated_by INT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
                )
            ''')
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NULL,
                    user_id INT NULL,
                    action VARCHAR(100) NOT NULL,
                    entity_type VARCHAR(50) NULL,
                    entity_id INT NULL,
                    details JSON NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                    INDEX idx_audit_school_created (school_id, created_at),
                    INDEX idx_audit_user_created (user_id, created_at)
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_crm_integrations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    crm_type VARCHAR(50) NOT NULL,
                    crm_name VARCHAR(255) NOT NULL,
                    api_url VARCHAR(1024) NULL,
                    api_key TEXT NULL,
                    api_secret TEXT NULL,
                    access_token TEXT NULL,
                    webhook_url VARCHAR(1024) NULL,
                    webhook_secret TEXT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    sync_direction VARCHAR(32) DEFAULT 'app_to_crm',
                    sync_frequency VARCHAR(32) DEFAULT 'daily',
                    last_sync_at TIMESTAMP NULL,
                    next_sync_at TIMESTAMP NULL,
                    field_mapping JSON NULL,
                    metadata JSON NULL,
                    notes TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_school_crm_integrations_school (school_id),
                    INDEX idx_school_crm_integrations_type (crm_type),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE
                )
            ''')

            # Classes table (linked to schools)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS classes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    `parallel` VARCHAR(20) NULL,
                    class_name VARCHAR(50) NULL,
                    academic_year VARCHAR(20) NOT NULL,
                    school_id INT NULL,
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                    CONSTRAINT fk_classes_school FOREIGN KEY (school_id) REFERENCES schools(id)
                )
            ''')
            for column_sql in (
                "ADD COLUMN `parallel` VARCHAR(20) NULL",
                "ADD COLUMN class_name VARCHAR(50) NULL",
                "ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE",
            ):
                try:
                    await cursor.execute(f"ALTER TABLE classes {column_sql}")
                except Exception as exc:
                    if getattr(exc, "args", [None])[0] != 1060:
                        raise
            for column_name in ("email", "phone"):
                try:
                    await cursor.execute(
                        f"ALTER TABLE schools ADD COLUMN {column_name} VARCHAR(255) NULL"
                    )
                except Exception as exc:
                    if getattr(exc, "args", [None])[0] != 1060:
                        raise
            
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

            # Class and parent invitation tokens.
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS class_invites (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    token VARCHAR(64) NOT NULL UNIQUE,
                    invite_type VARCHAR(20) NOT NULL DEFAULT 'student',
                    school_id INT NOT NULL,
                    class_id INT NULL,
                    student_id INT NULL,
                    created_by INT NULL,
                    expires_at DATETIME NOT NULL,
                    revoked_at DATETIME NULL,
                    used_count INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_class_invites_class_type (class_id, invite_type, revoked_at, expires_at),
                    INDEX idx_class_invites_student_type (student_id, invite_type, revoked_at, expires_at),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
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
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_subjects (
                    school_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    coefficient DECIMAL(5,2) NOT NULL DEFAULT 1.00,
                    PRIMARY KEY (school_id, subject_id),
                    INDEX idx_school_subjects_subject (subject_id, school_id),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
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
                    lesson_topic VARCHAR(255) NULL,
                    grade_reason VARCHAR(255) NULL,
                    comment TEXT NULL,
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
                    submission_deadline_at DATETIME NULL,
                    description TEXT NOT NULL,
                    assessment_type VARCHAR(32) NULL,
                    prep_checklist_json TEXT NULL,
                    attachment_path VARCHAR(512) NULL,
                    attachment_name VARCHAR(255) NULL,
                    teacher_id INT NOT NULL,
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
                )
            ''')
            # Homework submissions (фото/файлы от учеников)
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS homework_submissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    homework_id INT NOT NULL,
                    student_id INT NOT NULL,
                    file_path VARCHAR(512) NOT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    student_comment TEXT NULL,
                    grade INT NULL,
                    is_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                    review_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    comment TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (homework_id) REFERENCES homeworks(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id)
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS homework_done_marks (
                    homework_id INT NOT NULL,
                    student_id INT NOT NULL,
                    marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (homework_id, student_id),
                    FOREIGN KEY (homework_id) REFERENCES homeworks(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id)
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS teacher_lesson_feedback (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    teacher_id INT NOT NULL,
                    student_id INT NOT NULL,
                    lesson_date DATE NOT NULL,
                    lesson_number INT NOT NULL,
                    rating INT NOT NULL,
                    comment TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_teacher_lesson_feedback (
                        student_id, class_id, subject_id, teacher_id, lesson_date, lesson_number
                    ),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    FOREIGN KEY (student_id) REFERENCES users(id)
                )
            ''')


            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_advice_feedback (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    advice_id VARCHAR(255) NOT NULL,
                    student_id INT NOT NULL,
                    is_useful TINYINT(1) NOT NULL,
                    comment TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_ai_advice_feedback (student_id, advice_id),
                    INDEX idx_ai_advice_feedback_student (student_id),
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    recipient_id INT NOT NULL,
                    sender_id INT NULL,
                    type VARCHAR(64) NOT NULL DEFAULT 'system',
                    title VARCHAR(255) NOT NULL,
                    body TEXT NOT NULL,
                    entity_type VARCHAR(64) NULL,
                    entity_id INT NULL,
                    route_name VARCHAR(100) NULL,
                    route_params JSON NULL,
                    is_read TINYINT(1) NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    read_at TIMESTAMP NULL DEFAULT NULL,
                    INDEX idx_notifications_recipient_created (recipient_id, created_at),
                    INDEX idx_notifications_recipient_read (recipient_id, is_read, created_at),
                    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE SET NULL
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
                    attachment_path VARCHAR(512) NULL,
                    attachment_name VARCHAR(255) NULL,
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

            try:
                await cursor.execute('''
                    ALTER TABLE messages
                    ADD COLUMN attachment_path VARCHAR(512) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE messages
                    ADD COLUMN attachment_name VARCHAR(255) NULL
                ''')
            except Exception:
                pass
            
            # Add last_read_message_id columns to conversations if they don't exist
            try:
                await cursor.execute('''
                    ALTER TABLE conversations 
                    ADD COLUMN user1_last_read_message_id INT NULL,
                    ADD COLUMN user2_last_read_message_id INT NULL
                ''')
            except Exception:
                pass  # Columns already exist

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
                    INDEX idx_group_chat_member_user (user_id),
                    INDEX idx_group_chat_member_group (group_chat_id)
                )
            ''')
            try:
                await cursor.execute(
                    "ALTER TABLE messages ADD COLUMN group_chat_id INT NULL"
                )
            except Exception:
                pass
            try:
                await cursor.execute(
                    "ALTER TABLE messages MODIFY COLUMN conversation_id INT NULL"
                )
            except Exception:
                pass
            try:
                await cursor.execute(
                    "ALTER TABLE messages ADD CONSTRAINT fk_messages_group_chat "
                    "FOREIGN KEY (group_chat_id) REFERENCES group_chats(id) ON DELETE CASCADE"
                )
            except Exception:
                pass
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
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_rooms (
                    school_id INT NOT NULL,
                    room_id INT NOT NULL,
                    PRIMARY KEY (school_id, room_id),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
                )
            ''')
            
            # Academic periods table
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS academic_periods (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NULL,
                    name VARCHAR(100) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_academic_periods_school_dates
                        (school_id, start_date, end_date),
                    CONSTRAINT fk_academic_periods_school
                        FOREIGN KEY (school_id) REFERENCES schools(id)
                        ON DELETE CASCADE
                )
            ''')

            # Final grades for academic periods
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS final_grades (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    academic_period_id INT NOT NULL,
                    value INT NOT NULL,
                    source_average DECIMAL(4,2) NULL,
                    teacher_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_final_grade (student_id, class_id, subject_id, academic_period_id),
                    FOREIGN KEY (student_id) REFERENCES users(id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (academic_period_id) REFERENCES academic_periods(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id)
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
                    school_id INT NULL,
                    date DATE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    type ENUM('holiday', 'weekend', 'vacation') DEFAULT 'holiday',
                    affects_classes BOOLEAN DEFAULT TRUE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_holiday_school_date (school_id, date),
                    CONSTRAINT fk_holidays_school
                        FOREIGN KEY (school_id) REFERENCES schools(id)
                        ON DELETE CASCADE
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

            # Direct school membership used for subscription seat limits.
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_users (
                    school_id INT NOT NULL,
                    user_id INT NOT NULL,
                    role VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (school_id, user_id),
                    INDEX idx_school_users_role (school_id, role),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS parent_students (
                    parent_id INT NOT NULL,
                    student_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (parent_id, student_id),
                    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS school_storage_usage (
                    school_id INT PRIMARY KEY,
                    used_bytes BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE
                )
            ''')
            await cursor.execute('''
                INSERT IGNORE INTO school_users (school_id, user_id, role)
                SELECT school_id, admin_user_id, 'admin'
                FROM school_admins
            ''')
            await cursor.execute('''
                INSERT IGNORE INTO school_users (school_id, user_id, role)
                SELECT DISTINCT c.school_id, cs.student_id, 'student'
                FROM class_students cs
                JOIN classes c ON c.id = cs.class_id
                WHERE c.school_id IS NOT NULL
            ''')
            await cursor.execute('''
                INSERT IGNORE INTO school_users (school_id, user_id, role)
                SELECT DISTINCT c.school_id, ct.teacher_id, 'teacher'
                FROM class_teachers ct
                JOIN classes c ON c.id = ct.class_id
                WHERE c.school_id IS NOT NULL
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_subscription_plan_name (name)
                )
            ''')

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_plans
                    ADD UNIQUE KEY unique_subscription_plan_name (name)
                ''')
            except Exception:
                pass

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
                    payment_method VARCHAR(50) NULL,
                    provider_payment_id VARCHAR(255) NULL,
                    payment_note TEXT NULL,
                    confirmed_by_user_id INT NULL,
                    paid_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id),
                    FOREIGN KEY (plan_id) REFERENCES subscription_plans(id),
                    FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id)
                )
            ''')

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD COLUMN payment_method VARCHAR(50) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD COLUMN provider_payment_id VARCHAR(255) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD COLUMN payment_note TEXT NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD COLUMN confirmed_by_user_id INT NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD COLUMN paid_at TIMESTAMP NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD UNIQUE KEY unique_subscription_provider_payment
                    (provider_payment_id)
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE subscription_invoices
                    ADD CONSTRAINT fk_subscription_invoices_confirmed_by_user
                    FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id)
                ''')
            except Exception:
                pass

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscription_webhook_events (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    provider VARCHAR(50) NOT NULL,
                    event_id VARCHAR(255) NOT NULL,
                    payload_hash CHAR(64) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_subscription_webhook_event
                        (provider, event_id)
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

            # Teacher notes about students
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS teacher_student_notes (
                    teacher_id INT NOT NULL,
                    student_id INT NOT NULL,
                    note TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (teacher_id, student_id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    FOREIGN KEY (student_id) REFERENCES users(id)
                )
            ''')
            
            # Parent-students binding
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS parent_students (
                    parent_id INT NOT NULL,
                    student_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (parent_id, student_id),
                    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')

            # Paid additional lessons must exist before their enrollments,
            # sessions and reviews are created on a fresh database.
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS additional_lessons (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT NULL,
                    subject VARCHAR(100) NOT NULL,
                    tutor_id INT NOT NULL,
                    school_id INT NULL,
                    price DECIMAL(10,2) NULL,
                    max_students INT DEFAULT 10,
                    duration_minutes INT DEFAULT 60,
                    is_online BOOLEAN DEFAULT FALSE,
                    location TEXT NULL,
                    online_link VARCHAR(500) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (tutor_id) REFERENCES users(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (school_id) REFERENCES schools(id)
                        ON DELETE SET NULL,
                    INDEX idx_additional_lessons_tutor (tutor_id),
                    INDEX idx_additional_lessons_school (school_id)
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_enrollments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    student_id INT NOT NULL,
                    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status ENUM('enrolled', 'completed', 'cancelled')
                        DEFAULT 'enrolled',
                    payment_status ENUM('pending', 'paid', 'refunded')
                        DEFAULT 'pending',
                    purchase_price DECIMAL(10,2) NULL,
                    purchase_date TIMESTAMP NULL,
                    payment_method VARCHAR(50) NULL,
                    total_lessons INT NOT NULL DEFAULT 1,
                    remaining_lessons INT NOT NULL DEFAULT 1,
                    assigned_by INT NULL,
                    valid_until DATETIME NULL,
                    notes TEXT NULL,
                    UNIQUE KEY unique_enrollment (lesson_id, student_id),
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (assigned_by) REFERENCES users(id)
                        ON DELETE SET NULL,
                    INDEX idx_lesson_enrollments_student (student_id)
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

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN total_lessons INT NOT NULL DEFAULT 1
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN remaining_lessons INT NOT NULL DEFAULT 1
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN assigned_by INT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN valid_until DATETIME NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD COLUMN notes TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE lesson_enrollments
                    ADD CONSTRAINT fk_lesson_enrollments_assigned_by
                    FOREIGN KEY (assigned_by) REFERENCES users(id) ON DELETE SET NULL
                ''')
            except Exception:
                pass  # FK already exists

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_student_sessions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    enrollment_id INT NULL,
                    student_id INT NOT NULL,
                    tutor_id INT NOT NULL,
                    school_id INT NULL,
                    session_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    is_online BOOLEAN DEFAULT FALSE,
                    location TEXT NULL,
                    online_link VARCHAR(500) NULL,
                    status ENUM('scheduled', 'completed', 'cancelled') DEFAULT 'scheduled',
                    lessons_reserved INT NOT NULL DEFAULT 1,
                    notes TEXT NULL,
                    created_by INT NULL,
                    cancelled_at DATETIME NULL,
                    completed_at DATETIME NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id) ON DELETE CASCADE,
                    FOREIGN KEY (enrollment_id) REFERENCES lesson_enrollments(id) ON DELETE SET NULL,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (tutor_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
                    INDEX idx_lesson_student_sessions_student_date (student_id, session_date),
                    INDEX idx_lesson_student_sessions_tutor_date (tutor_id, session_date),
                    INDEX idx_lesson_student_sessions_lesson_date (lesson_id, session_date),
                    INDEX idx_lesson_student_sessions_status (status)
                )
            ''')

            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_reviews (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    student_id INT NOT NULL,
                    rating INT NOT NULL,
                    comment TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE KEY uniq_lesson_reviews_lesson_student (lesson_id, student_id),
                    INDEX idx_lesson_reviews_lesson (lesson_id),
                    INDEX idx_lesson_reviews_student (student_id)
                )
            ''')
            
            # Добавляем колонку description в существующую таблицу achievements, если её нет
            try:
                await cursor.execute('''
                    ALTER TABLE achievements
                    ADD COLUMN description TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homework_submissions
                    ADD COLUMN student_comment TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homework_submissions
                    ADD COLUMN grade INT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homework_submissions
                    ADD COLUMN is_reviewed BOOLEAN NOT NULL DEFAULT FALSE
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homework_submissions
                    ADD COLUMN review_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homework_submissions
                    ADD COLUMN comment TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE grades
                    ADD COLUMN lesson_topic VARCHAR(255) NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE grades
                    ADD COLUMN grade_reason VARCHAR(255) NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE grades
                    ADD COLUMN comment TEXT NULL
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN need_submission BOOLEAN NOT NULL DEFAULT FALSE
                ''')
            except Exception:
                pass  # Column already exists

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN attachment_path VARCHAR(512) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN attachment_name VARCHAR(255) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN submission_deadline_at DATETIME NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN assessment_type VARCHAR(32) NULL
                ''')
            except Exception:
                pass

            try:
                await cursor.execute('''
                    ALTER TABLE homeworks
                    ADD COLUMN prep_checklist_json TEXT NULL
                ''')
            except Exception:
                pass

            # Prevent a student from being assigned to multiple classes in the same school.
            # We enforce this at DB level so any code path inserting into class_students
            # gets the same rule.
            try:
                await cursor.execute("DROP TRIGGER IF EXISTS class_students_single_school_insert")
            except Exception:
                pass

            await cursor.execute("""
                CREATE TRIGGER class_students_single_school_insert
                BEFORE INSERT ON class_students
                FOR EACH ROW
                BEGIN
                    DECLARE new_school_id INT;
                    DECLARE duplicate_count INT DEFAULT 0;

                    SELECT school_id INTO new_school_id
                    FROM classes
                    WHERE id = NEW.class_id
                    LIMIT 1;

                    IF new_school_id IS NOT NULL THEN
                        SELECT COUNT(*)
                          INTO duplicate_count
                        FROM class_students cs
                        JOIN classes c ON c.id = cs.class_id
                        WHERE cs.student_id = NEW.student_id
                          AND c.school_id = new_school_id
                          AND cs.class_id <> NEW.class_id;

                        IF duplicate_count > 0 THEN
                            SIGNAL SQLSTATE '45000'
                                SET MESSAGE_TEXT = 'Student cannot belong to multiple classes in the same school';
                        END IF;
                    END IF;
                END
            """)

            try:
                await cursor.execute("DROP TRIGGER IF EXISTS class_students_single_school_update")
            except Exception:
                pass

            await cursor.execute("""
                CREATE TRIGGER class_students_single_school_update
                BEFORE UPDATE ON class_students
                FOR EACH ROW
                BEGIN
                    DECLARE new_school_id INT;
                    DECLARE duplicate_count INT DEFAULT 0;

                    SELECT school_id INTO new_school_id
                    FROM classes
                    WHERE id = NEW.class_id
                    LIMIT 1;

                    IF new_school_id IS NOT NULL THEN
                        SELECT COUNT(*)
                          INTO duplicate_count
                        FROM class_students cs
                        JOIN classes c ON c.id = cs.class_id
                        WHERE cs.student_id = NEW.student_id
                          AND c.school_id = new_school_id
                          AND NOT (cs.class_id = OLD.class_id AND cs.student_id = OLD.student_id);

                        IF duplicate_count > 0 THEN
                            SIGNAL SQLSTATE '45000'
                                SET MESSAGE_TEXT = 'Student cannot belong to multiple classes in the same school';
                        END IF;
                    END IF;
                END
            """)
            
            await conn.commit()
    finally:
        conn.close()
