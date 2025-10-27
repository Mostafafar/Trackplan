import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, JobQueue
)
from pytz import timezone

# تنظیمات
TELEGRAM_TOKEN = "8493311862:AAF0k6E2LHOImAhTdxXMVRxtD4eSI4k_e8Y"
DATABASE_URL = "YOUR_DATABASE_URL"

# تنظیمات زمان ایران
IRAN_TZ = timezone('Asia/Tehran')

# تنظیم لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class StudyBot:
    def __init__(self):
        self.db_connection = self.init_database()
        self.create_tables()
    
    def init_database(self):
        """اتصال به دیتابیس"""
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    
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
                    total_duration INTEGER, -- مدت زمان به دقیقه
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
            
            self.db_connection.commit()
    
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
            return cursor.fetchone()['id']
    
    def get_student(self, telegram_id: int):
        """دریافت اطلاعات دانش‌آموز"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE telegram_id = %s", (telegram_id,))
            return cursor.fetchone()
    
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
            return cursor.fetchone()['id']
    
    def get_advisor(self, telegram_id: int):
        """دریافت اطلاعات مشاور"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("SELECT * FROM advisors WHERE telegram_id = %s", (telegram_id,))
            return cursor.fetchone()
    
    # --- توابع مدیریت برنامه‌های درسی ---
    
    def create_study_plan(self, day_number: int, grade: str, subjects: List[Dict], created_by: int):
        """ایجاد برنامه درسی"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO study_plans (day_number, grade, subjects, created_by)
                VALUES (%s, %s, %s, %s)
            """, (day_number, grade, subjects, created_by))
            self.db_connection.commit()
    
    def get_study_plan(self, day_number: int, grade: str):
        """دریافت برنامه درسی"""
        with self.db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM study_plans 
                WHERE day_number = %s AND grade = %s
                ORDER BY created_at DESC LIMIT 1
            """, (day_number, grade))
            return cursor.fetchone()
    
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
            session_id = cursor.fetchone()['id']
            self.db_connection.commit()
            return session_id
    
    def end_study_session(self, session_id: int):
        """پایان جلسه مطالعه"""
        end_time = datetime.now(IRAN_TZ)
        with self.db_connection.cursor() as cursor:
            # محاسبه مدت زمان
            cursor.execute("""
                UPDATE study_sessions 
                SET end_time = %s, 
                    status = 'completed',
                    total_duration = EXTRACT(EPOCH FROM (%s - start_time))/60
                WHERE id = %s
            """, (end_time, end_time, session_id))
            self.db_connection.commit()
    
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
                    ss.check_count
                FROM students s
                LEFT JOIN study_sessions ss ON s.id = ss.student_id AND ss.day_number = %s
                ORDER BY s.full_name, ss.start_time
            """, (day_number,))
            return cursor.fetchall()

# ایجاد نمونه ربات
study_bot = StudyBot()

# --- handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ربات"""
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("🎓 دوازدهم تجربی", callback_data="grade_12_exp")],
        [InlineKeyboardButton("📊 دوازدهم ریاضی", callback_data="grade_12_math")],
        [InlineKeyboardButton("👨‍🏫 من مشاور هستم", callback_data="advisor_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"سلام {user.first_name}!\n"
        "لطفاً پایه تحصیلی خود را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def handle_grade_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب پایه تحصیلی"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    grade = "دوازدهم تجربی" if "exp" in query.data else "دوازدهم ریاضی"
    
    # ثبت نام دانش‌آموز
    student_id = study_bot.register_student(
        telegram_id=user.id,
        full_name=user.full_name,
        grade=grade
    )
    
    context.user_data['grade'] = grade
    context.user_data['student_id'] = student_id
    
    # نمایش برنامه روز جاری
    await show_daily_plan(update, context)

async def show_daily_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش برنامه روزانه"""
    query = update.callback_query
    user_data = context.user_data
    
    # دریافت برنامه روز جاری (می‌توانید منطق تعیین روز را اضافه کنید)
    current_day = 1  # برای نمونه
    grade = user_data.get('grade')
    
    plan = study_bot.get_study_plan(current_day, grade)
    
    if not plan:
        await query.edit_message_text("📝 برنامه‌ای برای امروز تعریف نشده است.")
        return
    
    # ایجاد دکمه‌های دروس
    keyboard = []
    subjects = plan['subjects']
    
    for subject in subjects:
        keyboard.append([
            InlineKeyboardButton(
                f"📚 {subject['name']}", 
                callback_data=f"start_subject_{subject['name']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("📊 گزارش روزانه", callback_data="daily_report")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    plan_text = f"📘 برنامه روز {current_day} - {grade}\n\n"
    for subject in subjects:
        plan_text += f"📚 {subject['name']}\n"
    
    await query.edit_message_text(
        plan_text + "\n✅ برای شروع مطالعه روی درس مورد نظر کلیک کنید:",
        reply_markup=reply_markup
    )

async def start_subject_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع مطالعه یک درس"""
    query = update.callback_query
    await query.answer()
    
    subject_name = query.data.replace("start_subject_", "")
    user_data = context.user_data
    
    # شروع جلسه مطالعه
    session_id = study_bot.start_study_session(
        student_id=user_data['student_id'],
        subject_name=subject_name,
        day_number=1  # برای نمونه
    )
    
    user_data['current_session'] = session_id
    user_data['current_subject'] = subject_name
    start_time = datetime.now(IRAN_TZ).strftime("%H:%M")
    
    # ایجاد دکمه‌های مدیریت مطالعه
    keyboard = [
        [InlineKeyboardButton("✅ پایان مطالعه", callback_data="end_study")],
        [InlineKeyboardButton("⏸ توقف موقت", callback_data="pause_study")],
        [InlineKeyboardButton("📊 بازگشت به برنامه", callback_data="back_to_plan")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🎯 مطالعه '{subject_name}' شروع شد!\n"
        f"🕐 زمان شروع: {start_time}\n"
        f"⏰ هر ۲۰ دقیقه وضعیت شما چک می‌شود...\n\n"
        f"✅ پس از اتمام مطالعه، دکمه پایان را بزنید.",
        reply_markup=reply_markup
    )
    
    # برنامه‌ریزی برای چک‌های ۲۰ دقیقه‌ای
    context.job_queue.run_repeating(
        progress_check,
        interval=1200,  # 20 دقیقه
        first=1200,
        chat_id=query.message.chat_id,
        name=f"check_{session_id}",
        data=session_id
    )

async def progress_check(context: ContextTypes.DEFAULT_TYPE):
    """بررسی پیشرفت هر ۲۰ دقیقه"""
    job = context.job
    session_id = job.data
    
    # به‌روزرسانی زمان بررسی
    study_bot.update_check_time(session_id)
    
    keyboard = [
        [
            InlineKeyboardButton("✅ در حال پیشرفت", callback_data=f"check_ok_{session_id}"),
            InlineKeyboardButton("⚠️ مشکل دارم", callback_data=f"check_problem_{session_id}")
        ],
        [InlineKeyboardButton("❌ متوقف کردم", callback_data=f"check_stopped_{session_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=job.chat_id,
        text="🔍 بررسی ۲۰ دقیقه‌ای\nوضعیت مطالعه شما چگونه است؟",
        reply_markup=reply_markup
    )

async def handle_progress_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پاسخ به بررسی ۲۰ دقیقه‌ای"""
    query = update.callback_query
    await query.answer()
    
    response_data = query.data
    session_id = int(response_data.split("_")[-1])
    response_type = response_data.split("_")[1]
    
    responses = {
        "ok": "در حال پیشرفت",
        "problem": "مشکل دارم", 
        "stopped": "متوقف کردم"
    }
    
    await query.edit_message_text(
        f"📝 وضعیت شما ثبت شد: {responses[response_type]}\n"
        f"✅ به مطالعه ادامه دهید..."
    )
    
    # در اینجا می‌توانید پاسخ را در دیتابیس ذخیره کنید

async def end_study_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پایان جلسه مطالعه"""
    query = update.callback_query
    await query.answer()
    
    user_data = context.user_data
    session_id = user_data.get('current_session')
    subject_name = user_data.get('current_subject')
    
    if session_id:
        # پایان جلسه در دیتابیس
        study_bot.end_study_session(session_id)
        
        # حذف job چک‌های ۲۰ دقیقه‌ای
        current_jobs = context.job_queue.get_jobs_by_name(f"check_{session_id}")
        for job in current_jobs:
            job.schedule_removal()
        
        end_time = datetime.now(IRAN_TZ).strftime("%H:%M")
        
        await query.edit_message_text(
            f"🎉 مطالعه '{subject_name}' با موفقیت به پایان رسید!\n"
            f"🕐 زمان پایان: {end_time}\n\n"
            f"📊 می‌توانید گزارش کامل را مشاهده کنید."
        )
        
        # پاک کردن session جاری
        user_data.pop('current_session', None)
        user_data.pop('current_subject', None)

async def show_daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش روزانه"""
    query = update.callback_query
    await query.answer()
    
    user_data = context.user_data
    student_id = user_data.get('student_id')
    
    report = study_bot.get_daily_report(student_id, day_number=1)
    
    if not report:
        await query.edit_message_text("📊 هیچ فعالیتی برای امروز ثبت نشده است.")
        return
    
    report_text = "📊 گزارش فعالیت‌های امروز\n\n"
    total_duration = 0
    
    for activity in report:
        start_time = activity['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
        end_time = activity['end_time'].astimezone(IRAN_TZ).strftime("%H:%M") if activity['end_time'] else "در حال مطالعه"
        duration = activity['total_duration'] or 0
        
        report_text += (
            f"📚 {activity['subject_name']}\n"
            f"🕐 {start_time} - {end_time}\n"
            f"⏱ {duration} دقیقه | 🔍 {activity['check_count']} بار بررسی\n"
            f"📊 وضعیت: {activity['status']}\n\n"
        )
        
        total_duration += duration
    
    report_text += f"✅ مجموع زمان مطالعه: {total_duration} دقیقه"
    
    keyboard = [[InlineKeyboardButton("🔙 بازگشت به برنامه", callback_data="back_to_plan")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(report_text, reply_markup=reply_markup)

async def advisor_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پنل مشاور"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    # ثبت/بررسی مشاور
    advisor = study_bot.get_advisor(user.id)
    if not advisor:
        study_bot.register_advisor(user.id, user.full_name, is_admin=True)
    
    keyboard = [
        [InlineKeyboardButton("👥 مشاهده دانش‌آموزان", callback_data="view_students")],
        [InlineKeyboardButton("📊 گزارش روزانه کلی", callback_data="daily_overview")],
        [InlineKeyboardButton("📋 ارسال برنامه جدید", callback_data="send_new_plan")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👨‍🏫 پنل مدیریت مشاور\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def daily_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گزارش کلی روزانه برای مشاور"""
    query = update.callback_query
    await query.answer()
    
    activities = study_bot.get_all_students_daily_activity(day_number=1)
    
    if not activities:
        await query.edit_message_text("📊 هیچ فعالیتی برای امروز ثبت نشده است.")
        return
    
    report_text = "📊 گزارش کلی فعالیت‌های امروز\n\n"
    current_student = None
    
    for activity in activities:
        if activity['full_name'] != current_student:
            current_student = activity['full_name']
            report_text += f"\n👤 {current_student} ({activity['grade']})\n"
        
        if activity['subject_name']:
            start_time = activity['start_time'].astimezone(IRAN_TZ).strftime("%H:%M")
            status_icon = "✅" if activity['status'] == 'completed' else "🟡"
            
            report_text += (
                f"{status_icon} {activity['subject_name']} | "
                f"🕐 {start_time} | "
                f"🔍 {activity['check_count']} بار\n"
            )
    
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="advisor_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(report_text, reply_markup=reply_markup)

def main():
    """تابع اصلی اجرای ربات"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_grade_selection, pattern="^grade_"))
    application.add_handler(CallbackQueryHandler(start_subject_study, pattern="^start_subject_"))
    application.add_handler(CallbackQueryHandler(end_study_session, pattern="^end_study"))
    application.add_handler(CallbackQueryHandler(handle_progress_response, pattern="^check_"))
    application.add_handler(CallbackQueryHandler(show_daily_report, pattern="^daily_report"))
    application.add_handler(CallbackQueryHandler(advisor_panel, pattern="^advisor_panel"))
    application.add_handler(CallbackQueryHandler(daily_overview, pattern="^daily_overview"))
    application.add_handler(CallbackQueryHandler(show_daily_plan, pattern="^back_to_plan"))
    
    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()
