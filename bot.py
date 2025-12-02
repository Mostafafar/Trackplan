import os
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, JobQueue,
    ConversationHandler
)
from pytz import timezone

# تنظیمات دیتابیس
DB_CONFIG = {
    'dbname': 'study_bot_db',
    'user': 'postgres',
    'password': 'f13821382',
    'host': 'localhost',
    'port': '5432'
}

# تنظیمات ربات
TELEGRAM_TOKEN = "8493311862:AAF0k6E2LHOImAhTdxXMVRxtD4eSI4k_e8Y"

# تنظیمات زمان ایران
IRAN_TZ = timezone('Asia/Tehran')

# حالت‌های گفتگو
(
    GRADE_SELECTION, STUDENT_PANEL, ADMIN_PANEL, WAITING_PLAN, 
    PLAN_DAY, PLAN_GRADE, PLAN_SUBJECTS, BROADCAST_MESSAGE, 
    SELECT_DAY, SELECT_SUBJECT, SELECT_ADVISOR, ADD_ADVISOR,
    EDIT_PLANS, EDIT_PLAN_DETAIL, EDIT_PLAN_DAY, EDIT_PLAN_SUBJECTS,
    STUDENT_REPORTS, STUDENT_DETAILS  # اضافه کردن حالت‌های جدید
) = range(18)  # عدد را از 16 به 18 تغییر دهید

# تنظیم لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- توابع دیتابیس ---

def get_db_connection():
    """اتصال به دیتابیس"""
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise

def init_database():
    """ایجاد جداول مورد نیاز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # جدول دانش‌آموزان
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                full_name VARCHAR(255),
                grade VARCHAR(50),
                advisor_id INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # جدول مشاوران
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS advisors (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                full_name VARCHAR(255),
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # جدول برنامه‌های درسی - با ستون day_description
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS study_plans (
                id SERIAL PRIMARY KEY,
                day_number INTEGER NOT NULL,
                day_description VARCHAR(255),
                grade VARCHAR(50) NOT NULL,
                subjects JSONB NOT NULL,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
                
            )
        """)
        cursor.execute("""
            ALTER TABLE study_plans 
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
        """)
        
        # جدول تاریخچه ویرایش برنامه‌ها
        # --- جدول تاریخچه ویرایش برنامه‌ها ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plan_edit_history (
                id SERIAL PRIMARY KEY,
                plan_id INTEGER REFERENCES study_plans(id) ON DELETE CASCADE,
                edited_by INTEGER REFERENCES advisors(id),
                old_data JSONB,
                new_data JSONB,
                edit_time TIMESTAMP DEFAULT NOW()
          )
      """)
        
        try:
           cursor.execute("ALTER TABLE plan_edit_history DROP CONSTRAINT IF EXISTS plan_edit_history_plan_id_fkey")
           cursor.execute("""
               ALTER TABLE plan_edit_history 
               ADD CONSTRAINT plan_edit_history_plan_id_fkey 
               FOREIGN KEY (plan_id) REFERENCES study_plans(id) ON DELETE CASCADE
           """)
        except Exception as e:
            logger.warning(f"Foreign key update skipped: {e}") 
        # جدول جلسات مطالعه
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS study_sessions (
                id SERIAL PRIMARY KEY,
                student_id INTEGER REFERENCES students(id),
                subject_name VARCHAR(255) NOT NULL,
                day_number INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                total_duration INTEGER,
                status VARCHAR(50) DEFAULT 'in_progress',
                check_count INTEGER DEFAULT 0,
                last_check_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # جدول چک‌های وضعیت مطالعه
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS progress_checks (
                id SERIAL PRIMARY KEY,
                session_id INTEGER REFERENCES study_sessions(id),
                check_time TIMESTAMP NOT NULL,
                student_response VARCHAR(255),
                response_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        conn.commit()
        logger.info("✅ Database tables created successfully")
    
    create_default_admin()
    conn.close()
# در بخش توابع دیتابیس بعد از get_all_students_daily_activity اضافه کنید:
def get_student_reports_keyboard():
    """کیبورد گزارش‌های دانش‌آموزی"""
    keyboard = [
        ["📊 گزارش کلی ۷ روز اخیر"],
        ["🔙 بازگشت به پنل ادمین"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_students_list_keyboard():
    """ایجاد کیبورد لیست دانش‌آموزان به ترتیب مطالعه"""
    students = get_students_by_study_time_7days()
    
    if not students:
        return get_back_keyboard()
    
    keyboard = []
    for student in students[:15]:  # حداکثر ۱۵ دانش‌آموز
        hours = student['total_study_minutes'] // 60
        minutes = student['total_study_minutes'] % 60
        
        if hours > 0:
            time_text = f"⏱️ {hours}:{minutes:02d} ساعت"
        else:
            time_text = f"⏱️ {minutes} دقیقه"
        
        button_text = f"🎓 {student['full_name']} - {student['grade']} - {time_text}"
        keyboard.append([button_text])
    
    keyboard.append(["🔙 بازگشت به گزارش‌ها"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_student_details_keyboard():
    """کیبورد جزئیات دانش‌آموز"""
    keyboard = [
        ["📅 گزارش ۷ روز اخیر"],
        ["📊 گزارش روزانه (دیروز)"],
        ["🔙 بازگشت به لیست دانش‌آموزان"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_students_by_study_time_7days():
    """دریافت دانش‌آموزان به ترتیب مجموع مطالعه در ۷ روز گذشته"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT 
                s.id,
                s.full_name,
                s.grade,
                s.advisor_id,
                COALESCE(SUM(ss.total_duration), 0) as total_study_minutes,
                COUNT(ss.id) as total_sessions,
                MAX(ss.start_time) as last_study_time
            FROM students s
            LEFT JOIN study_sessions ss ON s.id = ss.student_id 
                AND ss.start_time >= NOW() - INTERVAL '7 days'
                AND ss.status = 'completed'
            GROUP BY s.id, s.full_name, s.grade, s.advisor_id
            ORDER BY total_study_minutes DESC, s.full_name
        """)
        result = cursor.fetchall()
    conn.close()
    return result

def get_student_detailed_report_7days(student_id: int):
    """دریافت گزارش جزئیات مطالعه دانش‌آموز در ۷ روز گذشته"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # اطلاعات دانش‌آموز
        cursor.execute("""
            SELECT s.*, a.full_name as advisor_name
            FROM students s
            LEFT JOIN advisors a ON s.advisor_id = a.id
            WHERE s.id = %s
        """, (student_id,))
        student_info = cursor.fetchone()
        
        # جلسات مطالعه ۷ روز گذشته
        cursor.execute("""
            SELECT 
                ss.*,
                DATE(ss.start_time AT TIME ZONE 'Asia/Tehran') as study_date,
                TO_CHAR(ss.start_time AT TIME ZONE 'Asia/Tehran', 'HH24:MI') as start_time_fa,
                TO_CHAR(ss.end_time AT TIME ZONE 'Asia/Tehran', 'HH24:MI') as end_time_fa,
                (
                    SELECT COUNT(*) 
                    FROM progress_checks pc 
                    WHERE pc.session_id = ss.id
                ) as check_count
            FROM study_sessions ss
            WHERE ss.student_id = %s 
                AND ss.start_time >= NOW() - INTERVAL '7 days'
                AND ss.status = 'completed'
            ORDER BY ss.start_time DESC
        """, (student_id,))
        sessions = cursor.fetchall()
        
        # چک‌های هر جلسه
        sessions_with_checks = []
        for session in sessions:
            cursor.execute("""
                SELECT 
                    pc.*,
                    TO_CHAR(pc.check_time AT TIME ZONE 'Asia/Tehran', 'HH24:MI') as check_time_fa
                FROM progress_checks pc
                WHERE pc.session_id = %s
                ORDER BY pc.check_time
            """, (session['id'],))
            checks = cursor.fetchall()
            session['checks'] = checks
            sessions_with_checks.append(session)
        
        # آمار کلی
        cursor.execute("""
            SELECT 
                COUNT(*) as total_sessions,
                COALESCE(SUM(total_duration), 0) as total_minutes,
                AVG(total_duration) as avg_duration
            FROM study_sessions
            WHERE student_id = %s 
                AND start_time >= NOW() - INTERVAL '7 days'
                AND status = 'completed'
        """, (student_id,))
        stats = cursor.fetchone()
        
    conn.close()
    
    return {
        'student': student_info,
        'sessions': sessions_with_checks,
        'stats': stats
    }
def get_student_daily_report(student_id: int, date: datetime = None):
    """گزارش روزانه دانش‌آموز برای یک روز خاص"""
    if date is None:
        date = datetime.now(IRAN_TZ) - timedelta(days=1)  # روز قبل
    
    start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # اطلاعات دانش‌آموز
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        student_info = cursor.fetchone()
        
        # جلسات مطالعه روز قبل
        cursor.execute("""
            SELECT 
                ss.*,
                TO_CHAR(ss.start_time AT TIME ZONE 'Asia/Tehran', 'HH24:MI') as start_time_fa,
                TO_CHAR(ss.end_time AT TIME ZONE 'Asia/Tehran', 'HH24:MI') as end_time_fa
            FROM study_sessions ss
            WHERE ss.student_id = %s 
                AND ss.start_time >= %s
                AND ss.start_time <= %s
                AND ss.status = 'completed'
            ORDER BY ss.start_time
        """, (student_id, start_of_day, end_of_day))
        sessions = cursor.fetchall()
        
        # آمار روز
        cursor.execute("""
            SELECT 
                COUNT(*) as total_sessions,
                COALESCE(SUM(total_duration), 0) as total_minutes,
                AVG(total_duration) as avg_duration
            FROM study_sessions
            WHERE student_id = %s 
                AND start_time >= %s
                AND start_time <= %s
                AND status = 'completed'
        """, (student_id, start_of_day, end_of_day))
        stats = cursor.fetchone()
        
    conn.close()
    
    return {
        'student': student_info,
        'sessions': sessions,
        'stats': stats,
        'date': date
    }

def create_study_plan(day_number: int, day_description: str, grade: str, subjects: List[Dict], created_by: int):
    """ایجاد برنامه درسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO study_plans (day_number, day_description, grade, subjects, created_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (day_number, day_description, grade, json.dumps(subjects), created_by))
        conn.commit()
    conn.close()
    return True

def update_study_plan(plan_id: int, day_number: int, day_description: str, grade: str, subjects: List[Dict], edited_by: int):
    """ویرایش برنامه درسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # ذخیره نسخه قبلی در تاریخچه
        cursor.execute("SELECT * FROM study_plans WHERE id = %s", (plan_id,))
        old_plan = cursor.fetchone()
        
        if old_plan:
            cursor.execute("""
                INSERT INTO plan_edit_history (plan_id, edited_by, old_data, new_data)
                VALUES (%s, %s, %s, %s)
            """, (plan_id, edited_by, json.dumps({
                'day_number': old_plan['day_number'],
                'day_description': old_plan['day_description'],
                'grade': old_plan['grade'],
                'subjects': old_plan['subjects']
            }), json.dumps({
                'day_number': day_number,
                'day_description': day_description,
                'grade': grade,
                'subjects': subjects
            })))
        
        # آپدیت برنامه
        cursor.execute("""
            UPDATE study_plans 
            SET day_number = %s, 
                day_description = %s,
                grade = %s,
                subjects = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (day_number, day_description, grade, json.dumps(subjects), plan_id))
        conn.commit()
    conn.close()
    return True

def delete_study_plan(plan_id: int):
    """حذف برنامه درسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM study_plans WHERE id = %s", (plan_id,))
        conn.commit()
    conn.close()
    return True

def get_study_plan_by_id(plan_id: int):
    """دریافت برنامه با آیدی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT sp.*, a.full_name as creator_name
            FROM study_plans sp
            LEFT JOIN advisors a ON sp.created_by = a.id
            WHERE sp.id = %s
        """, (plan_id,))
        result = cursor.fetchone()
        if result and result['subjects']:
            if isinstance(result['subjects'], str):
                try:
                    result['subjects'] = json.loads(result['subjects'])
                except json.JSONDecodeError:
                    result['subjects'] = []
            elif not isinstance(result['subjects'], list):
                result['subjects'] = []
    conn.close()
    return result

def create_default_admin():
    """ایجاد ادمین پیش‌فرض"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # ادمین اصلی
            cursor.execute("""
                INSERT INTO advisors (telegram_id, full_name, is_admin) 
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                is_admin = EXCLUDED.is_admin
            """, (6680287530, "مدیر سیستم", True))
            
            # مشاوران دیگر - is_admin=True برای دسترسی به پنل
            cursor.execute("""
                INSERT INTO advisors (telegram_id, full_name, is_admin) 
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                is_admin = EXCLUDED.is_admin
            """, (123456789, "مشاور نمونه", True))
            
            conn.commit()
            logger.info("✅ Default admin and advisors created")
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error creating default admin: {e}")

# --- توابع مدیریت دانش‌آموزان ---

def register_student(telegram_id: int, full_name: str, grade: str, advisor_id: int = None):
    """ثبت نام دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO students (telegram_id, full_name, grade, advisor_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            grade = EXCLUDED.grade,
            advisor_id = EXCLUDED.advisor_id
            RETURNING id
        """, (telegram_id, full_name, grade, advisor_id))
        result = cursor.fetchone()
        conn.commit()
    conn.close()
    return result['id'] if result else None

def get_student(telegram_id: int):
    """دریافت اطلاعات دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM students WHERE telegram_id = %s", (telegram_id,))
        result = cursor.fetchone()
    conn.close()
    return result

def get_all_students():
    """دریافت تمام دانش‌آموزان"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM students ORDER BY full_name")
        result = cursor.fetchall()
    conn.close()
    return result

def get_all_students_telegram_ids():
    """دریافت تمام آیدی‌های تلگرام دانش‌آموزان"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT telegram_id FROM students")
        result = [row['telegram_id'] for row in cursor.fetchall()]
    conn.close()
    return result

# --- توابع مدیریت مشاوران ---

def register_advisor(telegram_id: int, full_name: str, is_admin: bool = False):
    """ثبت نام مشاور"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO advisors (telegram_id, full_name, is_admin)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            is_admin = EXCLUDED.is_admin
            RETURNING id
        """, (telegram_id, full_name, is_admin))
        result = cursor.fetchone()
        conn.commit()
    conn.close()
    return result['id'] if result else None

def get_advisor(telegram_id: int):
    """دریافت اطلاعات مشاور"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM advisors WHERE telegram_id = %s", (telegram_id,))
        result = cursor.fetchone()
    conn.close()
    return result

def get_all_advisors():
    """دریافت تمام مشاوران"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM advisors ORDER BY full_name")
        result = cursor.fetchall()
    conn.close()
    return result

def is_admin(telegram_id: int):
    """بررسی ادمین بودن"""
    advisor = get_advisor(telegram_id)
    return advisor and advisor['is_admin']

# --- توابع مدیریت برنامه‌های درسی ---

def get_study_plan(day_number: int, grade: str, advisor_id: int = None):
    """دریافت برنامه درسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        if advisor_id:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE sp.day_number = %s AND sp.grade = %s AND sp.created_by = %s
                ORDER BY sp.created_at DESC LIMIT 1
            """, (day_number, grade, advisor_id))
        else:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE sp.day_number = %s AND sp.grade = %s
                ORDER BY sp.created_at DESC LIMIT 1
            """, (day_number, grade))
        
        result = cursor.fetchone()
        if result and result['subjects']:
            if isinstance(result['subjects'], str):
                try:
                    result['subjects'] = json.loads(result['subjects'])
                except json.JSONDecodeError:
                    result['subjects'] = []
            elif not isinstance(result['subjects'], list):
                result['subjects'] = []
    conn.close()
    return result

def get_all_plans():
    """دریافت تمام برنامه‌ها"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT sp.*, a.full_name as creator_name
            FROM study_plans sp
            LEFT JOIN advisors a ON sp.created_by = a.id
            ORDER BY sp.day_number DESC, sp.grade
        """)
        plans = cursor.fetchall()
        for plan in plans:
            if plan['subjects']:
                if isinstance(plan['subjects'], str):
                    try:
                        plan['subjects'] = json.loads(plan['subjects'])
                    except json.JSONDecodeError:
                        plan['subjects'] = []
                elif not isinstance(plan['subjects'], list):
                    plan['subjects'] = []
            else:
                plan['subjects'] = []
    conn.close()
    return plans

def get_plans_by_advisor(advisor_id: int):
    """دریافت برنامه‌های یک مشاور خاص"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT sp.*, a.full_name as creator_name
            FROM study_plans sp
            LEFT JOIN advisors a ON sp.created_by = a.id
            WHERE sp.created_by = %s
            ORDER BY sp.day_number DESC
        """, (advisor_id,))
        plans = cursor.fetchall()
        for plan in plans:
            if plan['subjects']:
                if isinstance(plan['subjects'], str):
                    try:
                        plan['subjects'] = json.loads(plan['subjects'])
                    except json.JSONDecodeError:
                        plan['subjects'] = []
                elif not isinstance(plan['subjects'], list):
                    plan['subjects'] = []
            else:
                plan['subjects'] = []
    conn.close()
    return plans

def get_plans_by_grade_and_advisor(grade: str, advisor_id: int = None):
    """دریافت برنامه‌های یک پایه خاص برای مشاور خاص"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        if advisor_id:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE sp.grade = %s AND sp.created_by = %s
                ORDER BY sp.day_number DESC
            """, (grade, advisor_id))
        else:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE sp.grade = %s
                ORDER BY sp.day_number DESC
            """, (grade,))
        
        plans = cursor.fetchall()
        for plan in plans:
            if plan['subjects']:
                if isinstance(plan['subjects'], str):
                    try:
                        plan['subjects'] = json.loads(plan['subjects'])
                    except json.JSONDecodeError:
                        plan['subjects'] = []
                elif not isinstance(plan['subjects'], list):
                    plan['subjects'] = []
            else:
                plan['subjects'] = []
    conn.close()
    return plans

def get_advisors_with_plans_for_grade(grade: str):
    """دریافت مشاورانی که برای این پایه برنامه دارند"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT a.id, a.full_name
            FROM advisors a
            JOIN study_plans sp ON a.id = sp.created_by
            WHERE sp.grade = %s
            ORDER BY a.full_name
        """, (grade,))
        result = cursor.fetchall()
    conn.close()
    return result

def get_all_advisors_for_selection():
    """دریافت تمام مشاوران برای انتخاب"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, full_name FROM advisors ORDER BY full_name")
        result = cursor.fetchall()
    conn.close()
    return result

# --- توابع مدیریت جلسات مطالعه ---

def start_study_session(student_id: int, subject_name: str, day_number: int):
    """شروع جلسه مطالعه"""
    start_time = datetime.now(IRAN_TZ)
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO study_sessions 
            (student_id, subject_name, day_number, start_time, status)
            VALUES (%s, %s, %s, %s, 'in_progress')
            RETURNING id
        """, (student_id, subject_name, day_number, start_time))
        result = cursor.fetchone()
        conn.commit()
    conn.close()
    return result['id'] if result else None

def end_study_session(session_id: int):
    """پایان جلسه مطالعه"""
    end_time = datetime.now(IRAN_TZ)
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE study_sessions 
            SET end_time = %s, 
                status = 'completed',
                total_duration = EXTRACT(EPOCH FROM (%s - start_time))/60
            WHERE id = %s
            RETURNING student_id, subject_name, total_duration
        """, (end_time, end_time, session_id))
        result = cursor.fetchone()
        conn.commit()
    conn.close()
    return result

def update_check_time(session_id: int):
    """به‌روزرسانی زمان آخرین بررسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE study_sessions 
            SET check_count = check_count + 1,
                last_check_time = %s
            WHERE id = %s
        """, (datetime.now(IRAN_TZ), session_id))
        conn.commit()
    conn.close()

def get_active_sessions():
    """دریافت جلسات فعال - فقط آخرین جلسه هر دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT ON (s.id) 
                s.id as student_id,
                s.full_name, 
                s.grade,
                ss.subject_name, 
                ss.start_time, 
                ss.check_count, 
                ss.id as session_id,
                ss.day_number,
                a.full_name as advisor_name
            FROM study_sessions ss
            JOIN students s ON ss.student_id = s.id
            LEFT JOIN advisors a ON s.advisor_id = a.id
            WHERE ss.status = 'in_progress'
            ORDER BY s.id, ss.start_time DESC
        """)
        result = cursor.fetchall()
    conn.close()
    return result

def get_session_checks(session_id: int):
    """دریافت تمام چک‌های یک جلسه مطالعه"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT * FROM progress_checks 
            WHERE session_id = %s 
            ORDER BY check_time
        """, (session_id,))
        result = cursor.fetchall()
    conn.close()
    return result

def get_student_active_session(student_id: int):
    """دریافت جلسه فعال دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT * FROM study_sessions 
            WHERE student_id = %s AND status = 'in_progress'
            ORDER BY start_time DESC LIMIT 1
        """, (student_id,))
        result = cursor.fetchone()
    conn.close()
    return result

def get_advisor_by_id(advisor_id: int):
    """دریافت اطلاعات مشاور با آیدی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM advisors WHERE id = %s", (advisor_id,))
        result = cursor.fetchone()
    conn.close()
    return result

def get_all_active_sessions_by_student(student_id: int):
    """دریافت تمام جلسات فعال یک دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT * FROM study_sessions 
            WHERE student_id = %s AND status = 'in_progress'
            ORDER BY start_time DESC
        """, (student_id,))
        result = cursor.fetchall()
    conn.close()
    return result

# --- توابع گزارش‌گیری ---

def get_daily_report(student_id: int, day_number: int):
    """گزارش روزانه دانش‌آموز با جزئیات چک‌ها"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # دریافت جلسات
        cursor.execute("""
            SELECT 
                id,
                subject_name,
                start_time,
                end_time,
                total_duration,
                status,
                check_count
            FROM study_sessions 
            WHERE student_id = %s AND day_number = %s
            ORDER BY start_time
        """, (student_id, day_number))
        sessions = cursor.fetchall()
        
        # برای هر جلسه، چک‌ها را دریافت کن
        result = []
        for session in sessions:
            cursor.execute("""
                SELECT 
                    check_time,
                    student_response,
                    response_time
                FROM progress_checks 
                WHERE session_id = %s 
                ORDER BY check_time
            """, (session['id'],))
            checks = cursor.fetchall()
            
            session['checks'] = checks
            result.append(session)
    
    conn.close()
    return result

def get_all_students_daily_activity(day_number: int):
    """فعالیت تمام دانش‌آموزان در یک روز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT 
                s.full_name,
                s.grade,
                ss.subject_name,
                ss.start_time,
                ss.end_time,
                ss.status,
                ss.check_count,
                ss.total_duration
            FROM students s
            LEFT JOIN study_sessions ss ON s.id = ss.student_id AND ss.day_number = %s
            ORDER BY s.full_name, ss.start_time
        """, (day_number,))
        result = cursor.fetchall()
    conn.close()
    return result

# --- Keyboard Markups ---

def get_main_menu_keyboard():
    """منوی اصلی"""
    keyboard = [
        ["🎓 ثبت نام دانش‌آموز"],
        ["📚 برنامه‌های درسی", "📊 گزارش من"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_grade_selection_keyboard():
    """انتخاب پایه تحصیلی"""
    keyboard = [
        ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"],
        ["🔙 بازگشت به منوی اصلی"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_student_panel_keyboard():
    """پنل دانش‌آموز"""
    keyboard = [
        ["🎯 شروع مطالعه جدید"],
        ["📊 گزارش روزانه", "🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_panel_keyboard():
    """پنل ادمین - تغییر نام گزینه گزارش کلی"""
    keyboard = [
        ["➕ افزودن برنامه جدید", "📝 مشاهده برنامه‌ها"],
        ["✏️ ویرایش برنامه‌ها", "👥 مشاهده دانش‌آموزان"],
        ["📊 گزارش دانش‌آموزی", "🔍 جلسات فعال"],  # تغییر نام از "گزارش کلی" به "گزارش دانش‌آموزی"
        ["📢 ارسال پیام همگانی", "👨‍🏫 مدیریت مشاوران"],
        ["🔙 بازگشت به منوی اصلی"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
def setup_nightly_report_job(application: Application):
    """تنظیم job برای ارسال گزارش‌های شبانه"""
    # زمان‌بندی برای ساعت ۲ بامداد به وقت ایران
    # ابتدا زمان فعلی را به وقت ایران می‌گیریم
    now = datetime.now(IRAN_TZ)
    
    # ساعت ۲ بامداد فردا
    target_time = now.replace(hour=2, minute=0, second=0, microsecond=0)
    
    # اگر الان از ساعت ۲ گذشته، برای فردا برنامه‌ریزی کن
    if now.hour >= 2:
        target_time += timedelta(days=1)
    
    # محاسبه زمان تا ساعت ۲ بامداد
    delay_seconds = (target_time - now).total_seconds()
    
    logger.info(f"⏰ گزارش شبانه برای ساعت ۲ بامداد تنظیم شد (تا شروع: {delay_seconds} ثانیه)")
    
    # تنظیم job تکرار شونده روزانه
    application.job_queue.run_daily(
        send_nightly_reports,
        time=target_time.time(),
        days=(0, 1, 2, 3, 4, 5, 6),  # همه روزهای هفته
        name="nightly_reports"
    )

def get_back_keyboard():
    """دکمه بازگشت ساده"""
    keyboard = [["🔙 بازگشت"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_study_management_keyboard():
    """مدیریت مطالعه"""
    keyboard = [
        ["✅ پایان مطالعه", "🔄 تغییر درس"],
        ["📊 بازگشت به پنل"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_progress_check_keyboard():
    """بررسی ۲۰ دقیقه‌ای"""
    keyboard = [
        ["✅ در حال پیشرفت"],
        ["⚠️ مشکل دارم"],
        ["❌ متوقف کردم", "⏹️ اتمام مطالعه"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

def get_plan_grade_keyboard():
    """انتخاب پایه برای برنامه"""
    keyboard = [
        ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"],
        ["🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_plan_subjects_keyboard():
    """مدیریت دروس برنامه"""
    keyboard = [
        ["✅ پایان"],
        ["🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_broadcast_keyboard():
    """کیبورد برای ارسال پیام همگانی"""
    keyboard = [
        ["🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_advisors_management_keyboard():
    """مدیریت مشاوران"""
    keyboard = [
        ["➕ افزودن مشاور جدید"],
        ["📋 لیست مشاوران"],
        ["🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_advisors_keyboard(grade: str):
    """ایجاد دکمه‌های مشاوران برای یک پایه"""
    advisors = get_advisors_with_plans_for_grade(grade)
    
    if not advisors:
        # اگر هیچ مشاوری برای این پایه برنامه ندارد، تمام مشاوران را نشان بده
        advisors = get_all_advisors_for_selection()
    
    if not advisors:
        return None
    
    keyboard = []
    for advisor in advisors:
        keyboard.append([f"👤 {advisor['full_name']}"])
    
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_days_keyboard(grade: str, advisor_id: int):
    """ایجاد دکمه‌های روزهای موجود برای یک پایه و مشاور"""
    plans = get_plans_by_grade_and_advisor(grade, advisor_id)
    
    if not plans:
        return None
    
    keyboard = []
    for plan in plans:
        day_text = f"📅 روز {plan['day_number']}"
        if plan.get('day_description'):
            day_text += f" ({plan['day_description']})"
        keyboard.append([day_text])
    
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_subjects_keyboard(day_number: int, grade: str, advisor_id: int):
    """ایجاد دکمه‌های دروس یک روز خاص برای مشاور خاص"""
    plan = get_study_plan(day_number, grade, advisor_id)
    
    if not plan or not plan['subjects']:
        return None
    
    keyboard = []
    for subject in plan['subjects']:
        keyboard.append([f"📚 {subject['name']}"])
    
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_edit_plans_keyboard():
    """کیبورد مدیریت ویرایش برنامه‌ها"""
    keyboard = [
        ["📋 مشاهده برنامه‌های من", "👁️ مشاهده تمام برنامه‌ها"],
        ["🔍 جستجوی برنامه", "🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_plan_actions_keyboard():
    """کیبورد اقدامات روی برنامه"""
    keyboard = [
        ["✏️ ویرایش اطلاعات", "📝 ویرایش دروس"],
        ["❌ حذف برنامه", "🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_edit_plan_details_keyboard():
    """کیبورد ویرایش جزئیات برنامه"""
    keyboard = [
        ["📅 ویرایش شماره روز", "📝 ویرایش توضیحات"],
        ["🎓 تغییر پایه تحصیلی", "🔙 بازگشت"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
async def send_nightly_reports(context: ContextTypes.DEFAULT_TYPE):
    """ارسال گزارش شبانه به تمام دانش‌آموزان"""
    logger.info("📊 شروع ارسال گزارش‌های شبانه...")
    
    # دریافت تمام دانش‌آموزان
    students = get_all_students()
    
    sent_count = 0
    failed_count = 0
    
    for student in students:
        try:
            # دریافت گزارش روز قبل
            yesterday = datetime.now(IRAN_TZ) - timedelta(days=1)
            report = get_student_daily_report(student['id'], yesterday)
            
            # ساخت پیام گزارش
            if not report['sessions']:
                message_text = (
                    f"🌙 گزارش شبانه\n\n"
                    f"سلام {report['student']['full_name']} 👋\n\n"
                    f"📅 دیروز ({yesterday.strftime('%Y/%m/%d')})\n"
                    f"📊 هیچ فعالیت مطالعاتی ثبت نشده است.\n\n"
                    f"امروز را با انرژی شروع کن! 💪"
                )
            else:
                total_minutes = report['stats']['total_minutes'] or 0
                total_hours = total_minutes // 60
                total_min = total_minutes % 60
                
                if total_hours > 0:
                    total_text = f"{total_hours}:{total_min:02d} ساعت"
                else:
                    total_text = f"{total_min} دقیقه"
                
                message_text = (
                    f"🌙 گزارش شبانه\n\n"
                    f"سلام {report['student']['full_name']} 👋\n\n"
                    f"📅 دیروز ({yesterday.strftime('%Y/%m/%d')})\n"
                    f"📊 عملکرد مطالعاتی شما:\n\n"
                )
                
                for i, session in enumerate(report['sessions'], 1):
                    duration = session['total_duration'] or 0
                    hours = duration // 60
                    minutes = duration % 60
                    
                    if hours > 0:
                        dur_text = f"{hours}:{minutes:02d} ساعت"
                    else:
                        dur_text = f"{minutes} دقیقه"
                    
                    message_text += f"{i}. 🕐 {session['start_time_fa']}\n"
                    message_text += f"   📚 {session['subject_name']}\n"
                    message_text += f"   ⏱️ مدت: {dur_text}\n\n"
                
                message_text += (
                    f"📈 آمار روز:\n"
                    f"  ⏱️ مجموع مطالعه: {total_text}\n"
                    f"  📚 تعداد جلسات: {report['stats']['total_sessions']}\n\n"
                    f"فردا هم همین‌طور ادامه بده! 🚀"
                )
            
            # ارسال پیام
            await context.bot.send_message(
                chat_id=student['telegram_id'],
                text=message_text
            )
            
            sent_count += 1
            logger.info(f"✅ گزارش شبانه ارسال شد به {student['full_name']}")
            
            # تأخیر بین ارسال‌ها برای جلوگیری از محدودیت تلگرام
            await asyncio.sleep(0.5)
            
        except Exception as e:
            failed_count += 1
            logger.error(f"❌ خطا در ارسال گزارش به {student['full_name']}: {e}")
    
    logger.info(f"📊 ارسال گزارش‌های شبانه پایان یافت. ارسال شده: {sent_count}, ناموفق: {failed_count}")
async def handle_student_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت گزارش‌های دانش‌آموزی"""
    text = update.message.text
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text(
            "❌ فقط ادمین‌ها می‌توانند به این بخش دسترسی داشته باشند.",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    if text == "🔙 بازگشت به پنل ادمین":
        await update.message.reply_text(
            "پنل ادمین:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    elif text == "📊 گزارش کلی ۷ روز اخیر":
        # دریافت لیست دانش‌آموزان به ترتیب مطالعه
        students = get_students_by_study_time_7days()
        
        if not students:
            await update.message.reply_text(
                "📊 هیچ فعالیت مطالعاتی در ۷ روز اخیر ثبت نشده است.",
                reply_markup=get_student_reports_keyboard()
            )
            return STUDENT_REPORTS
        
        # نمایش خلاصه
        summary_text = "📊 گزارش ۷ روز اخیر:\n\n"
        summary_text += "📈 رتبه‌بندی بر اساس میزان مطالعه:\n\n"
        
        for i, student in enumerate(students[:10], 1):  # ۱۰ نفر اول
            hours = student['total_study_minutes'] // 60
            minutes = student['total_study_minutes'] % 60
            
            emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
            
            if hours > 0:
                time_text = f"{hours}:{minutes:02d} ساعت"
            else:
                time_text = f"{minutes} دقیقه"
            
            summary_text += f"{emoji} {student['full_name']} ({student['grade']})\n"
            summary_text += f"   ⏱️ مجموع مطالعه: {time_text}\n"
            summary_text += f"   📚 تعداد جلسات: {student['total_sessions']}\n\n"
        
        summary_text += "\nبرای مشاهده جزئیات هر دانش‌آموز، از لیست زیر انتخاب کنید:"
        
        await update.message.reply_text(
            summary_text,
            reply_markup=get_students_list_keyboard()
        )
        context.user_data['report_mode'] = '7days'
        return STUDENT_DETAILS
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=get_student_reports_keyboard()
    )
async def handle_student_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب دانش‌آموز از لیست"""
    text = update.message.text
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text(
            "❌ فقط ادمین‌ها می‌توانند به این بخش دسترسی داشته باشند.",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    if text == "🔙 بازگشت به گزارش‌ها":
        await update.message.reply_text(
            "📊 گزارش‌های دانش‌آموزی:",
            reply_markup=get_student_reports_keyboard()
        )
        return STUDENT_REPORTS
    
    if text.startswith("🎓 "):
        try:
            # استخراج نام دانش‌آموز از متن دکمه
            parts = text.split(" - ")
            student_name = parts[0].replace("🎓 ", "").strip()
            
            # پیدا کردن دانش‌آموز در دیتابیس
            students = get_students_by_study_time_7days()
            selected_student = None
            
            for student in students:
                if student['full_name'] == student_name:
                    selected_student = student
                    break
            
            if not selected_student:
                await update.message.reply_text(
                    "❌ دانش‌آموز یافت نشد.",
                    reply_markup=get_students_list_keyboard()
                )
                return STUDENT_DETAILS
            
            # ذخیره اطلاعات دانش‌آموز در context
            context.user_data['selected_student_id'] = selected_student['id']
            context.user_data['selected_student_name'] = selected_student['full_name']
            
            # نمایش خلاصه اطلاعات
            hours = selected_student['total_study_minutes'] // 60
            minutes = selected_student['total_study_minutes'] % 60
            
            if hours > 0:
                time_text = f"{hours}:{minutes:02d} ساعت"
            else:
                time_text = f"{minutes} دقیقه"
            
            advisor = get_advisor_by_id(selected_student['advisor_id'])
            advisor_name = advisor['full_name'] if advisor else "تعیین نشده"
            
            summary_text = (
                f"👤 دانش‌آموز: {selected_student['full_name']}\n"
                f"📚 پایه: {selected_student['grade']}\n"
                f"👨‍🏫 مشاور: {advisor_name}\n"
                f"📈 عملکرد ۷ روز اخیر:\n"
                f"  ⏱️ مجموع مطالعه: {time_text}\n"
                f"  📚 تعداد جلسات: {selected_student['total_sessions']}\n\n"
                f"لطفاً نوع گزارش را انتخاب کنید:"
            )
            
            await update.message.reply_text(
                summary_text,
                reply_markup=get_student_details_keyboard()
            )
            return STUDENT_DETAILS
            
        except Exception as e:
            logger.error(f"Error in handle_student_selection: {e}")
            await update.message.reply_text(
                "❌ خطا در پردازش اطلاعات دانش‌آموز.",
                reply_markup=get_students_list_keyboard()
            )
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از دانش‌آموزان را انتخاب کنید:",
            reply_markup=get_students_list_keyboard()
        )
async def handle_student_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت جزئیات گزارش دانش‌آموز"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "🔙 بازگشت به لیست دانش‌آموزان":
        await update.message.reply_text(
            "📊 لیست دانش‌آموزان بر اساس میزان مطالعه ۷ روز اخیر:",
            reply_markup=get_students_list_keyboard()
        )
        return STUDENT_DETAILS
    
    elif text == "📅 گزارش ۷ روز اخیر":
        student_id = user_data.get('selected_student_id')
        
        if not student_id:
            await update.message.reply_text(
                "❌ دانش‌آموزی انتخاب نشده است.",
                reply_markup=get_student_details_keyboard()
            )
            return STUDENT_DETAILS
        
        # دریافت گزارش ۷ روز
        report = get_student_detailed_report_7days(student_id)
        
        if not report['sessions']:
            await update.message.reply_text(
                f"📊 هیچ فعالیت مطالعاتی در ۷ روز اخیر برای {report['student']['full_name']} ثبت نشده است.",
                reply_markup=get_student_details_keyboard()
            )
            return
        
        # ساخت گزارش
        report_text = f"📊 گزارش ۷ روز اخیر {report['student']['full_name']}\n"
        report_text += f"📚 پایه: {report['student']['grade']}\n"
        report_text += f"👨‍🏫 مشاور: {report['student']['advisor_name'] or 'تعیین نشده'}\n"
        report_text += "─" * 30 + "\n\n"
        
        # گروه‌بندی بر اساس تاریخ
        sessions_by_date = {}
        for session in report['sessions']:
            date_key = session['study_date']
            if date_key not in sessions_by_date:
                sessions_by_date[date_key] = []
            sessions_by_date[date_key].append(session)
        
        # نمایش برای هر تاریخ
        for date_key in sorted(sessions_by_date.keys(), reverse=True):
            # تبدیل تاریخ به شمسی (ساده)
            try:
                date_obj = datetime.strptime(str(date_key), '%Y-%m-%d')
                jalali_date = date_obj.strftime('%Y/%m/%d')
            except:
                jalali_date = str(date_key)
            
            report_text += f"📅 {jalali_date}:\n"
            
            daily_minutes = 0
            for session in sessions_by_date[date_key]:
                hours = session['total_duration'] // 60
                minutes = session['total_duration'] % 60
                daily_minutes += session['total_duration']
                
                if hours > 0:
                    duration_text = f"{hours}:{minutes:02d} ساعت"
                else:
                    duration_text = f"{minutes} دقیقه"
                
                report_text += f"  🕐 {session['start_time_fa']} - {session['end_time_fa']}\n"
                report_text += f"  📚 {session['subject_name']}\n"
                report_text += f"  ⏱️ مدت: {duration_text}\n"
                report_text += f"  🔢 چک‌ها: {session['check_count']}\n"
                
                # نمایش چک‌ها
                if session['checks']:
                    report_text += f"  📋 پاسخ‌ها:\n"
                    for check in session['checks']:
                        response_text = check.get('student_response', 'بدون پاسخ')
                        report_text += f"    • {check['check_time_fa']}: {response_text}\n"
                
                report_text += "\n"
            
            # مجموع روز
            daily_hours = daily_minutes // 60
            daily_min = daily_minutes % 60
            if daily_hours > 0:
                daily_total = f"{daily_hours}:{daily_min:02d} ساعت"
            else:
                daily_total = f"{daily_min} دقیقه"
            
            report_text += f"  📈 مجموع روز: {daily_total}\n"
            report_text += "─" * 30 + "\n\n"
        
        # آمار کلی
        total_hours = report['stats']['total_minutes'] // 60
        total_minutes = report['stats']['total_minutes'] % 60
        
        if total_hours > 0:
            total_text = f"{total_hours}:{total_minutes:02d} ساعت"
        else:
            total_text = f"{total_minutes} دقیقه"
        
        report_text += f"📈 آمار کلی ۷ روز اخیر:\n"
        report_text += f"  ⏱️ مجموع مطالعه: {total_text}\n"
        report_text += f"  📚 تعداد جلسات: {report['stats']['total_sessions']}\n"
        report_text += f"  ⏱️ میانگین هر جلسه: {int(report['stats']['avg_duration'] or 0)} دقیقه\n"
        
        # اگر گزارش طولانی شد، آن را تقسیم کنیم
        if len(report_text) > 4000:
            parts = [report_text[i:i+4000] for i in range(0, len(report_text), 4000)]
            for i, part in enumerate(parts):
                if i == 0:
                    await update.message.reply_text(part, reply_markup=get_student_details_keyboard())
                else:
                    await update.message.reply_text(part)
        else:
            await update.message.reply_text(
                report_text,
                reply_markup=get_student_details_keyboard()
            )
    
    elif text == "📊 گزارش روزانه (دیروز)":
        student_id = user_data.get('selected_student_id')
        
        if not student_id:
            await update.message.reply_text(
                "❌ دانش‌آموزی انتخاب نشده است.",
                reply_markup=get_student_details_keyboard()
            )
            return STUDENT_DETAILS
        
        # گزارش روز قبل
        yesterday = datetime.now(IRAN_TZ) - timedelta(days=1)
        report = get_student_daily_report(student_id, yesterday)
        
        if not report['sessions']:
            await update.message.reply_text(
                f"📊 هیچ فعالیت مطالعاتی در تاریخ {yesterday.strftime('%Y/%m/%d')} برای {report['student']['full_name']} ثبت نشده است.",
                reply_markup=get_student_details_keyboard()
            )
            return
        
        # ساخت گزارش روزانه
        date_text = yesterday.strftime('%Y/%m/%d')
        
        report_text = f"📊 گزارش روزانه {report['student']['full_name']}\n"
        report_text += f"📅 تاریخ: {date_text}\n"
        report_text += f"📚 پایه: {report['student']['grade']}\n"
        report_text += "─" * 30 + "\n\n"
        
        total_minutes = 0
        for session in report['sessions']:
            hours = session['total_duration'] // 60
            minutes = session['total_duration'] % 60
            total_minutes += session['total_duration']
            
            if hours > 0:
                duration_text = f"{hours}:{minutes:02d} ساعت"
            else:
                duration_text = f"{minutes} دقیقه"
            
            report_text += f"🕐 {session['start_time_fa']} - {session['end_time_fa']}\n"
            report_text += f"📚 {session['subject_name']}\n"
            report_text += f"⏱️ مدت: {duration_text}\n"
            report_text += "─" * 20 + "\n"
        
        # جمع کل
        total_hours = total_minutes // 60
        total_min = total_minutes % 60
        
        if total_hours > 0:
            total_text = f"{total_hours}:{total_min:02d} ساعت"
        else:
            total_text = f"{total_min} دقیقه"
        
        report_text += f"\n📈 آمار روز:\n"
        report_text += f"  ⏱️ مجموع مطالعه: {total_text}\n"
        report_text += f"  📚 تعداد جلسات: {len(report['sessions'])}\n"
        
        await update.message.reply_text(
            report_text,
            reply_markup=get_student_details_keyboard()
                )
# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ربات"""
    user = update.effective_user
    
    # متن خوش‌آمدگویی
    welcome_text = (
        "🤖 به ربات مدیریت مطالعه خوش آمدید!\n\n"
        "🎯 این ربات به شما کمک می‌کند:\n"
        "• برنامه‌ریزی درسی داشته باشید\n"
        "• جلسات مطالعه را مدیریت کنید\n"
        "• پیشرفت خود را پیگیری کنید\n\n"
        "برای شروع از گزینه‌های زیر استفاده کنید:"
    )
    
    # بررسی ادمین بودن (فقط ادمین‌های ثبت شده)
    advisor = get_advisor(user.id)
    if advisor and advisor['is_admin']:
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    # برای کاربران عادی - ارسال اطلاعات به ادمین خاص
    # فقط اگر کاربر جدید باشد یا قبلاً ثبت‌نام نکرده باشد
    student = get_student(user.id)
    if not student:
        # ارسال اطلاعات کاربر جدید به ادمین خاص (6680287530)
        try:
            await context.bot.send_message(
                chat_id=6680287530,
                text=f"👤 کاربر جدید\n\n"
                     f"🆔 آیدی: {user.id}\n"
                     f"👤 نام: {user.full_name}\n"
                     f"📱 نام کاربری: @{user.username if user.username else 'ندارد'}\n"
                     f"🕐 زمان: {datetime.now(IRAN_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                     f"✅ کاربر /start را زده و در حال ثبت‌نام است."
            )
        except Exception as e:
            logger.error(f"Failed to send new user info to admin 6680287530: {e}")
    
    # برای کاربران عادی
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard()
    )
    return GRADE_SELECTION

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت منوی اصلی"""
    text = update.message.text
    
    if text == "🎓 ثبت نام دانش‌آموز":
        await update.message.reply_text(
            "🎓 لطفاً پایه تحصیلی خود را انتخاب کنید:",
            reply_markup=get_grade_selection_keyboard()
        )
        return GRADE_SELECTION
    
    elif text == "📚 برنامه‌های درسی":
        await show_all_study_plans_to_student(update, context)
        return GRADE_SELECTION
    
    elif text == "📊 گزارش من":
        student = get_student(update.effective_user.id)
        if student:
            await show_student_report(update, context)
        else:
            await update.message.reply_text(
                "❌ شما به عنوان دانش‌آموز ثبت‌نام نکرده‌اید.",
                reply_markup=get_main_menu_keyboard()
            )
        return GRADE_SELECTION
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منو را انتخاب کنید:",
            reply_markup=get_main_menu_keyboard()
        )
        return GRADE_SELECTION

async def show_all_study_plans_to_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش برنامه‌های درسی به دانش‌آموز"""
    plans = get_all_plans()
    
    if not plans:
        await update.message.reply_text(
            "📝 در حال حاضر هیچ برنامه درسی موجود نیست.",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    plans_text = "📚 برنامه‌های درسی موجود:\n\n"
    
    for plan in plans:
        day_text = f"روز {plan['day_number']}"
        if plan.get('day_description'):
            day_text += f" ({plan['day_description']})"
            
        plans_text += (
            f"📘 {day_text} - {plan['grade']}\n"
            f"👤 مشاور: {plan['creator_name']}\n"
            f"📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        plans_text,
        reply_markup=get_main_menu_keyboard()
    )

async def handle_grade_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب پایه تحصیلی"""
    text = update.message.text
    
    if text == "🔙 بازگشت به منوی اصلی":
        await update.message.reply_text(
            "منوی اصلی:",
            reply_markup=get_main_menu_keyboard()
        )
        return GRADE_SELECTION
    
    if text in ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"]:
        user = update.effective_user
        grade = "دوازدهم تجربی" if "تجربی" in text else "دوازدهم ریاضی"
        
        context.user_data['grade'] = grade
        
        # نمایش مشاوران موجود برای این پایه
        advisors_keyboard = get_advisors_keyboard(grade)
        if not advisors_keyboard:
            await update.message.reply_text(
                f"❌ برای پایه {grade} هیچ مشاور و برنامه‌ای موجود نیست.\n"
                f"لطفاً با مدیر سیستم تماس بگیرید.",
                reply_markup=get_main_menu_keyboard()
            )
            return GRADE_SELECTION
        
        await update.message.reply_text(
            f"🎓 پایه {grade} انتخاب شد!\n\n"
            f"👤 لطفاً مشاور خود را انتخاب کنید:",
            reply_markup=advisors_keyboard
        )
        return SELECT_ADVISOR
    
    await update.message.reply_text(
        "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=get_grade_selection_keyboard()
    )
    return GRADE_SELECTION

async def handle_advisor_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب مشاور"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "🎓 لطفاً پایه تحصیلی خود را انتخاب کنید:",
            reply_markup=get_grade_selection_keyboard()
        )
        return GRADE_SELECTION
    
    if text.startswith("👤 "):
        advisor_name = text.replace("👤 ", "")
        
        # پیدا کردن آیدی مشاور
        advisors = get_all_advisors_for_selection()
        selected_advisor = None
        for advisor in advisors:
            if advisor['full_name'] == advisor_name:
                selected_advisor = advisor
                break
        
        if not selected_advisor:
            await update.message.reply_text(
                "❌ مشاور انتخاب شده یافت نشد.",
                reply_markup=get_advisors_keyboard(grade)
            )
            return SELECT_ADVISOR
        
        user_data['advisor_id'] = selected_advisor['id']
        user_data['advisor_name'] = advisor_name
        
        # ثبت نام دانش‌آموز
        student_id = register_student(
            telegram_id=update.effective_user.id,
            full_name=update.effective_user.full_name,
            grade=grade,
            advisor_id=selected_advisor['id']
        )
        
        user_data['student_id'] = student_id
        
        # نمایش روزهای موجود برای این مشاور و پایه
        days_keyboard = get_days_keyboard(grade, selected_advisor['id'])
        if not days_keyboard:
            await update.message.reply_text(
                f"✅ ثبت‌نام شما در پایه {grade} با مشاور {advisor_name} انجام شد!\n\n"
                f"📝 در حال حاضر مشاور شما برنامه‌ای برای این پایه ندارد.\n"
                f"لطفاً منتظر بمانید یا با مشاور خود تماس بگیرید.",
                reply_markup=get_student_panel_keyboard()
            )
            return STUDENT_PANEL
        
        await update.message.reply_text(
            f"✅ ثبت‌نام شما در پایه {grade} با مشاور {advisor_name} با موفقیت انجام شد!\n\n"
            f"📅 لطفاً روز مورد نظر را انتخاب کنید:",
            reply_markup=days_keyboard
        )
        return SELECT_DAY
    
    await update.message.reply_text(
        "لطفاً یکی از مشاوران را انتخاب کنید:",
        reply_markup=get_advisors_keyboard(grade)
    )

async def handle_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب روز"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    advisor_id = user_data.get('advisor_id')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            f"👤 لطفاً مشاور خود را انتخاب کنید:",
            reply_markup=get_advisors_keyboard(grade)
        )
        return SELECT_ADVISOR
    
    if text.startswith("📅 روز "):
        try:
            # استخراج شماره روز از متن
            day_text = text.replace("📅 روز ", "")
            day_number = int(day_text.split()[0])  # فقط عدد اول را بگیر
            user_data['selected_day'] = day_number
            
            # نمایش دروس این روز
            subjects_keyboard = get_subjects_keyboard(day_number, grade, advisor_id)
            if not subjects_keyboard:
                await update.message.reply_text(
                    f"❌ برای روز {day_number} هیچ درسی تعریف نشده است.",
                    reply_markup=get_days_keyboard(grade, advisor_id)
                )
                return SELECT_DAY
            
            plan = get_study_plan(day_number, grade, advisor_id)
            day_info = f"روز {day_number}"
            if plan and plan.get('day_description'):
                day_info += f" ({plan['day_description']})"
            
            await update.message.reply_text(
                f"📘 برنامه {day_info} - {grade}\n"
                f"👤 مشاور: {user_data.get('advisor_name')}\n\n"
                f"📚 لطفاً درس مورد نظر را انتخاب کنید:",
                reply_markup=subjects_keyboard
            )
            return SELECT_SUBJECT
            
        except ValueError:
            await update.message.reply_text(
                "❌ خطا در انتخاب روز. لطفاً مجدداً تلاش کنید.",
                reply_markup=get_days_keyboard(grade, advisor_id)
            )
    
    await update.message.reply_text(
        "لطفاً یکی از روزها را انتخاب کنید:",
        reply_markup=get_days_keyboard(grade, advisor_id)
    )

async def handle_subject_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب درس"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    day_number = user_data.get('selected_day')
    advisor_id = user_data.get('advisor_id')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            f"📅 لطفاً روز مورد نظر را انتخاب کنید:",
            reply_markup=get_days_keyboard(grade, advisor_id)
        )
        return SELECT_DAY
    
    if text.startswith("📚 "):
        subject_name = text.replace("📚 ", "")
        
        # پایان دادن به تمام جلسات فعال قبلی این دانش‌آموز
        student_id = user_data['student_id']
        active_sessions = get_all_active_sessions_by_student(student_id)
        for session in active_sessions:
            end_study_session(session['id'])
            # حذف job چک‌های ۲۰ دقیقه‌ای قبلی
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{session['id']}"]
                for job in current_jobs:
                    job.schedule_removal()
        
        # شروع جلسه مطالعه جدید
        session_id = start_study_session(
            student_id=user_data['student_id'],
            subject_name=subject_name,
            day_number=day_number
        )
        
        user_data['current_session'] = session_id
        user_data['current_subject'] = subject_name
        start_time = datetime.now(IRAN_TZ).strftime("%H:%M")
        
        plan = get_study_plan(day_number, grade, advisor_id)
        day_info = f"روز {day_number}"
        if plan and plan.get('day_description'):
            day_info += f" ({plan['day_description']})"
        
        await update.message.reply_text(
            f"🎯 مطالعه '{subject_name}' شروع شد!\n"
            f"📅 {day_info}\n"
            f"👤 مشاور: {user_data.get('advisor_name')}\n"
            f"🕐 زمان شروع: {start_time}\n"
            f"⏰ هر ۲۰ دقیقه وضعیت شما چک می‌شود...\n\n"
            f"✅ پس از اتمام مطالعه، دکمه پایان را بزنید.\n"
            f"🔄 برای تغییر درس، دکمه تغییر درس را بزنید.",
            reply_markup=get_study_management_keyboard()
        )
        
        # برنامه‌ریزی برای چک‌های ۲۰ دقیقه‌ای
        if 'check_jobs' not in context.chat_data:
            context.chat_data['check_jobs'] = []
        
        # استفاده از application job queue
        job = context.application.job_queue.run_repeating(
            progress_check,
            interval=1200,  # 20 دقیقه
            first=1200,
            chat_id=update.message.chat_id,
            name=f"check_{session_id}",
            data=session_id
        )
        context.chat_data['check_jobs'].append(job)
        
        # برنامه‌ریزی برای پایان خودکار بعد از 120 دقیقه
        auto_end_job = context.application.job_queue.run_once(
            auto_end_session,
            7200,  # 120 دقیقه
            chat_id=update.message.chat_id,
            name=f"auto_end_{session_id}",
            data=session_id
        )
        context.chat_data['check_jobs'].append(auto_end_job)
        
        return STUDENT_PANEL
    
    await update.message.reply_text(
        "لطفاً یکی از دروس را انتخاب کنید:",
        reply_markup=get_subjects_keyboard(day_number, grade, advisor_id)
    )

async def progress_check(context: ContextTypes.DEFAULT_TYPE):
    """بررسی وضعیت مطالعه هر ۲۰ دقیقه"""
    job = context.job
    session_id = job.data
    
    # به‌روزرسانی زمان آخرین بررسی
    update_check_time(session_id)
    
    # ارسال پیام بررسی وضعیت
    await context.bot.send_message(
        chat_id=job.chat_id,
        text="🔄 وضعیت مطالعه شما چگونه است؟",
        reply_markup=get_progress_check_keyboard()
    )

async def auto_end_session(context: ContextTypes.DEFAULT_TYPE):
    """پایان خودکار جلسه مطالعه بعد از 120 دقیقه"""
    job = context.job
    session_id = job.data
    
    # پایان دادن به جلسه
    result = end_study_session(session_id)
    
    if result:
        await context.bot.send_message(
            chat_id=job.chat_id,
            text=f"⏰ زمان مطالعه به پایان رسید!\n\n"
                 f"📚 درس: {result['subject_name']}\n"
                 f"⏱️ مدت زمان: {int(result['total_duration'])} دقیقه\n"
                 f"✅ جلسه مطالعه به صورت خودکار پایان یافت.",
            reply_markup=get_student_panel_keyboard()
        )
        
        # حذف job چک‌های ۲۰ دقیقه‌ای
        if 'check_jobs' in context.chat_data:
            current_jobs = [j for j in context.chat_data['check_jobs'] if j.name.startswith(f"check_{session_id}")]
            for j in current_jobs:
                j.schedule_removal()

async def handle_study_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت جلسه مطالعه"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "✅ پایان مطالعه":
        if 'current_session' in user_data:
            session_id = user_data['current_session']
            result = end_study_session(session_id)
            
            if result:
                # حذف job چک‌های ۲۰ دقیقه‌ای
                if 'check_jobs' in context.chat_data:
                    current_jobs = [job for job in context.chat_data['check_jobs'] if job.name.startswith(f"check_{session_id}")]
                    for job in current_jobs:
                        job.schedule_removal()
                
                await update.message.reply_text(
                    f"✅ مطالعه '{user_data['current_subject']}' با موفقیت پایان یافت!\n\n"
                    f"⏱️ مدت زمان: {int(result['total_duration'])} دقیقه\n"
                    f"📊 تعداد چک‌ها: {result.get('check_count', 0)}",
                    reply_markup=get_student_panel_keyboard()
                )
                
                # حذف اطلاعات جلسه
                del user_data['current_session']
                del user_data['current_subject']
            else:
                await update.message.reply_text(
                    "❌ خطا در پایان دادن به جلسه مطالعه.",
                    reply_markup=get_student_panel_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ جلسه مطالعه فعالی ندارید.",
                reply_markup=get_student_panel_keyboard()
            )
    
    elif text == "🔄 تغییر درس":
        # پایان دادن به جلسه فعلی
        if 'current_session' in user_data:
            session_id = user_data['current_session']
            end_study_session(session_id)
            
            # حذف job چک‌های ۲۰ دقیقه‌ای
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name.startswith(f"check_{session_id}")]
                for job in current_jobs:
                    job.schedule_removal()
            
            del user_data['current_session']
            del user_data['current_subject']
        
        # بازگشت به انتخاب درس
        grade = user_data.get('grade')
        advisor_id = user_data.get('advisor_id')
        day_number = user_data.get('selected_day')
        
        subjects_keyboard = get_subjects_keyboard(day_number, grade, advisor_id)
        if subjects_keyboard:
            await update.message.reply_text(
                "📚 لطفاً درس جدید را انتخاب کنید:",
                reply_markup=subjects_keyboard
            )
            return SELECT_SUBJECT
        else:
            await update.message.reply_text(
                "❌ درسی برای انتخاب موجود نیست.",
                reply_markup=get_student_panel_keyboard()
            )
    
    elif text == "📊 بازگشت به پنل":
        await update.message.reply_text(
            "پنل دانش‌آموز:",
            reply_markup=get_student_panel_keyboard()
        )
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=get_study_management_keyboard()
        )

async def handle_progress_check_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ به بررسی وضعیت مطالعه"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "⏹️ اتمام مطالعه":
        # پایان جلسه مطالعه
        if 'current_session' in user_data:
            session_id = user_data['current_session']
            result = end_study_session(session_id)
            
            if result:
                # حذف job چک‌های ۲۰ دقیقه‌ای
                if 'check_jobs' in context.chat_data:
                    current_jobs = [job for job in context.chat_data['check_jobs'] if job.name.startswith(f"check_{session_id}")]
                    for job in current_jobs:
                        job.schedule_removal()
                
                await update.message.reply_text(
                    f"✅ مطالعه '{user_data['current_subject']}' با موفقیت پایان یافت!\n\n"
                    f"⏱️ مدت زمان: {int(result['total_duration'])} دقیقه\n"
                    f"📊 تعداد چک‌ها: {result.get('check_count', 0)}",
                    reply_markup=get_student_panel_keyboard()
                )
                
                # حذف اطلاعات جلسه
                del user_data['current_session']
                del user_data['current_subject']
            else:
                await update.message.reply_text(
                    "❌ خطا در پایان دادن به جلسه مطالعه.",
                    reply_markup=get_student_panel_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ جلسه مطالعه فعالی ندارید.",
                reply_markup=get_student_panel_keyboard()
            )
        return STUDENT_PANEL
    
    else:
        # ذخیره پاسخ و ادامه جلسه
        response_text = ""
        if text == "✅ در حال پیشرفت":
            response_text = "عالی! ادامه بده 💪"
        elif text == "⚠️ مشکل دارم":
            response_text = "مشکلت چیه؟ می‌تونی ادامه بدی یا نیاز به کمک داری؟"
        elif text == "❌ متوقف کردم":
            response_text = "اشکال نداره! می‌تونی بعداً ادامه بدی."
        
        await update.message.reply_text(
            response_text + "\n\nادامه بده...",
            reply_markup=get_study_management_keyboard()
        )
        
        # ثبت پاسخ در دیتابیس
        if 'current_session' in user_data:
            session_id = user_data['current_session']
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO progress_checks (session_id, check_time, student_response)
                    VALUES (%s, %s, %s)
                """, (session_id, datetime.now(IRAN_TZ), text))
                conn.commit()
            conn.close()

async def show_student_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش دانش‌آموز با تاریخچه چک‌ها"""
    student = get_student(update.effective_user.id)
    if not student:
        await update.message.reply_text(
            "❌ شما به عنوان دانش‌آموز ثبت‌نام نکرده‌اید.",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    # دریافت گزارش روز جاری
    today = datetime.now(IRAN_TZ).day
    report = get_daily_report(student['id'], today)
    
    if not report:
        await update.message.reply_text(
            "📊 امروز هیچ مطالعه‌ای ثبت نشده است.",
            reply_markup=get_student_panel_keyboard()
        )
        return
    
    report_text = f"📊 گزارش مطالعه امروز ({today})\n\n"
    total_duration = 0
    
    for session in report:
        duration = session['total_duration'] or 0
        total_duration += duration
        
        status_emoji = "✅" if session['status'] == 'completed' else "🔄"
        start_time = session['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        
        report_text += (
            f"{status_emoji} {session['subject_name']}\n"
            f"🕐 شروع: {start_time}\n"
            f"⏱️ مدت: {int(duration)} دقیقه\n"
            f"🔢 چک‌ها: {session['check_count']}\n"
        )
        
        # نمایش تاریخچه چک‌ها
        if session['checks']:
            report_text += "📋 تاریخچه پاسخ‌ها:\n"
            for i, check in enumerate(session['checks'], 1):
                check_time = check['check_time'].astimezone(IRAN_TZ).strftime("%H:%M")
                response = check.get('student_response', 'بدون پاسخ')
                report_text += f"  {i}. {check_time}: {response}\n"
        
        report_text += "────────────────────\n"
    
    report_text += f"\n📈 مجموع مطالعه: {int(total_duration)} دقیقه"
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_student_panel_keyboard()
    )

async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پنل ادمین"""
    text = update.message.text
    
    if text == "🔙 بازگشت به منوی اصلی":
        await update.message.reply_text(
            "منوی اصلی:",
            reply_markup=get_main_menu_keyboard()
        )
        return GRADE_SELECTION
    
    elif text == "➕ افزودن برنامه جدید":
        await update.message.reply_text(
            "📝 برای ایجاد برنامه جدید، لطفاً پایه مورد نظر را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        return PLAN_GRADE
    
    elif text == "📝 مشاهده برنامه‌ها":
        await show_all_plans_admin(update, context)
    
    elif text == "✏️ ویرایش برنامه‌ها":
        await update.message.reply_text(
            "✏️ مدیریت ویرایش برنامه‌های درسی:",
            reply_markup=get_edit_plans_keyboard()
        )
        return EDIT_PLANS
    
    elif text == "👥 مشاهده دانش‌آموزان":
        await show_all_students_admin(update, context)
    
    elif text == "📊 گزارش دانش‌آموزی":
        await update.message.reply_text(
            "📊 گزارش‌های دانش‌آموزی:",
            reply_markup=get_student_reports_keyboard()
        )
        return STUDENT_REPORTS
    # ... سایر گزینه‌ها ...
    
    elif text == "🔍 جلسات فعال":
        await show_active_sessions(update, context)
    
    elif text == "📢 ارسال پیام همگانی":
        await update.message.reply_text(
            "📢 لطفاً پیام همگانی خود را ارسال کنید:",
            reply_markup=get_broadcast_keyboard()
        )
        return BROADCAST_MESSAGE
    
    elif text == "👨‍🏫 مدیریت مشاوران":
        await update.message.reply_text(
            "👨‍🏫 مدیریت مشاوران:",
            reply_markup=get_advisors_management_keyboard()
        )
        return ADD_ADVISOR
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منو را انتخاب کنید:",
            reply_markup=get_admin_panel_keyboard()
        )

# --- توابع مربوط به ویرایش برنامه‌ها ---

async def handle_edit_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ویرایش برنامه‌ها"""
    text = update.message.text
    user = update.effective_user
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل ادمین:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    elif text == "📋 مشاهده برنامه‌های من":
        # نمایش برنامه‌های ایجاد شده توسط کاربر فعلی
        advisor = get_advisor(user.id)
        if not advisor:
            await update.message.reply_text(
                "❌ شما به عنوان مشاور ثبت‌نام نکرده‌اید.",
                reply_markup=get_edit_plans_keyboard()
            )
            return EDIT_PLANS
        
        plans = get_plans_by_advisor(advisor['id'])
        await show_plans_for_editing(update, plans, "برنامه‌های شما:")
        return EDIT_PLANS
    
    elif text == "👁️ مشاهده تمام برنامه‌ها":
        # نمایش تمام برنامه‌ها (فقط برای ادمین)
        if not is_admin(user.id):
            await update.message.reply_text(
                "❌ فقط ادمین‌ها می‌توانند تمام برنامه‌ها را مشاهده کنند.",
                reply_markup=get_edit_plans_keyboard()
            )
            return EDIT_PLANS
        
        plans = get_all_plans()
        await show_plans_for_editing(update, plans, "تمام برنامه‌های درسی:")
        return EDIT_PLANS
    
    elif text == "🔍 جستجوی برنامه":
        await update.message.reply_text(
            "🔍 لطفاً شماره روز یا نام پایه را برای جستجو وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        context.user_data['search_mode'] = True
        return EDIT_PLANS
    
    else:
        # پردازش جستجو
        if context.user_data.get('search_mode'):
            search_term = text
            plans = search_plans(search_term, user.id)
            await show_plans_for_editing(update, plans, f"نتایج جستجو برای '{search_term}':")
            context.user_data['search_mode'] = False
            return EDIT_PLANS
        
        # اگر عدد وارد شده، آن را به عنوان کد برنامه در نظر بگیر
        try:
            plan_id = int(text)
            # مستقیماً تابع را فراخوانی کن
            return await handle_plan_selection_for_edit(update, context)
        except ValueError:
            await update.message.reply_text(
                "لطفاً یکی از گزینه‌ها را انتخاب کنید یا کد برنامه را وارد کنید:",
                reply_markup=get_edit_plans_keyboard()
            )
            return EDIT_PLANS
async def handle_plan_selection_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب برنامه برای ویرایش"""
    text = update.message.text.strip()

    try:
        plan_id = int(text)
        plan = get_study_plan_by_id(plan_id)
        
        if not plan:
            await update.message.reply_text(
                "❌ برنامه‌ای با این کد یافت نشد.",
                reply_markup=get_back_keyboard()
            )
            return EDIT_PLANS
        
        # بررسی دسترسی کاربر
        user = update.effective_user
        advisor = get_advisor(user.id)
        if not advisor:
            await update.message.reply_text(
                "❌ شما به عنوان مشاور ثبت‌نام نکرده‌اید.",
                reply_markup=get_edit_plans_keyboard()
            )
            return EDIT_PLANS
        
        if not advisor['is_admin'] and plan['created_by'] != advisor['id']:
            await update.message.reply_text(
                "❌ شما دسترسی ویرایش این برنامه را ندارید.",
                reply_markup=get_edit_plans_keyboard()
            )
            return EDIT_PLANS
        
        context.user_data['editing_plan'] = plan
        context.user_data['editing_plan_id'] = plan_id
        
        # نمایش اطلاعات برنامه
        day_text = f"روز {plan['day_number']}"
        if plan.get('day_description'):
            day_text += f" ({plan['day_description']})"
        
        plan_info = (
            f"📘 برنامه انتخابی:\n\n"
            f"{day_text} - {plan['grade']}\n"
            f"👤 مشاور: {plan['creator_name']}\n"
            f"📚 دروس:\n"
        )
        
        for i, subject in enumerate(plan['subjects'], 1):
            plan_info += f"  {i}. {subject['name']}\n"
        
        plan_info += f"\nلطفاً اقدام مورد نظر را انتخاب کنید:"
        
        await update.message.reply_text(
            plan_info,
            reply_markup=get_plan_actions_keyboard()
        )
        return EDIT_PLAN_DETAIL
        
    except ValueError:
        await update.message.reply_text(
            "❌ لطفاً یک کد برنامه معتبر وارد کنید.",
            reply_markup=get_back_keyboard()
        )
        return EDIT_PLANS
def search_plans(search_term: str, user_id: int):
    """جستجوی برنامه‌ها"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # بررسی اگر کاربر ادمین است
        advisor = get_advisor(user_id)
        if advisor and advisor['is_admin']:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE sp.day_number::TEXT LIKE %s OR sp.grade LIKE %s OR sp.day_description LIKE %s
                ORDER BY sp.day_number DESC
            """, (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
        else:
            cursor.execute("""
                SELECT sp.*, a.full_name as creator_name
                FROM study_plans sp
                LEFT JOIN advisors a ON sp.created_by = a.id
                WHERE (sp.day_number::TEXT LIKE %s OR sp.grade LIKE %s OR sp.day_description LIKE %s)
                AND sp.created_by = %s
                ORDER BY sp.day_number DESC
            """, (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', advisor['id']))
        
        plans = cursor.fetchall()
        for plan in plans:
            if plan['subjects']:
                if isinstance(plan['subjects'], str):
                    try:
                        plan['subjects'] = json.loads(plan['subjects'])
                    except json.JSONDecodeError:
                        plan['subjects'] = []
                elif not isinstance(plan['subjects'], list):
                    plan['subjects'] = []
            else:
                plan['subjects'] = []
    conn.close()
    return plans

async def show_plans_for_editing(update: Update, plans: List, title: str):
    """نمایش برنامه‌ها برای ویرایش"""
    if not plans:
        await update.message.reply_text(
            "📝 هیچ برنامه‌ای یافت نشد.",
            reply_markup=get_edit_plans_keyboard()
        )
        return
    
    plans_text = f"{title}\n\n"
    
    for i, plan in enumerate(plans, 1):
        day_text = f"روز {plan['day_number']}"
        if plan.get('day_description'):
            day_text += f" ({plan['day_description']})"
            
        plans_text += (
            f"{i}. 📘 {day_text} - {plan['grade']}\n"
            f"   👤 مشاور: {plan['creator_name']}\n"
            f"   📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"   🆔 کد برنامه: {plan['id']}\n"
            f"   ────────────────────\n"
        )
    
    plans_text += "\nبرای ویرایش یک برنامه، کد برنامه را وارد کنید:"
    
    await update.message.reply_text(
        plans_text,
        reply_markup=get_back_keyboard()
    )


async def handle_plan_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت اقدامات روی برنامه"""
    text = update.message.text
    user_data = context.user_data
    plan = user_data.get('editing_plan')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "✏️ مدیریت ویرایش برنامه‌های درسی:",
            reply_markup=get_edit_plans_keyboard()
        )
        return EDIT_PLANS
    
    elif text == "✏️ ویرایش اطلاعات":
        await update.message.reply_text(
            "✏️ لطفاً بخش مورد نظر برای ویرایش را انتخاب کنید:",
            reply_markup=get_edit_plan_details_keyboard()
        )
        return EDIT_PLAN_DETAIL
    
    elif text == "📝 ویرایش دروس":
        await update.message.reply_text(
            f"📝 ویرایش دروس برنامه:\n\n"
            f"در حال حاضر {len(plan['subjects'])} درس در برنامه وجود دارد:\n"
            f"{chr(10).join([f'{i+1}. {s['name']}' for i, s in enumerate(plan['subjects'])])}\n\n"
            f"لطفاً دروس جدید را به ترتیب وارد کنید (هر خط یک درس):\n"
            f"برای حذف یک درس، آن را حذف کنید.\n"
            f"برای اضافه کردن درس جدید، آن را اضافه کنید.\n\n"
            f"پس از اتمام، گزینه '✅ پایان' را انتخاب کنید.",
            reply_markup=get_plan_subjects_keyboard()
        )
        user_data['editing_subjects'] = plan['subjects'].copy()
        return EDIT_PLAN_SUBJECTS
    
    elif text == "❌ حذف برنامه":
        # حذف برنامه
        success = delete_study_plan(user_data['editing_plan_id'])
        if success:
            await update.message.reply_text(
                "✅ برنامه با موفقیت حذف شد.",
                reply_markup=get_edit_plans_keyboard()
            )
            # پاک کردن داده‌های ویرایش
            if 'editing_plan' in user_data:
                del user_data['editing_plan']
            if 'editing_plan_id' in user_data:
                del user_data['editing_plan_id']
            return EDIT_PLANS
        else:
            await update.message.reply_text(
                "❌ خطا در حذف برنامه.",
                reply_markup=get_plan_actions_keyboard()
            )
    
    elif text == "📅 ویرایش شماره روز":
        await update.message.reply_text(
            f"📅 شماره روز فعلی: {plan['day_number']}\n\n"
            f"لطفاً شماره روز جدید را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        user_data['editing_field'] = 'day_number'
    
    elif text == "📝 ویرایش توضیحات":
        current_desc = plan.get('day_description', 'تعریف نشده')
        await update.message.reply_text(
            f"📝 توضیحات روز فعلی: {current_desc}\n\n"
            f"لطفاً توضیحات جدید را وارد کنید (یا برای حذف توضیحات، 'حذف' را وارد کنید):",
            reply_markup=get_back_keyboard()
        )
        user_data['editing_field'] = 'day_description'
    
    elif text == "🎓 تغییر پایه تحصیلی":
        await update.message.reply_text(
            f"🎓 پایه تحصیلی فعلی: {plan['grade']}\n\n"
            f"لطفاً پایه تحصیلی جدید را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        user_data['editing_field'] = 'grade'
    
    else:
        # پردازش ویرایش فیلدها
        if 'editing_field' in user_data:
            field = user_data['editing_field']
            new_value = text
            
            if field == 'day_number':
                try:
                    new_value = int(new_value)
                except ValueError:
                    await update.message.reply_text(
                        "❌ شماره روز باید عدد باشد.",
                        reply_markup=get_edit_plan_details_keyboard()
                    )
                    return EDIT_PLAN_DETAIL
            
            elif field == 'day_description' and new_value.lower() == 'حذف':
                new_value = None
            
            elif field == 'grade' and new_value not in ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"]:
                await update.message.reply_text(
                    "❌ لطفاً یکی از پایه‌های معتبر را انتخاب کنید.",
                    reply_markup=get_plan_grade_keyboard()
                )
                return EDIT_PLAN_DETAIL
            
            # آپدیت برنامه
            if field == 'grade':
                new_value = "دوازدهم تجربی" if "تجربی" in new_value else "دوازدهم ریاضی"
            
            advisor = get_advisor(update.effective_user.id)
            success = update_study_plan(
                plan_id=user_data['editing_plan_id'],
                day_number=new_value if field == 'day_number' else plan['day_number'],
                day_description=new_value if field == 'day_description' else plan.get('day_description'),
                grade=new_value if field == 'grade' else plan['grade'],
                subjects=plan['subjects'],
                edited_by=advisor['id']
            )
            
            if success:
                await update.message.reply_text(
                    f"✅ {field.replace('_', ' ')} با موفقیت به‌روز شد.",
                    reply_markup=get_plan_actions_keyboard()
                )
                # به‌روزرسانی برنامه در user_data
                user_data['editing_plan'][field] = new_value
            else:
                await update.message.reply_text(
                    f"❌ خطا در به‌روزرسانی {field.replace('_', ' ')}.",
                    reply_markup=get_plan_actions_keyboard()
                )
            
            del user_data['editing_field']
    
    return EDIT_PLAN_DETAIL

async def handle_edit_plan_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ویرایش دروس برنامه"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "لطفاً اقدام مورد نظر را انتخاب کنید:",
            reply_markup=get_plan_actions_keyboard()
        )
        return EDIT_PLAN_DETAIL
    
    if text == "✅ پایان":
        if not user_data.get('editing_subjects'):
            await update.message.reply_text(
                "❌ حداقل یک درس باید در برنامه وجود داشته باشد.",
                reply_markup=get_plan_subjects_keyboard()
            )
            return EDIT_PLAN_SUBJECTS
        
        # ذخیره تغییرات دروس
        advisor = get_advisor(update.effective_user.id)
        plan = user_data['editing_plan']
        
        success = update_study_plan(
            plan_id=user_data['editing_plan_id'],
            day_number=plan['day_number'],
            day_description=plan.get('day_description'),
            grade=plan['grade'],
            subjects=user_data['editing_subjects'],
            edited_by=advisor['id']
        )
        
        if success:
            await update.message.reply_text(
                f"✅ دروس برنامه با موفقیت به‌روز شد!\n\n"
                f"📚 دروس جدید:\n"
                f"{chr(10).join([f'• {s['name']}' for s in user_data['editing_subjects']])}",
                reply_markup=get_plan_actions_keyboard()
            )
            # به‌روزرسانی برنامه در user_data
            user_data['editing_plan']['subjects'] = user_data['editing_subjects'].copy()
            del user_data['editing_subjects']
            return EDIT_PLAN_DETAIL
        else:
            await update.message.reply_text(
                "❌ خطا در به‌روزرسانی دروس.",
                reply_markup=get_plan_subjects_keyboard()
            )
    
    else:
        # اضافه کردن درس جدید یا جایگزینی لیست
        if 'editing_subjects' not in user_data:
            user_data['editing_subjects'] = []
        
        # اگر کاربر چند خط ارسال کرده، همه را به عنوان درس جدید در نظر بگیر
        lines = text.split('\n')
        new_subjects = []
        
        for i, line in enumerate(lines):
            if line.strip():  # اگر خط خالی نباشد
                new_subjects.append({
                    'name': line.strip(),
                    'order': i + 1
                })
        
        user_data['editing_subjects'] = new_subjects
        
        subjects_count = len(user_data['editing_subjects'])
        await update.message.reply_text(
            f"✅ {subjects_count} درس ذخیره شد.\n\n"
            f"در صورت نیاز تغییرات بیشتری اعمال کنید یا برای پایان، گزینه '✅ پایان' را انتخاب کنید.",
            reply_markup=get_plan_subjects_keyboard()
        )

async def show_all_plans_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تمام برنامه‌ها به ادمین"""
    plans = get_all_plans()
    
    if not plans:
        await update.message.reply_text(
            "📝 هیچ برنامه درسی موجود نیست.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    plans_text = "📚 برنامه‌های درسی موجود:\n\n"
    
    for plan in plans:
        day_text = f"روز {plan['day_number']}"
        if plan.get('day_description'):
            day_text += f" ({plan['day_description']})"
            
        plans_text += (
            f"📘 {day_text} - {plan['grade']}\n"
            f"👤 مشاور: {plan['creator_name']}\n"
            f"📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        plans_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_all_students_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تمام دانش‌آموزان به ادمین"""
    students = get_all_students()
    
    if not students:
        await update.message.reply_text(
            "👥 هیچ دانش‌آموزی ثبت‌نام نکرده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    students_text = "👥 لیست دانش‌آموزان:\n\n"
    
    for student in students:
        advisor_name = "تعیین نشده"
        if student['advisor_id']:
            advisor = get_advisor_by_id(student['advisor_id'])
            if advisor:
                advisor_name = advisor['full_name']
        
        students_text += (
            f"🎓 {student['full_name']}\n"
            f"📚 پایه: {student['grade']}\n"
            f"👤 مشاور: {advisor_name}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        students_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_overall_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش کلی مطالعه هر کاربر"""
    today = datetime.now(IRAN_TZ).day
    activities = get_all_students_daily_activity(today)
    
    if not activities:
        await update.message.reply_text(
            f"📊 امروز ({today}) هیچ فعالیتی ثبت نشده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    # گروه‌بندی فعالیت‌ها بر اساس دانش‌آموز
    student_activities = {}
    for activity in activities:
        if activity['full_name'] not in student_activities:
            student_activities[activity['full_name']] = {
                'grade': activity['grade'],
                'sessions': []
            }
        
        if activity['subject_name']:  # اگر جلسه‌ای وجود دارد
            student_activities[activity['full_name']]['sessions'].append(activity)
    
    report_text = f"📊 گزارش کلی مطالعه امروز ({today})\n\n"
    
    for student_name, data in student_activities.items():
        total_duration = sum(session['total_duration'] or 0 for session in data['sessions'])
        completed_sessions = sum(1 for session in data['sessions'] if session['status'] == 'completed')
        in_progress_sessions = sum(1 for session in data['sessions'] if session['status'] == 'in_progress')
        
        report_text += f"🎓 {student_name}\n"
        report_text += f"📚 پایه: {data['grade']}\n"
        report_text += f"⏱️ مجموع مطالعه: {int(total_duration)} دقیقه\n"
        report_text += f"✅ جلسات تکمیل شده: {completed_sessions}\n"
        report_text += f"🔄 جلسات فعال: {in_progress_sessions}\n"
        
        # نمایش جزئیات هر جلسه
        if data['sessions']:
            report_text += "📖 جزئیات دروس:\n"
            for session in data['sessions']:
                status_emoji = "✅" if session['status'] == 'completed' else "🔄"
                duration = session['total_duration'] or 0
                report_text += f"  {status_emoji} {session['subject_name']} - {int(duration)} دقیقه\n"
        
        report_text += "────────────────────\n"
    
    # اگر متن گزارش خیلی طولانی شد، آن را تقسیم کنیم
    if len(report_text) > 4000:
        parts = []
        current_part = ""
        lines = report_text.split('\n')
        
        for line in lines:
            if len(current_part + line + '\n') > 4000:
                parts.append(current_part)
                current_part = line + '\n'
            else:
                current_part += line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        for i, part in enumerate(parts):
            if i == 0:
                await update.message.reply_text(part, reply_markup=get_admin_panel_keyboard())
            else:
                await update.message.reply_text(part)
    else:
        await update.message.reply_text(
            report_text,
            reply_markup=get_admin_panel_keyboard()
        )

async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جلسات فعال با جزئیات کامل"""
    active_sessions = get_active_sessions()
    
    if not active_sessions:
        await update.message.reply_text(
            "🔍 هیچ جلسه مطالعه فعالی وجود ندارد.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    sessions_text = "🔍 جلسات مطالعه فعال:\n\n"
    
    for session in active_sessions:
        start_time = session['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        duration = (datetime.now(IRAN_TZ) - session['start_time'].astimezone(IRAN_TZ)).total_seconds() / 60
        
        # دریافت جزئیات چک‌ها
        checks = get_session_checks(session['session_id'])
        checks_details = []
        
        for check in checks:
            check_time = check['check_time'].astimezone(IRAN_TZ).strftime("%H:%M")
            response = check.get('student_response', 'بدون پاسخ')
            checks_details.append(f"{check_time}: {response}")
        
        sessions_text += (
            f"🎓 دانش‌آموز: {session['full_name']}\n"
            f"📚 پایه: {session['grade']}\n"
            f"👤 مشاور: {session['advisor_name'] or 'تعیین نشده'}\n"
            f"📖 درس: {session['subject_name']}\n"
            f"📅 روز: {session['day_number']}\n"
            f"🕐 شروع: {start_time}\n"
            f"⏱️ مدت: {int(duration)} دقیقه\n"
            f"🔢 تعداد چک‌ها: {session['check_count']}\n"
        )
        
        if checks_details:
            sessions_text += f"📋 تاریخچه چک‌ها:\n"
            for i, check_detail in enumerate(checks_details, 1):
                sessions_text += f"  {i}. {check_detail}\n"
        else:
            sessions_text += f"📋 چک‌ها: هنوز چکی انجام نشده\n"
        
        sessions_text += "────────────────────\n"
    
    # اگر متن خیلی طولانی شد، آن را تقسیم کنیم
    if len(sessions_text) > 4000:
        parts = []
        current_part = ""
        lines = sessions_text.split('\n')
        
        for line in lines:
            if len(current_part + line + '\n') > 4000:
                parts.append(current_part)
                current_part = line + '\n'
            else:
                current_part += line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        for i, part in enumerate(parts):
            if i == 0:
                await update.message.reply_text(part, reply_markup=get_admin_panel_keyboard())
            else:
                await update.message.reply_text(part)
    else:
        await update.message.reply_text(
            sessions_text,
            reply_markup=get_admin_panel_keyboard()
        )

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ارسال پیام همگانی"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل ادمین:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    # ارسال پیام به تمام دانش‌آموزان
    student_ids = get_all_students_telegram_ids()
    success_count = 0
    
    for student_id in student_ids:
        try:
            await context.bot.send_message(
                chat_id=student_id,
                text=f"📢 پیام همگانی:\n\n{text}"
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {student_id}: {e}")
    
    await update.message.reply_text(
        f"✅ پیام همگانی به {success_count} دانش‌آموز ارسال شد.",
        reply_markup=get_admin_panel_keyboard()
    )
    return ADMIN_PANEL

async def handle_add_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت افزودن مشاور"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل ادمین:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    elif text == "➕ افزودن مشاور جدید":
        await update.message.reply_text(
            "👤 لطفاً اطلاعات مشاور جدید را به فرمت زیر ارسال کنید:\n\n"
            "آیدی_تلگرام نام_کامل\n\n"
            "مثال:\n"
            "123456789 علی محمدی"
        )
        return ADD_ADVISOR
    
    elif text == "📋 لیست مشاوران":
        advisors = get_all_advisors()
        
        if not advisors:
            await update.message.reply_text(
                "👤 هیچ مشاوری ثبت نشده است.",
                reply_markup=get_advisors_management_keyboard()
            )
            return ADD_ADVISOR
        
        advisors_text = "👤 لیست مشاوران:\n\n"
        
        for advisor in advisors:
            role = "👑 ادمین" if advisor['is_admin'] else "👤 مشاور"
            advisors_text += (
                f"{role}: {advisor['full_name']}\n"
                f"🆔 آیدی: {advisor['telegram_id']}\n"
                f"────────────────────\n"
            )
        
        await update.message.reply_text(
            advisors_text,
            reply_markup=get_advisors_management_keyboard()
        )
    
    else:
        # پردازش اطلاعات مشاور جدید
        try:
            parts = text.split()
            if len(parts) < 2:
                raise ValueError("فرمت نامعتبر")
            
            telegram_id = int(parts[0])
            full_name = ' '.join(parts[1:])
            
            # ثبت مشاور جدید با دسترسی ادمین
            advisor_id = register_advisor(telegram_id, full_name, True)  # is_admin=True
            
            if advisor_id:
                await update.message.reply_text(
                    f"✅ ادمین جدید با موفقیت اضافه شد:\n\n"
                    f"👤 نام: {full_name}\n"
                    f"🆔 آیدی: {telegram_id}\n"
                    f"🎯 دسترسی: پنل مدیریت",
                    reply_markup=get_advisors_management_keyboard()
                )
            else:
                await update.message.reply_text(
                    "❌ خطا در ثبت ادمین جدید.",
                    reply_markup=get_advisors_management_keyboard()
                )
        
        except ValueError:
            await update.message.reply_text(
                "❌ فرمت اطلاعات نامعتبر است.\n\n"
                "لطفاً اطلاعات را به فرمت زیر ارسال کنید:\n"
                "آیدی_تلگرام نام_کامل\n\n"
                "مثال:\n"
                "123456789 علی محمدی",
                reply_markup=get_advisors_management_keyboard()
            )

async def handle_plan_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب پایه برای برنامه جدید"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل ادمین:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    if text in ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"]:
        grade = "دوازدهم تجربی" if "تجربی" in text else "دوازدهم ریاضی"
        context.user_data['plan_grade'] = grade
        
        await update.message.reply_text(
            f"📝 ایجاد برنامه جدید برای پایه {grade}\n\n"
            f"لطفاً شماره روز را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        return PLAN_DAY
    
    await update.message.reply_text(
        "لطفاً یکی از پایه‌ها را انتخاب کنید:",
        reply_markup=get_plan_grade_keyboard()
    )

async def handle_plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورود شماره روز و توضیحات"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "📝 لطفاً پایه مورد نظر را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        return PLAN_GRADE
    
    try:
        # استخراج شماره روز و توضیحات از متن
        parts = text.split('(', 1)
        day_number = int(parts[0].strip())
        
        day_description = None
        if len(parts) > 1 and parts[1].endswith(')'):
            day_description = parts[1][:-1].strip()
        
        context.user_data['plan_day'] = day_number
        context.user_data['day_description'] = day_description
        
        await update.message.reply_text(
            f"📅 روز {day_number} {f'({day_description})' if day_description else ''}\n\n"
            f"📚 حالا نام دروس را به ترتیب وارد کنید (هر خط یک درس):\n\n"
            f"مثال:\n"
            f"ریاضی\n"
            f"فیزیک\n"
            f"شیمی\n\n"
            f"پس از اتمام، گزینه '✅ پایان' را انتخاب کنید.",
            reply_markup=get_plan_subjects_keyboard()
        )
        return PLAN_SUBJECTS
    
    except ValueError:
        await update.message.reply_text(
            "❌ شماره روز باید یک عدد باشد.\n\n"
            f"لطفاً شماره روز را وارد کنید (مثال: '۵' یا '۵(زوج درس دهم)'):",
            reply_markup=get_back_keyboard()
        )

async def handle_plan_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورود دروس برنامه"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            f"📝 لطفاً شماره روز را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        return PLAN_DAY
    
    if text == "✅ پایان":
        if 'subjects' not in user_data or not user_data['subjects']:
            await update.message.reply_text(
                "❌ حداقل یک درس باید اضافه شود.",
                reply_markup=get_plan_subjects_keyboard()
            )
            return PLAN_SUBJECTS
        
        # ذخیره برنامه در دیتابیس
        advisor = get_advisor(update.effective_user.id)
        if not advisor:
            await update.message.reply_text(
                "❌ شما به عنوان مشاور ثبت‌نام نکرده‌اید.",
                reply_markup=get_admin_panel_keyboard()
            )
            return ADMIN_PANEL
        
        success = create_study_plan(
            day_number=user_data['plan_day'],
            day_description=user_data.get('day_description'),
            grade=user_data['plan_grade'],
            subjects=user_data['subjects'],
            created_by=advisor['id']
        )
        
        if success:
            day_text = f"روز {user_data['plan_day']}"
            if user_data.get('day_description'):
                day_text += f" ({user_data['day_description']})"
            
            subjects_text = '\n'.join([f"• {s['name']}" for s in user_data['subjects']])
            
            await update.message.reply_text(
                f"✅ برنامه درسی با موفقیت ایجاد شد!\n\n"
                f"📘 {day_text} - {user_data['plan_grade']}\n"
                f"📚 دروس:\n{subjects_text}",
                reply_markup=get_admin_panel_keyboard()
            )
            
            # پاک کردن داده‌های موقت
            if 'plan_day' in user_data:
                del user_data['plan_day']
            if 'plan_grade' in user_data:
                del user_data['plan_grade']
            if 'day_description' in user_data:
                del user_data['day_description']
            if 'subjects' in user_data:
                del user_data['subjects']
            
            return ADMIN_PANEL
        else:
            await update.message.reply_text(
                "❌ خطا در ایجاد برنامه درسی.",
                reply_markup=get_admin_panel_keyboard()
            )
            return ADMIN_PANEL
    
    else:
        # اضافه کردن درس جدید
        if 'subjects' not in user_data:
            user_data['subjects'] = []
        
        user_data['subjects'].append({
            'name': text.strip(),
            'order': len(user_data['subjects']) + 1
        })
        
        subjects_count = len(user_data['subjects'])
        await update.message.reply_text(
            f"✅ درس '{text}' اضافه شد. ({subjects_count} درس)\n\n"
            f"درس بعدی را وارد کنید یا برای پایان، گزینه '✅ پایان' را انتخاب کنید.",
            reply_markup=get_plan_subjects_keyboard()
        )

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های ناشناخته"""
    await update.message.reply_text(
        "❌ دستور نامعتبر!\n\n"
        "لطفاً از گزینه‌های منو استفاده کنید.",
        reply_markup=get_main_menu_keyboard()
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاها"""
    logger.error(f"Error: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ خطایی رخ داد! لطفاً مجدداً تلاش کنید.",
            reply_markup=get_main_menu_keyboard()
        )

def add_day_description_column():
    """اضافه کردن ستون day_description به جدول study_plans"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE study_plans 
            ADD COLUMN IF NOT EXISTS day_description VARCHAR(255)
        """)
        conn.commit()
    conn.close()
    logger.info("✅ Column day_description added to study_plans table")

def main():
    """تابع اصلی"""
    # ایجاد دیتابیس و جداول
    init_database()
    
    # اضافه کردن ستون day_description اگر وجود ندارد
    add_day_description_column()
    
    # ایجاد اپلیکیشن
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # تنظیم job گزارش شبانه
    setup_nightly_report_job(application)
    
    # ایجاد Conversation Handler با حالت‌های جدید
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            GRADE_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_grade_selection)
            ],
            SELECT_ADVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_advisor_selection)
            ],
            SELECT_DAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_day_selection)
            ],
            SELECT_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subject_selection)
            ],
            STUDENT_PANEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_study_management),
                MessageHandler(filters.TEXT & filters.Regex("^(✅ در حال پیشرفت|⚠️ مشکل دارم|❌ متوقف کردم|⏹️ اتمام مطالعه)$"), 
                             handle_progress_check_response)
            ],
            ADMIN_PANEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_panel)
            ],
            PLAN_DAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plan_day)
            ],
            PLAN_GRADE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plan_grade)
            ],
            PLAN_SUBJECTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plan_subjects)
            ],
            BROADCAST_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message)
            ],
            ADD_ADVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_advisor)
            ],
            EDIT_PLANS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_plans)
            ],
            EDIT_PLAN_DETAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plan_actions)
            ],
            EDIT_PLAN_SUBJECTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_plan_subjects)
            ],
            STUDENT_REPORTS: [  # حالت جدید
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_reports)
            ],
            STUDENT_DETAILS: [
                MessageHandler(filters.TEXT & filters.Regex("^(🔙 بازگشت به لیست دانش‌آموزان|🎓 .*)$"), handle_student_selection),
                MessageHandler(filters.TEXT & filters.Regex("^(📅 گزارش ۷ روز اخیر|📊 گزارش روزانه \(دیروز\)|🔙 بازگشت)$"), handle_student_details)
    
            ]
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    # شروع ربات
    logger.info("🤖 ربات شروع به کار کرد...")
    logger.info("📊 سیستم گزارش‌دهی شبانه فعال شد")
    application.run_polling()
if __name__ == '__main__':
    main()
