USE grade_book;

-- 1) Active academic period
INSERT INTO academic_periods (name, start_date, end_date, is_active)
SELECT * FROM (SELECT '2024-2025','2024-09-01','2025-06-30', TRUE) AS tmp
WHERE NOT EXISTS (SELECT 1 FROM academic_periods WHERE name='2024-2025')
LIMIT 1;

SET @ap_id := (SELECT id FROM academic_periods WHERE name='2024-2025' LIMIT 1);

-- 2) Base entities
INSERT IGNORE INTO rooms(name) VALUES ('201'), ('302');
SET @room201 := (SELECT id FROM rooms WHERE name='201' LIMIT 1);
SET @room302 := (SELECT id FROM rooms WHERE name='302' LIMIT 1);

INSERT IGNORE INTO subjects (name, coefficient) VALUES ('Математика', 1.5), ('Физика', 1.5);
SET @sub_math := (SELECT id FROM subjects WHERE name='Математика' LIMIT 1);
SET @sub_phys := (SELECT id FROM subjects WHERE name='Физика' LIMIT 1);

INSERT IGNORE INTO users (username, hashed_password, role) VALUES ('teacher_demo', '$2y$10$demo', 'teacher');
SET @teacher_id := (SELECT id FROM users WHERE username='teacher_demo' LIMIT 1);
INSERT IGNORE INTO profiles (user_id, full_name) VALUES (@teacher_id, 'Иванов Иван Иванович');

INSERT IGNORE INTO classes (name, academic_year) VALUES ('10','2024-2025'), ('11','2024-2025');
SET @class10 := (SELECT id FROM classes WHERE name='10' LIMIT 1);
SET @class11 := (SELECT id FROM classes WHERE name='11' LIMIT 1);

-- 3) Subject mappings
INSERT IGNORE INTO teacher_subjects (teacher_id, subject_id) VALUES
(@teacher_id, @sub_math), (@teacher_id, @sub_phys);

INSERT IGNORE INTO class_subjects (class_id, subject_id) VALUES
(@class10, @sub_math), (@class10, @sub_phys),
(@class11, @sub_math), (@class11, @sub_phys);

INSERT IGNORE INTO class_subject_teachers (class_id, subject_id, teacher_id) VALUES
(@class10, @sub_math, @teacher_id),
(@class11, @sub_phys, @teacher_id);

-- 4) Week (current monday) with proper academic_period_id
SET @monday := DATE_SUB(CURDATE(), INTERVAL (WEEKDAY(CURDATE())) DAY);

INSERT INTO academic_weeks (week_start_date, week_end_date, week_number, week_type, academic_period_id, is_holiday_week)
VALUES (@monday, DATE_ADD(@monday, INTERVAL 6 DAY), 1, 'A', @ap_id, FALSE)
ON DUPLICATE KEY UPDATE academic_period_id = VALUES(academic_period_id);

-- 5) Timetable template using *_id fields
INSERT INTO timetable_templates
(class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, start_time, end_time, week_type, academic_period_id, is_active)
VALUES
(@class10, @sub_math, @teacher_id, @room201, 1, 1, '08:30','09:15','A', @ap_id, TRUE),
(@class10, @sub_math, @teacher_id, @room201, 1, 2, '09:25','10:10','A', @ap_id, TRUE),
(@class10, @sub_math, @teacher_id, @room201, 3, 1, '08:30','09:15','A', @ap_id, TRUE),
(@class11, @sub_phys, @teacher_id, @room302, 2, 2, '09:25','10:10','A', @ap_id, TRUE),
(@class11, @sub_phys, @teacher_id, @room302, 2, 3, '10:20','11:05','A', @ap_id, TRUE),
(@class11, @sub_phys, @teacher_id, @room302, 4, 2, '09:25','10:10','A', @ap_id, TRUE);


