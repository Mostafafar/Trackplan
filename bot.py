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
GRADE_SELECTION, STUDENT_PANEL, ADMIN_PANEL, WAITING_PLAN, PLAN_DAY, PLAN_GRADE, PLAN_SUBJECTS, BROADCAST_MESSAGE, SELECT_DAY, SELECT_SUBJECT = range(10)

# تنظیم لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class StudyBot:
    def __init__(self):
        self.db_connection = self.init_database()
        self.create_tables()
        self.create_default_admin()
    
    def init_database(self):
        """اتصال به دیتابیس"""
        try:
            conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
            logger.info("✅ Connected to database successfully")
            return conn
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            raise
    
    def create_tables(self):
        """ایجاد جداول مورد نیاز"""
        with self.db_connection.cursor() as cursor:
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
            
            # جدول گزارش‌های ۲۰ دقیقه‌ای
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS progress_checks (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER REFERENCES study_sessions(id),
                    check_time TIMESTAMP NOT NULL,
                    student_response VARCHAR(50),
                    response_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # جدول فعالیت‌های روزانه
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_activities (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER REFERENCES students(id),
                    activity_date DATE NOT NULL,
                    total_study_time INTEGER DEFAULT 0,
                    completed_subjects INTEGER DEFAULT 0,
                    total_checks INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            self.db_connection.commit()
            logger.info("✅ Database tables created successfully")
    
    def create_default_admin(self):
        """ایجاد ادمین پیش‌فرض"""
        try:
            with self.db_connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO advisors (telegram_id, full_name, is_admin) 
                    VALUES (%s, %s, %s)
                    ON CONFLICT (telegram_id) DO NOTHING
                """, (6680287530, "مدیر سیستم", True))
                self.db_connection.commit()
                logger.info("✅ Default admin created")
        except Exception as e:
            logger.error(f"❌ Error creating default admin: {e}")
    
    # --- توابع مدیریت دانش‌آموزان ---
    
    def register_student(self, telegram_id: int, full_name: str, grade: str, advisor_id: int = None):
        """ثبت نام دانش‌آموز"""
        with self.db_connection.cursor() as cursor:
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
            self.db_connection.commit()
            return result['id'] if result else None
    
    def get_student(self, telegram_id: int):
        """دریافت اطلاعات دانش‌آموز"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE telegram_id = %s", (telegram_id,))
            return cursor.fetchone()
    
    def get_all_students(self):
        """دریافت تمام دانش‌آموزان"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT * FROM students ORDER BY full_name")
            return cursor.fetchall()
    
    def get_all_students_telegram_ids(self):
        """دریافت تمام آیدی‌های تلگرام دانش‌آموزان"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT telegram_id FROM students")
            return [row['telegram_id'] for row in cursor.fetchall()]
    
    # --- توابع مدیریت مشاوران ---
    
    def register_advisor(self, telegram_id: int, full_name: str, is_admin: bool = False):
        """ثبت نام مشاور"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO advisors (telegram_id, full_name, is_admin)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                is_admin = EXCLUDED.is_admin
                RETURNING id
            """, (telegram_id, full_name, is_admin))
            result = cursor.fetchone()
            self.db_connection.commit()
            return result['id'] if result else None
    
    def get_advisor(self, telegram_id: int):
        """دریافت اطلاعات مشاور"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT * FROM advisors WHERE telegram_id = %s", (telegram_id,))
            return cursor.fetchone()
    
    def is_admin(self, telegram_id: int):
        """بررسی ادمین بودن"""
        advisor = self.get_advisor(telegram_id)
        return advisor and advisor['is_admin']
    
    # --- توابع مدیریت برنامه‌های درسی ---
    
    def create_study_plan(self, day_number: int, grade: str, subjects: List[Dict], created_by: int):
        """ایجاد برنامه درسی"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO study_plans (day_number, grade, subjects, created_by)
                VALUES (%s, %s, %s, %s)
            """, (day_number, grade, json.dumps(subjects), created_by))
            self.db_connection.commit()
            return True
    
    def get_study_plan(self, day_number: int, grade: str):
        """دریافت برنامه درسی"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM study_plans 
                WHERE day_number = %s AND grade = %s
                ORDER BY created_at DESC LIMIT 1
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
            return result
    
    def get_all_plans(self):
        """دریافت تمام برنامه‌ها"""
        with self.db_connection.cursor() as cursor:
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
            return plans

    def get_plans_by_grade(self, grade: str):
        """دریافت برنامه‌های یک پایه خاص"""
        with self.db_connection.cursor() as cursor:
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
            return plans
    
    # --- توابع مدیریت جلسات مطالعه ---
    
    def start_study_session(self, student_id: int, subject_name: str, day_number: int):
        """شروع جلسه مطالعه"""
        start_time = datetime.now(IRAN_TZ)
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO study_sessions 
                (student_id, subject_name, day_number, start_time, status)
                VALUES (%s, %s, %s, %s, 'in_progress')
                RETURNING id
            """, (student_id, subject_name, day_number, start_time))
            result = cursor.fetchone()
            self.db_connection.commit()
            return result['id'] if result else None
    
    def end_study_session(self, session_id: int):
        """پایان جلسه مطالعه"""
        end_time = datetime.now(IRAN_TZ)
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                UPDATE study_sessions 
                SET end_time = %s, 
                    status = 'completed',
                    total_duration = EXTRACT(EPOCH FROM (%s - start_time))/60
                WHERE id = %s
                RETURNING student_id, subject_name, total_duration
            """, (end_time, end_time, session_id))
            result = cursor.fetchone()
            self.db_connection.commit()
            return result
    
    def update_check_time(self, session_id: int):
        """به‌روزرسانی زمان آخرین بررسی"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                UPDATE study_sessions 
                SET check_count = check_count + 1,
                    last_check_time = %s
                WHERE id = %s
            """, (datetime.now(IRAN_TZ), session_id))
            self.db_connection.commit()
    
    def get_active_sessions(self):
        """دریافت جلسات فعال"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT s.full_name, ss.subject_name, ss.start_time, ss.check_count, ss.id
                FROM study_sessions ss
                JOIN students s ON ss.student_id = s.id
                WHERE ss.status = 'in_progress'
                ORDER BY ss.start_time
            """)
            return cursor.fetchall()

    def get_student_active_session(self, student_id: int):
        """دریافت جلسه فعال دانش‌آموز"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM study_sessions 
                WHERE student_id = %s AND status = 'in_progress'
                ORDER BY start_time DESC LIMIT 1
            """, (student_id,))
            return cursor.fetchone()
    
    # --- توابع گزارش‌گیری ---
    
    def get_daily_report(self, student_id: int, day_number: int):
        """گزارش روزانه دانش‌آموز"""
        with self.db_connection.cursor() as cursor:
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
            return cursor.fetchall()
    
    def get_all_students_daily_activity(self, day_number: int):
        """فعالیت تمام دانش‌آموزان در یک روز"""
        with self.db_connection.cursor() as cursor:
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
            return cursor.fetchall()

# ایجاد نمونه ربات
study_bot = StudyBot()

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
        ["🔙 بازگشت به منوی اصلی"]
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

def get_days_keyboard(grade: str):
    """ایجاد دکمه‌های روزهای موجود برای یک پایه"""
    plans = study_bot.get_plans_by_grade(grade)
    
    if not plans:
        return None
    
    keyboard = []
    for plan in plans:
        keyboard.append([f"📅 روز {plan['day_number']}"])
    
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_subjects_keyboard(day_number: int, grade: str):
    """ایجاد دکمه‌های دروس یک روز خاص"""
    plan = study_bot.get_study_plan(day_number, grade)
    
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
    
    # متن برنامه درسی امروز
    study_plan_text = (
        "🎯 برنامه درسی امروز:\n\n"
        "✅ زیست۱۰: تست (سرخرگ و مویرگ)\n"
        "✅ شیمی۱۲: تست (محاسبات درجه یونش و ثابت تعادل)\n"
        "✅ فیزیک۱۲: تست (مسائل فرمولی حرکت با شتاب ثابت)\n"
        "📚 زیست۱۲: مراحل ترجمه و مقدار پروتئین‌سازی\n"
        "📐 ریاضی۱۲: اتحاد و نسبت مثلثاتی و رادیان\n"
        "📘 فیزیک۱۰: پایستگی انرژی و انرژی درونی\n\n"
        "🤖 به ربات مدیریت مطالعه خوش آمدید!\n"
        "برای شروع از گزینه‌های زیر استفاده کنید:"
    )
    
    # بررسی ادمین بودن
    if study_bot.is_admin(user.id):
        await update.message.reply_text(
            study_plan_text,
            reply_markup=get_admin_panel_keyboard()
        )
        return ADMIN_PANEL
    
    # برای کاربران عادی
    await update.message.reply_text(
        study_plan_text,
        reply_markup=get_main_menu_keyboard()
    )
    return GRADE_SELECTION

async def show_all_study_plans_to_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش برنامه‌های درسی به دانش‌آموز"""
    plans = study_bot.get_all_plans()
    
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
            f"📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        plans_text,
        reply_markup=get_main_menu_keyboard()
    )

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
        student = study_bot.get_student(update.effective_user.id)
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
        
        # ثبت نام دانش‌آموز
        student_id = study_bot.register_student(
            telegram_id=user.id,
            full_name=user.full_name,
            grade=grade
        )
        
        context.user_data['grade'] = grade
        context.user_data['student_id'] = student_id
        
        # نمایش روزهای موجود برای این پایه
        days_keyboard = get_days_keyboard(grade)
        if not days_keyboard:
            await update.message.reply_text(
                f"❌ برای پایه {grade} هیچ برنامه‌ای موجود نیست.\n"
                f"لطفاً با مدیر سیستم تماس بگیرید.",
                reply_markup=get_main_menu_keyboard()
            )
            return GRADE_SELECTION
        
        await update.message.reply_text(
            f"✅ ثبت‌نام شما در پایه {grade} با موفقیت انجام شد!\n\n"
            f"📅 لطفاً روز مورد نظر را انتخاب کنید:",
            reply_markup=days_keyboard
        )
        return SELECT_DAY
    
    await update.message.reply_text(
        "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=get_grade_selection_keyboard()
    )

async def handle_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب روز"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "🎓 لطفاً پایه تحصیلی خود را انتخاب کنید:",
            reply_markup=get_grade_selection_keyboard()
        )
        return GRADE_SELECTION
    
    if text.startswith("📅 روز "):
        try:
            day_number = int(text.replace("📅 روز ", ""))
            user_data['selected_day'] = day_number
            
            # نمایش دروس این روز
            subjects_keyboard = get_subjects_keyboard(day_number, grade)
            if not subjects_keyboard:
                await update.message.reply_text(
                    f"❌ برای روز {day_number} هیچ درسی تعریف نشده است.",
                    reply_markup=get_days_keyboard(grade)
                )
                return SELECT_DAY
            
            await update.message.reply_text(
                f"📘 برنامه روز {day_number} - {grade}\n\n"
                f"📚 لطفاً درس مورد نظر را انتخاب کنید:",
                reply_markup=subjects_keyboard
            )
            return SELECT_SUBJECT
            
        except ValueError:
            await update.message.reply_text(
                "❌ خطا در انتخاب روز. لطفاً مجدداً تلاش کنید.",
                reply_markup=get_days_keyboard(grade)
            )
    
    await update.message.reply_text(
        "لطفاً یکی از روزها را انتخاب کنید:",
        reply_markup=get_days_keyboard(grade)
    )

async def handle_subject_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب درس"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    day_number = user_data.get('selected_day')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            f"📅 لطفاً روز مورد نظر را انتخاب کنید:",
            reply_markup=get_days_keyboard(grade)
        )
        return SELECT_DAY
    
    if text.startswith("📚 "):
        subject_name = text.replace("📚 ", "")
        
        # پایان دادن به جلسه فعال قبلی (اگر وجود دارد)
        active_session = study_bot.get_student_active_session(user_data['student_id'])
        if active_session:
            study_bot.end_study_session(active_session['id'])
            # حذف job چک‌های ۲۰ دقیقه‌ای قبلی
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{active_session['id']}"]
                for job in current_jobs:
                    job.schedule_removal()
        
        # شروع جلسه مطالعه جدید
        session_id = study_bot.start_study_session(
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
            f"🕐 زمان شروع: {start_time}\n"
            f"⏰ هر ۲۰ دقیقه وضعیت شما چک می‌شود...\n\n"
            f"✅ پس از اتمام مطالعه، دکمه پایان را بزنید.\n"
            f"🔄 برای تغییر درس، دکمه تغییر درس را بزنید.",
            reply_markup=get_study_management_keyboard()
        )
        
        # برنامه‌ریزی برای چک‌های ۲۰ دقیقه‌ای
        if 'check_jobs' not in context.chat_data:
            context.chat_data['check_jobs'] = []
        
        job = context.job_queue.run_repeating(
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
        reply_markup=get_subjects_keyboard(day_number, grade)
    )

async def handle_student_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پنل دانش‌آموز"""
    text = update.message.text
    user_data = context.user_data
    grade = user_data.get('grade')
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "منوی اصلی:",
            reply_markup=get_main_menu_keyboard()
        )
        return GRADE_SELECTION
    
    elif text == "🎯 شروع مطالعه جدید":
        # بازگشت به انتخاب روز
        days_keyboard = get_days_keyboard(grade)
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
            study_bot.end_study_session(session_id)
            # حذف job چک‌های ۲۰ دقیقه‌ای
            if 'check_jobs' in context.chat_data:
                current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{session_id}"]
                for job in current_jobs:
                    job.schedule_removal()
        
        day_number = user_data.get('selected_day')
        subjects_keyboard = get_subjects_keyboard(day_number, grade)
        if subjects_keyboard:
            await update.message.reply_text(
                f"📚 لطفاً درس جدید را انتخاب کنید:",
                reply_markup=subjects_keyboard
            )
            return SELECT_SUBJECT
        else:
            await update.message.reply_text(
                "❌ خطا در تغییر درس. لطفاً مجدداً تلاش کنید.",
                reply_markup=get_study_management_keyboard()
            )
    
    elif text == "✅ پایان مطالعه":
        await end_study_session_handler(update, context)
    
    elif text == "📊 گزارش روزانه":
        await show_student_report(update, context)
    
    elif text == "📊 بازگشت به پنل":
        await update.message.reply_text(
            "پنل دانش‌آموز:",
            reply_markup=get_student_panel_keyboard()
        )

async def end_study_session_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پایان جلسه مطالعه"""
    user_data = context.user_data
    session_id = user_data.get('current_session')
    subject_name = user_data.get('current_subject')
    
    if not session_id:
        await update.message.reply_text(
            "❌ جلسه مطالعه فعالی ندارید.",
            reply_markup=get_student_panel_keyboard()
        )
        return
    
    # پایان جلسه در دیتابیس
    result = study_bot.end_study_session(session_id)
    
    # حذف job چک‌های ۲۰ دقیقه‌ای
    if 'check_jobs' in context.chat_data:
        current_jobs = [job for job in context.chat_data['check_jobs'] if job.name == f"check_{session_id}"]
        for job in current_jobs:
            job.schedule_removal()
    
    end_time = datetime.now(IRAN_TZ).strftime("%H:%M")
    duration = result['total_duration'] if result else 0
    
    await update.message.reply_text(
        f"🎉 مطالعه '{subject_name}' با موفقیت به پایان رسید!\n"
        f"🕐 زمان پایان: {end_time}\n"
        f"⏱ مدت مطالعه: {duration:.0f} دقیقه\n\n"
        f"📊 می‌توانید گزارش کامل را مشاهده کنید.",
        reply_markup=get_student_panel_keyboard()
    )
    
    # پاک کردن session جاری
    user_data.pop('current_session', None)
    user_data.pop('current_subject', None)

async def progress_check(context: ContextTypes.DEFAULT_TYPE):
    """بررسی پیشرفت هر ۲۰ دقیقه"""
    job = context.job
    session_id = job.data
    
    # به‌روزرسانی زمان بررسی
    study_bot.update_check_time(session_id)
    
    await context.bot.send_message(
        chat_id=job.chat_id,
        text="🔍 بررسی ۲۰ دقیقه‌ای\nوضعیت مطالعه شما چگونه است؟",
        reply_markup=get_progress_check_keyboard()
    )

async def handle_progress_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پاسخ به بررسی ۲۰ دقیقه‌ای"""
    text = update.message.text
    
    responses = {
        "✅ در حال پیشرفت": "در حال پیشرفت",
        "⚠️ مشکل دارم": "مشکل دارم", 
        "❌ متوقف کردم": "متوقف کردم"
    }
    
    response_text = responses.get(text, "نامشخص")
    
    await update.message.reply_text(
        f"📝 وضعیت شما ثبت شد: {response_text}\n"
        f"✅ به مطالعه ادامه دهید...",
        reply_markup=get_study_management_keyboard()
    )

async def show_student_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش روزانه دانش‌آموز"""
    user_data = context.user_data
    student_id = user_data.get('student_id')
    
    if not student_id:
        await update.message.reply_text(
            "❌ ابتدا باید ثبت‌نام کنید.",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    report = study_bot.get_daily_report(student_id, day_number=1)
    
    if not report:
        await update.message.reply_text(
            "📊 هیچ فعالیتی برای امروز ثبت نشده است.",
            reply_markup=get_student_panel_keyboard()
        )
        return
    
    report_text = "📊 گزارش فعالیت‌های امروز\n\n"
    total_duration = 0
    completed_subjects = 0
    
    for activity in report:
        start_time = activity['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        end_time = activity['end_time'].astimezone(IRAN_TZ).strftime("%H:%M") if activity['end_time'] else "در حال مطالعه"
        duration = activity['total_duration'] or 0
        
        status_icon = "✅" if activity['status'] == 'completed' else "🟡"
        
        report_text += (
            f"{status_icon} {activity['subject_name']}\n"
            f"🕐 {start_time} - {end_time}\n"
            f"⏱ {duration:.0f} دقیقه | 🔍 {activity['check_count']} بار بررسی\n\n"
        )
        
        total_duration += duration
        if activity['status'] == 'completed':
            completed_subjects += 1
    
    report_text += f"📈 جمع‌بندی:\n"
    report_text += f"✅ دروس تکمیل شده: {completed_subjects}\n"
    report_text += f"⏱ کل زمان مطالعه: {total_duration:.0f} دقیقه\n"
    report_text += f"📚 میانگین زمان هر درس: {total_duration/len(report) if report else 0:.1f} دقیقه"
    
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
            "📝 لطفاً شماره روز برنامه را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        context.user_data['plan_creation'] = {'step': 'day'}
        return PLAN_DAY
    
    elif text == "📝 مشاهده برنامه‌ها":
        await show_all_plans(update, context)
    
    elif text == "👥 مشاهده دانش‌آموزان":
        await show_students_list(update, context)
    
    elif text == "📊 گزارش کلی":
        await show_daily_overview(update, context)
    
    elif text == "🔍 جلسات فعال":
        await show_active_sessions(update, context)
    
    elif text == "📢 ارسال پیام همگانی":
        await update.message.reply_text(
            "📢 لطفاً پیام همگانی خود را وارد کنید:",
            reply_markup=get_broadcast_keyboard()
        )
        return BROADCAST_MESSAGE

async def show_students_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست دانش‌آموزان"""
    students = study_bot.get_all_students()
    
    if not students:
        await update.message.reply_text(
            "👥 هیچ دانش‌آموزی ثبت‌نام نکرده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    report_text = "👥 لیست دانش‌آموزان\n\n"
    
    for i, student in enumerate(students, 1):
        report_text += f"{i}. {student['full_name']} - {student['grade']}\n"
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_daily_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گزارش کلی روزانه برای ادمین"""
    activities = study_bot.get_all_students_daily_activity(day_number=1)
    
    if not activities:
        await update.message.reply_text(
            "📊 هیچ فعالیتی برای امروز ثبت نشده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    report_text = "📊 گزارش کلی فعالیت‌های امروز\n\n"
    current_student = None
    student_count = 0
    
    for activity in activities:
        if activity['full_name'] != current_student:
            current_student = activity['full_name']
            student_count += 1
            report_text += f"\n👤 {current_student} ({activity['grade']})\n"
        
        if activity['subject_name']:
            start_time = activity['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
            status_icon = "✅" if activity['status'] == 'completed' else "🟡"
            duration = activity['total_duration'] or 0
            
            report_text += (
                f"{status_icon} {activity['subject_name']} | "
                f"🕐 {start_time} | "
                f"⏱ {duration:.0f}دقیقه | "
                f"🔍 {activity['check_count']} بار\n"
            )
        else:
            report_text += "❌ هیچ فعالیتی ثبت نشده\n"
    
    report_text += f"\n📈 جمع‌بندی:\n"
    report_text += f"👥 تعداد دانش‌آموزان: {student_count}\n"
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جلسات فعال"""
    active_sessions = study_bot.get_active_sessions()
    
    if not active_sessions:
        await update.message.reply_text(
            "🔍 هیچ جلسه فعالی در حال حاضر وجود ندارد.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    report_text = "🔍 جلسات فعال در حال حاضر\n\n"
    
    for session in active_sessions:
        start_time = session['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        duration = (datetime.now(IRAN_TZ) - session['start_time']).total_seconds() / 60
        
        report_text += (
            f"👤 {session['full_name']}\n"
            f"📚 {session['subject_name']}\n"
            f"🕐 شروع: {start_time} | "
            f"⏱ مدت: {duration:.0f} دقیقه | "
            f"🔍 {session['check_count']} بار بررسی\n\n"
        )
    
    await update.message.reply_text(
        report_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def show_all_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش تمام برنامه‌ها"""
    plans = study_bot.get_all_plans()
    
    if not plans:
        await update.message.reply_text(
            "📝 هیچ برنامه‌ای ایجاد نشده است.",
            reply_markup=get_admin_panel_keyboard()
        )
        return
    
    plans_text = "📚 برنامه‌های درسی موجود:\n\n"
    
    for plan in plans:
        plans_text += (
            f"📘 روز {plan['day_number']} - {plan['grade']}\n"
            f"📚 دروس: {', '.join([s['name'] for s in plan['subjects']])}\n"
            f"👤 ایجاد شده توسط: {plan['creator_name'] or 'سیستم'}\n"
            f"────────────────────\n"
        )
    
    await update.message.reply_text(
        plans_text,
        reply_markup=get_admin_panel_keyboard()
    )

async def handle_plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ورود شماره روز برنامه"""
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
        
        context.user_data['plan_creation']['day_number'] = day_number
        context.user_data['plan_creation']['step'] = 'grade'
        
        await update.message.reply_text(
            "🎓 لطفاً پایه تحصیلی را انتخاب کنید:",
            reply_markup=get_plan_grade_keyboard()
        )
        return PLAN_GRADE
    
    except ValueError:
        await update.message.reply_text(
            "❌ لطفاً یک عدد معتبر برای روز وارد کنید:",
            reply_markup=get_back_keyboard()
        )

async def handle_plan_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب پایه برنامه"""
    text = update.message.text
    
    if text == "🔙 بازگشت":
        await update.message.reply_text(
            "📝 لطفاً شماره روز برنامه را وارد کنید:",
            reply_markup=get_back_keyboard()
        )
        return PLAN_DAY
    
    if text in ["🎓 دوازدهم تجربی", "📊 دوازدهم ریاضی"]:
        grade = "دوازدهم تجربی" if "تجربی" in text else "دوازدهم ریاضی"
        
        context.user_data['plan_creation']['grade'] = grade
        context.user_data['plan_creation']['step'] = 'subjects'
        context.user_data['plan_creation']['subjects'] = []
        
        await update.message.reply_text(
            "📚 لطفاً نام درس اول را وارد کنید:",
            reply_markup=get_plan_subjects_keyboard()
        )
        return PLAN_SUBJECTS
    
    await update.message.reply_text(
        "لطفاً یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=get_plan_grade_keyboard()
    )

async def handle_plan_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت ورود دروس برنامه"""
    text = update.message.text
    plan_data = context.user_data['plan_creation']
    subjects = plan_data['subjects']
    
    if text == "🔙 بازگشت":
        if subjects:
            subjects.pop()
            await update.message.reply_text(
                f"آخرین درس حذف شد. درس قبلی: {subjects[-1]['name'] if subjects else 'هیچ'}\n"
                f"لطفاً درس بعدی را وارد کنید یا 'پایان' را بزنید:",
                reply_markup=get_plan_subjects_keyboard()
            )
        else:
            await update.message.reply_text(
                "🎓 لطفاً پایه تحصیلی را انتخاب کنید:",
                reply_markup=get_plan_grade_keyboard()
            )
            return PLAN_GRADE
    
    elif text == "پایان":
        if not subjects:
            await update.message.reply_text(
                "❌ حداقل یک درس باید اضافه کنید.",
                reply_markup=get_plan_subjects_keyboard()
            )
            return
        
        # ذخیره برنامه در دیتابیس
        advisor = study_bot.get_advisor(update.effective_user.id)
        success = study_bot.create_study_plan(
            day_number=plan_data['day_number'],
            grade=plan_data['grade'],
            subjects=subjects,
            created_by=advisor['id']
        )
        
        if success:
            await update.message.reply_text(
                f"✅ برنامه روز {plan_data['day_number']} برای پایه {plan_data['grade']} با موفقیت ایجاد شد!\n"
                f"📚 دروس: {', '.join([s['name'] for s in subjects])}",
                reply_markup=get_admin_panel_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ خطا در ایجاد برنامه.",
                reply_markup=get_admin_panel_keyboard()
            )
        
        context.user_data.pop('plan_creation', None)
        return ADMIN_PANEL
    
    else:
        # اضافه کردن درس جدید
        subject_data = {
            'name': text,
            'duration': 60  # مدت پیش‌فرض
        }
        subjects.append(subject_data)
        
        await update.message.reply_text(
            f"✅ درس '{text}' اضافه شد.\n"
            f"تعداد دروس: {len(subjects)}\n\n"
            f"لطفاً درس بعدی را وارد کنید یا 'پایان' را بزنید:",
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
    student_ids = study_bot.get_all_students_telegram_ids()
    sent_count = 0
    
    for student_id in student_ids:
        try:
            await context.bot.send_message(
                chat_id=student_id,
                text=f"📢 پیام همگانی از مدیریت:\n\n{text}"
            )
            sent_count += 1
            await asyncio.sleep(0.1)  # جلوگیری از محدودیت تلگرام
        except Exception as e:
            logger.error(f"Failed to send broadcast to {student_id}: {e}")
    
    await update.message.reply_text(
        f"✅ پیام همگانی به {sent_count} دانش‌آموز ارسال شد.",
        reply_markup=get_admin_panel_keyboard()
    )
    return ADMIN_PANEL

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاها"""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ خطایی رخ داده است. لطفاً دوباره تلاش کنید.",
            reply_markup=get_main_menu_keyboard()
        )

def main():
    """تابع اصلی اجرای ربات"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation Handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            GRADE_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_grade_selection)
            ],
            STUDENT_PANEL: [
                MessageHandler(filters.Regex(r"^(✅ پایان مطالعه|🔄 تغییر درس|📊 بازگشت به پنل|📊 گزارش روزانه|🎯 شروع مطالعه جدید|🔙 بازگشت)$"), handle_student_panel),
                MessageHandler(filters.Regex(r"^(✅ در حال پیشرفت|⚠️ مشکل دارم|❌ متوقف کردم)$"), handle_progress_response),
            ],
            ADMIN_PANEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_panel)
            ],
            SELECT_DAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_day_selection)
            ],
            SELECT_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subject_selection)
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
            ]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    # اجرای ربات
    logger.info("🤖 Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
