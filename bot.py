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
GRADE_SELECTION, STUDENT_PANEL, ADMIN_PANEL, WAITING_PLAN, PLAN_DAY, PLAN_GRADE, PLAN_SUBJECTS, BROADCAST_MESSAGE, SELECT_DAY, SELECT_SUBJECT, SELECT_ADVISOR, ADD_ADVISOR = range(12)

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
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
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
    """دریافت جلسات فعال"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT s.full_name, ss.subject_name, ss.start_time, ss.check_count, ss.id, ss.student_id, sp.created_by as advisor_id
            FROM study_sessions ss
            JOIN students s ON ss.student_id = s.id
            LEFT JOIN study_plans sp ON ss.day_number = sp.day_number AND s.grade = sp.grade
            WHERE ss.status = 'in_progress'
            ORDER BY ss.start_time
        """)
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

# --- توابع گزارش‌گیری ---

def get_daily_report(student_id: int, day_number: int):
    """گزارش روزانه دانش‌آموز"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT 
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
        result = cursor.fetchall()
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
    """پنل ادمین"""
    keyboard = [
        ["➕ افزودن برنامه جدید", "📝 مشاهده برنامه‌ها"],
        ["👥 مشاهده دانش‌آموزان", "📊 گزارش کلی"],
        ["🔍 جلسات فعال", "📢 ارسال پیام همگانی"],
        ["👨‍🏫 مدیریت مشاوران", "🔙 بازگشت به منوی اصلی"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
        
        # پایان دادن به جلسه فعال قبلی (اگر وجود دارد)
        active_session = get_student_active_session(user_data['student_id'])
        if active_session:
            end_study_session(active_session['id'])
            # حذف job چک‌های ۲۰ دقیقه‌ای قبلی
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{active_session['id']}"]
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
    """نمایش گزارش دانش‌آموز"""
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
        
        report_text += (
            f"{status_emoji} {session['subject_name']}\n"
            f"⏱️ مدت: {int(duration)} دقیقه\n"
            f"🔢 چک‌ها: {session['check_count']}\n"
            f"────────────────────\n"
        )
    
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
    
    elif text == "👥 مشاهده دانش‌آموزان":
        await show_all_students_admin(update, context)
    
    elif text == "📊 گزارش کلی":
        await show_overall_report(update, context)
    
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
    """نمایش گزارش کلی"""
    today = datetime.now(IRAN_TZ).day
    activities = get_all_students_daily_activity(today)
    
    if not activities:
        await update.message.reply_text(
            "📊 امروز هیچ فعالیتی ثبت نشده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    report_text = f"📊 گزارش کلی فعالیت امروز ({today})\n\n"
    
    current_student = None
    student_sessions = []
    
    for activity in activities:
        if activity['full_name'] != current_student:
            if current_student and student_sessions:
                # محاسبه مجموع برای دانش‌آموز قبلی
                total_duration = sum(session['total_duration'] or 0 for session in student_sessions)
                completed_count = sum(1 for session in student_sessions if session['status'] == 'completed')
                
                report_text += (
                    f"🎓 {current_student}\n"
                    f"📚 پایه: {student_sessions[0]['grade']}\n"
                    f"✅ جلسات تکمیل شده: {completed_count}\n"
                    f"⏱️ مجموع مطالعه: {int(total_duration)} دقیقه\n"
                    f"────────────────────\n"
                )
            
            current_student = activity['full_name']
            student_sessions = []
        
        if activity['subject_name']:  # اگر جلسه‌ای وجود دارد
            student_sessions.append(activity)
    
    # اضافه کردن آخرین دانش‌آموز
    if current_student and student_sessions:
        total_duration = sum(session['total_duration'] or 0 for session in student_sessions)
        completed_count = sum(1 for session in student_sessions if session['status'] == 'completed')
        
        report_text += (
            f"🎓 {current_student}\n"
            f"📚 پایه: {student_sessions[0]['grade']}\n"
            f"✅ جلسات تکمیل شده: {completed_count}\n"
            f"⏱️ مجموع مطالعه: {int(total_duration)} دقیقه\n"
        )
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جلسات فعال"""
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
        
        sessions_text += (
            f"🎓 {session['full_name']}\n"
            f"📚 درس: {session['subject_name']}\n"
            f"🕐 شروع: {start_time}\n"
            f"⏱️ مدت: {int(duration)} دقیقه\n"
            f"🔢 چک‌ها: {session['check_count']}\n"
            f"────────────────────\n"
        )
    
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
    
    # ایجاد Conversation Handler
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
                MessageHandler(filters.TEXT & filters.Regex("^(✅ در حال پیشرفت|⚠️ مشکل دارم|❌ متوقف کردم|ℹ️ اتمام مطالعه)$"), handle_progress_check_response)
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
            ]
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    
    # شروع ربات
    logger.info("🤖 ربات شروع به کار کرد...")
    application.run_polling()

if __name__ == '__main__':
    main()
