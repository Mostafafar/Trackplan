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
        
        # جدول برنامه‌های درسی
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS study_plans (
                id SERIAL PRIMARY KEY,
                day_number INTEGER NOT NULL,
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
        
        conn.commit()
        logger.info("✅ Database tables created successfully")
    
    create_default_admin()
    conn.close()

def create_default_admin():
    """ایجاد ادمین پیش‌فرض"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO advisors (telegram_id, full_name, is_admin) 
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (6680287530, "مدیر سیستم", True))
            conn.commit()
            logger.info("✅ Default admin created")
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

def create_study_plan(day_number: int, grade: str, subjects: List[Dict], created_by: int):
    """ایجاد برنامه درسی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO study_plans (day_number, grade, subjects, created_by)
            VALUES (%s, %s, %s, %s)
        """, (day_number, grade, json.dumps(subjects), created_by))
        conn.commit()
    conn.close()
    return True

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
            SELECT s.full_name, ss.subject_name, ss.start_time, ss.check_count, ss.id
            FROM study_sessions ss
            JOIN students s ON ss.student_id = s.id
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
        ["❌ متوقف کردم"]
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
        ["پایان"],
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
        keyboard.append([f"📅 روز {plan['day_number']}"])
    
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
    
    # بررسی ادمین بودن
    if is_admin(user.id):
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
    
    elif text == "📊 گزارش من":
        student = get_student(update.effective_user.id)
        if student:
            await show_student_report(update, context)
        else:
            await update.message.reply_text(
                "❌ شما به عنوان دانش‌آموز ثبت‌نام نکرده‌اید.",
                reply_markup=get_main_menu_keyboard()
            )
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منو را انتخاب کنید:",
            reply_markup=get_main_menu_keyboard()
        )

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
        plans_text += (
            f"📘 روز {plan['day_number']} - {plan['grade']}\n"
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

# --- تغییرات در تابع get_advisors_keyboard ---

def get_advisors_keyboard(grade: str):
    """ایجاد دکمه‌های مشاوران برای یک پایه"""
    advisors = get_advisors_with_plans_for_grade(grade)
    
    # اگر هیچ مشاوری برای این پایه برنامه ندارد، تمام مشاوران را نشان بده
    if not advisors:
        advisors = get_all_advisors_for_selection()
    
    if not advisors:
        return None
    
    keyboard = []
    for advisor in advisors:
        keyboard.append([f"👤 {advisor['full_name']}"])
    
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
            day_number = int(text.replace("📅 روز ", ""))
            user_data['selected_day'] = day_number
            
            # نمایش دروس این روز
            subjects_keyboard = get_subjects_keyboard(day_number, grade, advisor_id)
            if not subjects_keyboard:
                await update.message.reply_text(
                    f"❌ برای روز {day_number} هیچ درسی تعریف نشده است.",
                    reply_markup=get_days_keyboard(grade, advisor_id)
                )
                return SELECT_DAY
            
            await update.message.reply_text(
                f"📘 برنامه روز {day_number} - {grade}\n"
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
        
        await update.message.reply_text(
            f"🎯 مطالعه '{subject_name}' شروع شد!\n"
            f"📅 روز: {day_number}\n"
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
        
        return STUDENT_PANEL
    
    await update.message.reply_text(
        "لطفاً یکی از دروس را انتخاب کنید:",
        reply_markup=get_subjects_keyboard(day_number, grade, advisor_id)
    )

async def handle_student_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پنل دانش‌آموز"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    advisor_id = user_data.get('advisor_id')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "منوی اصلی:",
            reply_markup=get_main_menu_keyboard()
        )
        return GRADE_SELECTION
    
    elif text == "🎯 شروع مطالعه جدید":
        # بازگشت به انتخاب روز
        days_keyboard = get_days_keyboard(grade, advisor_id)
        if days_keyboard:
            await update.message.reply_text(
                "📅 لطفاً روز مورد نظر را انتخاب کنید:",
                reply_markup=days_keyboard
            )
            return SELECT_DAY
        else:
            await update.message.reply_text(
                "❌ هیچ برنامه‌ای برای پایه شما موجود نیست.",
                reply_markup=get_student_panel_keyboard()
            )
    
    elif text == "🔄 تغییر درس":
        # پایان جلسه فعلی و بازگشت به انتخاب درس
        session_id = user_data.get('current_session')
        if session_id:
            end_study_session(session_id)
            # حذف job چک‌های ۲۰ دقیقه‌ای
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{session_id}"]
                for job in current_jobs:
                    job.schedule_removal()
        
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
                "❌ هیچ درسی برای این روز موجود نیست.",
                reply_markup=get_student_panel_keyboard()
            )
    
    elif text == "✅ پایان مطالعه":
        session_id = user_data.get('current_session')
        if session_id:
            result = end_study_session(session_id)
            # حذف job چک‌های ۲۰ دقیقه‌ای
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{session_id}"]
                for job in current_jobs:
                    job.schedule_removal()
            
            if result:
                duration = int(result['total_duration']) if result['total_duration'] else 0
                await update.message.reply_text(
                    f"✅ مطالعه '{result['subject_name']}' با موفقیت به پایان رسید!\n"
                    f"⏱️ مدت مطالعه: {duration} دقیقه\n\n"
                    f"📊 برای مشاهده گزارش به پنل مراجعه کنید.",
                    reply_markup=get_student_panel_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ جلسه مطالعه فعالی ندارید.",
                reply_markup=get_student_panel_keyboard()
            )
    
    elif text == "📊 گزارش روزانه":
        await show_student_report(update, context)
    
    elif text == "📊 بازگشت به پنل":
        await update.message.reply_text(
            "پنل دانش‌آموز:",
            reply_markup=get_student_panel_keyboard()
        )
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=get_student_panel_keyboard()
        )

async def show_student_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش دانش‌آموز"""
    student = get_student(update.effective_user.id)
    if not student:
        await update.message.reply_text(
            "❌ شما به عنوان دانش‌آموز ثبت‌نام نکرده‌اید.",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    day_number = context.user_data.get('selected_day', 1)
    report = get_daily_report(student['id'], day_number)
    
    if not report:
        await update.message.reply_text(
            f"📊 گزارش روز {day_number}:\n"
            f"❌ هیچ فعالیتی ثبت نشده است.",
            reply_markup=get_student_panel_keyboard()
        )
        return
    
    total_time = sum(session['total_duration'] or 0 for session in report)
    completed_subjects = sum(1 for session in report if session['status'] == 'completed')
    
    report_text = f"📊 گزارش روز {day_number} - {student['full_name']}\n\n"
    
    for session in report:
        start_time = session['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        end_time = session['end_time'].astimezone(IRAN_TZ).strftime("%H:%M") if session['end_time'] else "در حال مطالعه"
        duration = session['total_duration'] or "نامشخص"
        
        report_text += (
            f"📚 {session['subject_name']}\n"
            f"⏰ {start_time} - {end_time} ({duration} دقیقه)\n"
            f"✅ وضعیت: {session['status']}\n"
            f"🔍 تعداد چک‌ها: {session['check_count']}\n"
            f"────────────────────\n"
        )
    
    report_text += f"\n📈 جمع‌بندی:\n⏱️ کل زمان مطالعه: {total_time} دقیقه\n✅ دروس تکمیل شده: {completed_subjects}"
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_student_panel_keyboard()
    )

# --- Admin Handlers ---

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
            "📝 افزودن برنامه جدید\n\n"
            "لطفاً شماره روز را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        return PLAN_DAY
    
    elif text == "📝 مشاهده برنامه‌ها":
        await show_all_plans_admin(update, context)
    
    elif text == "👥 مشاهده دانش‌آموزان":
        await show_all_students_admin(update, context)
    
    elif text == "📊 گزارش کلی":
        await show_overall_report(update, context)
    
    elif text == "🔍 جلسات فعال":
        await show_active_sessions_admin(update, context)
    
    elif text == "📢 ارسال پیام همگانی":
        await update.message.reply_text(
            "📢 ارسال پیام همگانی\n\n"
            "لطفاً پیام خود را وارد کنید:",
            reply_markup=get_broadcast_keyboard()
        )
        return BROADCAST_MESSAGE
    
    elif text == "👨‍🏫 مدیریت مشاوران":
        await update.message.reply_text(
            "👨‍🏫 مدیریت مشاوران\n\n"
            "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=get_advisors_management_keyboard()
        )
        return ADD_ADVISOR
    
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منو را انتخاب کنید:",
            reply_markup=get_admin_panel_keyboard()
        )

async def handle_advisors_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت مشاوران"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل مدیریت:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    elif text == "➕ افزودن مشاور جدید":
        await update.message.reply_text(
            "👨‍🏫 افزودن مشاور جدید\n\n"
            "لطفاً اطلاعات مشاور را به این فرمت وارد کنید:\n"
            "آیدی_تلگرام,نام_کامل\n\n"
            "مثال:\n"
            "123456789,احمد احمدی",
            reply_markup=get_back_keyboard()
        )
        context.user_data['awaiting_advisor_info'] = True
        return ADD_ADVISOR
    
    elif text == "📋 لیست مشاوران":
        advisors = get_all_advisors()
        if not advisors:
            await update.message.reply_text(
                "❌ هیچ مشاوری ثبت نشده است.",
                reply_markup=get_advisors_management_keyboard()
            )
            return ADD_ADVISOR
        
        advisors_text = "👨‍🏫 لیست مشاوران:\n\n"
        for advisor in advisors:
            role = "👑 مدیر" if advisor['is_admin'] else "👤 مشاور"
            advisors_text += f"{role} - {advisor['full_name']}\n"
            advisors_text += f"🆔 آیدی: {advisor['telegram_id']}\n"
            advisors_text += f"📅 تاریخ ثبت: {advisor['created_at'].astimezone(IRAN_TZ).strftime('%Y-%m-%d %H:%M')}\n"
            advisors_text += "────────────────────\n"
        
        await update.message.reply_text(
            advisors_text,
            reply_markup=get_advisors_management_keyboard()
        )
    
    else:
        # پردازش اطلاعات مشاور جدید
        if context.user_data.get('awaiting_advisor_info'):
            try:
                parts = text.split(',')
                if len(parts) != 2:
                    raise ValueError
                
                telegram_id = int(parts[0].strip())
                full_name = parts[1].strip()
                
                # ثبت مشاور جدید
                advisor_id = register_advisor(telegram_id, full_name, False)
                
                if advisor_id:
                    await update.message.reply_text(
                        f"✅ مشاور جدید با موفقیت ثبت شد!\n\n"
                        f"👤 نام: {full_name}\n"
                        f"🆔 آیدی: {telegram_id}",
                        reply_markup=get_advisors_management_keyboard()
                    )
                else:
                    await update.message.reply_text(
                        "❌ خطا در ثبت مشاور.",
                        reply_markup=get_advisors_management_keyboard()
                    )
                
                context.user_data.pop('awaiting_advisor_info', None)
                
            except (ValueError, IndexError):
                await update.message.reply_text(
                    "❌ فرمت اطلاعات صحیح نیست.\n"
                    "لطفاً به این فرمت وارد کنید:\n"
                    "آیدی_تلگرام,نام_کامل\n\n"
                    "مثال:\n"
                    "123456789,احمد احمدی",
                    reply_markup=get_back_keyboard()
                )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ خطا در ثبت مشاور: {e}",
                    reply_markup=get_advisors_management_keyboard()
                )
                context.user_data.pop('awaiting_advisor_info', None)

async def handle_plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت شماره روز برای برنامه جدید"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل مدیریت:",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    try:
        day_number = int(text)
        if day_number <= 0:
            raise ValueError
        
        context.user_data['plan_day'] = day_number
        await update.message.reply_text(
            f"📘 روز {day_number} انتخاب شد.\n\n"
            f"لطفاً پایه تحصیلی را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        return PLAN_GRADE
        
    except ValueError:
        await update.message.reply_text(
            "❌ شماره روز باید یک عدد صحیح مثبت باشد.\n"
            "لطفاً مجدداً وارد کنید:",
            reply_markup=get_back_keyboard()
        )

async def handle_plan_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت پایه تحصیلی برای برنامه جدید"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "لطفاً شماره روز را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        return PLAN_DAY
    
    if text in ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"]:
        grade = "دوازدهم تجربی" if "تجربی" in text else "دوازدهم ریاضی"
        context.user_data['plan_grade'] = grade
        context.user_data['plan_subjects'] = []
        
        await update.message.reply_text(
            f"📘 پایه {grade} انتخاب شد.\n\n"
            f"لطفاً نام درس اول را وارد کنید:",
            reply_markup=get_plan_subjects_keyboard()
        )
        return PLAN_SUBJECTS
    
    await update.message.reply_text(
        "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=get_plan_grade_keyboard()
    )

async def handle_plan_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت دروس برنامه"""
    text = update.message.text
    user_data = context.user_data
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "لطفاً پایه تحصیلی را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        return PLAN_GRADE
    
    if text == "پایان":
        if not user_data['plan_subjects']:
            await update.message.reply_text(
                "❌ حداقل یک درس باید اضافه کنید.\n"
                "لطفاً نام درس را وارد کنید:",
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
        
        create_study_plan(
            day_number=user_data['plan_day'],
            grade=user_data['plan_grade'],
            subjects=user_data['plan_subjects'],
            created_by=advisor['id']
        )
        
        subjects_list = "\n".join([f"📚 {s['name']}" for s in user_data['plan_subjects']])
        
        await update.message.reply_text(
            f"✅ برنامه درسی با موفقیت ذخیره شد!\n\n"
            f"📘 روز {user_data['plan_day']} - {user_data['plan_grade']}\n"
            f"📚 دروس:\n{subjects_list}",
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    # اضافه کردن درس جدید
    subject_name = text.strip()
    if subject_name:
        user_data['plan_subjects'].append({"name": subject_name})
        
        await update.message.reply_text(
            f"✅ درس '{subject_name}' اضافه شد.\n\n"
            f"برای اضافه کردن درس دیگر، نام آن را وارد کنید.\n"
            f"برای پایان، دکمه 'پایان' را بزنید.",
            reply_markup=get_plan_subjects_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ نام درس نمی‌تواند خالی باشد.\n"
            "لطفاً مجدداً وارد کنید:",
            reply_markup=get_plan_subjects_keyboard()
        )

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ارسال پیام همگانی"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "پنل مدیریت:",
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
            logger.error(f"Failed to send message to {student_id}: {e}")
    
    await update.message.reply_text(
        f"✅ پیام همگانی ارسال شد!\n"
        f"📊 تعداد موفق: {success_count} از {len(student_ids)}",
        reply_markup=get_admin_panel_keyboard()
    )
    return ADMIN_PANEL

async def show_all_plans_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تمام برنامه‌ها به ادمین"""
    plans = get_all_plans()
    
    if not plans:
        await update.message.reply_text(
            "📝 در حال حاضر هیچ برنامه درسی موجود نیست.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    plans_text = "📚 برنامه‌های درسی:\n\n"
    
    for plan in plans:
        plans_text += (
            f"📘 روز {plan['day_number']} - {plan['grade']}\n"
            f"👤 مشاور: {plan['creator_name']}\n"
            f"📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"📅 تاریخ ایجاد: {plan['created_at'].astimezone(IRAN_TZ).strftime('%Y-%m-%d %H:%M')}\n"
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
    
    students_text = "👥 دانش‌آموزان ثبت‌نام شده:\n\n"
    
    for student in students:
        advisor_name = "نامشخص"
        if student['advisor_id']:
            advisor = get_advisor_by_id(student['advisor_id'])
            if advisor:
                advisor_name = advisor['full_name']
        
        students_text += (
            f"👤 {student['full_name']}\n"
            f"🎓 {student['grade']}\n"
            f"📞 آیدی: {student['telegram_id']}\n"
            f"👨‍🏫 مشاور: {advisor_name}\n"
            f"📅 تاریخ ثبت‌نام: {student['created_at'].astimezone(IRAN_TZ).strftime('%Y-%m-%d %H:%M')}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        students_text,
        reply_markup=get_admin_panel_keyboard()
    )

def get_advisor_by_id(advisor_id: int):
    """دریافت اطلاعات مشاور با آیدی"""
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM advisors WHERE id = %s", (advisor_id,))
        result = cursor.fetchone()
    conn.close()
    return result

async def show_overall_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش کلی"""
    day_number = 1  # می‌تواند از ورودی دریافت شود
    activities = get_all_students_daily_activity(day_number)
    
    if not activities:
        await update.message.reply_text(
            f"📊 گزارش روز {day_number}:\n"
            f"❌ هیچ فعالیتی ثبت نشده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    # محاسبه آمار کلی
    total_students = len(set(activity['full_name'] for activity in activities if activity['subject_name']))
    active_students = len(set(activity['full_name'] for activity in activities if activity['status'] == 'in_progress'))
    total_study_time = sum(activity['total_duration'] or 0 for activity in activities)
    
    report_text = f"📊 گزارش کلی روز {day_number}\n\n"
    report_text += f"👥 کل دانش‌آموزان: {total_students}\n"
    report_text += f"🎯 دانش‌آموزان فعال: {active_students}\n"
    report_text += f"⏱️ کل زمان مطالعه: {total_study_time} دقیقه\n\n"
    
    # فعالیت‌های هر دانش‌آموز
    student_activities = {}
    for activity in activities:
        if activity['full_name'] not in student_activities:
            student_activities[activity['full_name']] = {
                'grade': activity['grade'],
                'subjects': []
            }
        
        if activity['subject_name']:
            student_activities[activity['full_name']]['subjects'].append({
                'subject': activity['subject_name'],
                'duration': activity['total_duration'] or 0,
                'status': activity['status'],
                'checks': activity['check_count'] or 0
            })
    
    for student_name, data in student_activities.items():
        total_time = sum(subject['duration'] for subject in data['subjects'])
        completed = sum(1 for subject in data['subjects'] if subject['status'] == 'completed')
        
        report_text += (
            f"👤 {student_name} ({data['grade']})\n"
            f"⏱️ {total_time} دقیقه - ✅ {completed} درس\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_active_sessions_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جلسات فعال به ادمین"""
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
        duration = int((datetime.now(IRAN_TZ) - session['start_time'].astimezone(IRAN_TZ)).total_seconds() / 60)
        
        sessions_text += (
            f"👤 {session['full_name']}\n"
            f"📚 {session['subject_name']}\n"
            f"🕐 شروع: {start_time} ({duration} دقیقه گذشته)\n"
            f"🔍 چک‌ها: {session['check_count']}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        sessions_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def progress_check(context: ContextTypes.DEFAULT_TYPE):
    """بررسی ۲۰ دقیقه‌ای پیشرفت مطالعه"""
    job = context.job
    session_id = job.data
    
    # دریافت اطلاعات جلسه
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT ss.*, s.telegram_id, s.full_name
            FROM study_sessions ss
            JOIN students s ON ss.student_id = s.id
            WHERE ss.id = %s AND ss.status = 'in_progress'
        """, (session_id,))
        session = cursor.fetchone()
    
    if not session:
        # اگر جلسه فعال نیست، job را متوقف کن
        job.schedule_removal()
        return
    
    # به‌روزرسانی زمان آخرین بررسی
    update_check_time(session_id)
    
    # ارسال پیام بررسی
    try:
        await context.bot.send_message(
            chat_id=session['telegram_id'],
            text=f"🔍 بررسی ۲۰ دقیقه‌ای\n\n"
                 f"👤 {session['full_name']}\n"
                 f"📚 {session['subject_name']}\n\n"
                 f"لطفاً وضعیت مطالعه خود را گزارش دهید:",
            reply_markup=get_progress_check_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send progress check to {session['telegram_id']}: {e}")

async def handle_progress_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پاسخ‌های بررسی ۲۰ دقیقه‌ای"""
    text = update.message.text
    user = update.effective_user
    
    # پیدا کردن جلسه فعال کاربر
    student = get_student(user.id)
    if not student:
        await update.message.reply_text(
            "❌ شما به عنوان دانش‌آموز ثبت‌نام نکرده‌اید.",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    active_session = get_student_active_session(student['id'])
    if not active_session:
        await update.message.reply_text(
            "❌ جلسه مطالعه فعالی ندارید.",
            reply_markup=get_student_panel_keyboard()
        )
        return
    
    # ذخیره پاسخ
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO progress_checks 
            (session_id, check_time, student_response, response_time)
            VALUES (%s, %s, %s, %s)
        """, (active_session['id'], datetime.now(IRAN_TZ), text, datetime.now(IRAN_TZ)))
        conn.commit()
    conn.close()
    
    if text == "✅ در حال پیشرفت":
        response_text = "✅ عالی! ادامه بده..."
    elif text == "⚠️ مشکل دارم":
        response_text = "⚠️ اگر نیاز به کمک داری، با مشاورت در میان بذار."
    elif text == "❌ متوقف کردم":
        response_text = "❌ مطالعه متوقف شد. می‌خواهی درس جدیدی شروع کنی؟"
        # پایان جلسه
        end_study_session(active_session['id'])
        # حذف job
        if 'check_jobs' in context.chat_data:
            current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{active_session['id']}"]
            for job in current_jobs:
                job.schedule_removal()
    else:
        response_text = "پاسخ شما ثبت شد."
    
    await update.message.reply_text(
        response_text,
        reply_markup=get_study_management_keyboard()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو عملیات"""
    await update.message.reply_text(
        "عملیات لغو شد.",
        reply_markup=get_main_menu_keyboard()
    )
    return GRADE_SELECTION

def main():
    """تابع اصلی"""
    # ایجاد دیتابیس و جداول
    init_database()
    
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_panel),
                MessageHandler(filters.TEXT & filters.Regex("^(✅ در حال پیشرفت|⚠️ مشکل دارم|❌ متوقف کردم)$"), handle_progress_response)
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_advisors_management)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    
    # شروع ربات
    logger.info("🤖 ربات شروع به کار کرد...")
    application.run_polling()
