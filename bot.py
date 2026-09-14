import os
import sys
import sqlite3
import random
import string
import logging
import asyncio
import re
import json
import time
import urllib.request
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ----------------------------------------------------
# 1. إعدادات التسجيل والبيئة
# ----------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8960246242:AAG5oFS5zMt1u4xRrxeOTcHyLm02wkR6SM8")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

# ----------------------------------------------------
# 2. إعداد قاعدة البيانات الموحدة (database.db)
# ----------------------------------------------------
DB_NAME = "database.db"

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def is_maintenance_active() -> bool:
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='maintenance_mode'").fetchone()
        conn.close()
        return row is not None and row["value"] == "1"
    except Exception:
        return False

def is_admin_user(user_id: int) -> bool:
    if user_id == DEFAULT_ADMIN_ID:
        return True
    try:
        conn = get_db()
        row = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            balance REAL DEFAULT 0.0,
            referred_by INTEGER,
            referrals_count INTEGER DEFAULT 0,
            active_referrals_count INTEGER DEFAULT 0,
            referral_mode TEXT DEFAULT NULL,
            free_spins INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0.0,
            is_verified INTEGER DEFAULT 0,
            phone_verified INTEGER DEFAULT 0,
            welcome_bonus_claimed INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            captcha_answer TEXT DEFAULT '',
            step TEXT DEFAULT 'start',
            custom_boost REAL DEFAULT 0.0
        )
    ''')

    columns_to_add = [
        ("full_name", "TEXT"),
        ("phone", "TEXT"),
        ("balance", "REAL DEFAULT 0.0"),
        ("referred_by", "INTEGER"),
        ("referrals_count", "INTEGER DEFAULT 0"),
        ("active_referrals_count", "INTEGER DEFAULT 0"),
        ("referral_mode", "TEXT DEFAULT NULL"),
        ("free_spins", "INTEGER DEFAULT 0"),
        ("games_played", "INTEGER DEFAULT 0"),
        ("total_spent", "REAL DEFAULT 0.0"),
        ("is_verified", "INTEGER DEFAULT 0"),
        ("phone_verified", "INTEGER DEFAULT 0"),
        ("welcome_bonus_claimed", "INTEGER DEFAULT 0"),
        ("is_banned", "INTEGER DEFAULT 0"),
        ("captcha_answer", "TEXT DEFAULT ''"),
        ("step", "TEXT DEFAULT 'start'"),
        ("custom_boost", "REAL DEFAULT 0.0")
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    cursor.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, amount REAL, uses_left INTEGER)')
    cursor.execute('CREATE TABLE IF NOT EXISTS channels (channel_id TEXT PRIMARY KEY, channel_title TEXT, channel_link TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS deposit_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, method_name TEXT, account_details TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, amount REAL, tx_id TEXT, photo_file_id TEXT, status TEXT DEFAULT "pending", timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, account_code TEXT, amount REAL, net_amount REAL, status TEXT DEFAULT "pending", timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, amount REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, link TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, image_url TEXT, game_url TEXT, is_active INTEGER DEFAULT 1)')

    if DEFAULT_ADMIN_ID:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
        
    # الإعدادات الافتراضية
    defaults = [
        ('maintenance_mode', '0'),
        ('welcome_bonus', '100'),
        ('welcome_bonus_enabled', '1'),
        ('referral_reward', '50'),
        ('referral_reward_enabled', '1'),
        ('referral_spin_enabled', '1'),
        ('referral_burn_percent', '10'),
        ('deposit_bonus_percent', '0'),
        ('withdraw_commission_percent', '0'),
        ('min_withdraw', '100'),
        ('min_deposit', '50'),
        # خوارزمية العجلة الـ 12
        ('wheel_prob_luck', '25'),
        ('wheel_prob_5', '20'),
        ('wheel_prob_10', '15'),
        ('wheel_prob_15', '10'),
        ('wheel_prob_try_again', '15'),
        ('wheel_prob_25', '7'),
        ('wheel_prob_50', '4'),
        ('wheel_prob_100', '2'),
        ('wheel_prob_250', '1'),
        ('wheel_prob_dep_bonus_20', '0.8'),
        ('wheel_prob_500', '0.15'),
        ('wheel_prob_1000', '0.05')
    ]
    for key, val in defaults:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (1, 'شام كاش', '6a257b9a22a0e063c24b38201e93fcab')")
    cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (2, 'سيريتل كاش', '18843131')")

    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 3. إرسال الرسائل والتفاعل العشوائي
# ----------------------------------------------------
async def send_start_reaction(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        reactions = ["🔥", "⚡", "💥", "🎉", "🏆", "👍"]
        selected_emoji = random.choice(reactions)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[{"type": "emoji", "emoji": selected_emoji}]
        )
    except Exception as e:
        logger.warning(f"Reaction failed: {e}")

async def check_user_channels_subscription(bot, user_id: int) -> tuple[bool, list]:
    conn = get_db()
    channels = conn.execute("SELECT channel_id, channel_title, channel_link FROM channels").fetchall()
    conn.close()

    if not channels:
        return True, []

    unsubscribed = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
            if member.status in ['left', 'kicked']:
                unsubscribed.append(ch)
        except Exception:
            unsubscribed.append(ch)

    return (len(unsubscribed) == 0), unsubscribed

def build_sub_keyboard(unsubscribed_channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in unsubscribed_channels:
        title = ch["channel_title"] or "📢 قناة الاشتراك الإجباري"
        keyboard.append([InlineKeyboardButton(f"🔗 {title}", url=ch["channel_link"])])
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الآن", callback_data="check_subscription_status")])
    return InlineKeyboardMarkup(keyboard)

def cancel_keyboard(target="back_to_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية والعودة", callback_data=target)]])

# ----------------------------------------------------
# 4. لوحات التحكم والقوائم
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    wheel_url = f"{SERVER_URL}/wheel"
    keyboard = [
        [InlineKeyboardButton("🎮 دخول صفحة الألعاب | Golden Games 🎰", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("🎡 عجلة الحظ 🎯", web_app=WebAppInfo(url=wheel_url)), InlineKeyboardButton("🎁 العروض الحالية", callback_data="btn_offers")],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="btn_deposit"), InlineKeyboardButton("💸 سحب رصيدي", callback_data="btn_withdraw")],
        [InlineKeyboardButton("👤 حسابي ورصيدي", callback_data="btn_account"), InlineKeyboardButton("🔗 رابط إحالاتي", callback_data="btn_referral")],
        [InlineKeyboardButton("📸 إرسال إصابة / إثبات", callback_data="btn_send_proof"), InlineKeyboardButton("🎁 إدخال كود هدية", callback_data="btn_gift")],
        [InlineKeyboardButton("🤖 شراء بوت", callback_data="btn_buy_bot"), InlineKeyboardButton("📜 سجلاتي", callback_data="btn_logs")],
        [InlineKeyboardButton("💬 مراسلة الدعم", callback_data="btn_support"), InlineKeyboardButton("📢 قناة المبرمج الرسمية", url="https://t.me/lerafree")]
    ]
    if is_admin:
        keyboard.insert(1, [InlineKeyboardButton("⚙️ لوحة الإدارة الشاملة 👮‍♂️", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    maint_status = "🔴 مفعل (البوت مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
    keyboard = [
        [InlineKeyboardButton(f"🛠️ وضع الصيانة: {maint_status}", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🎡 خوارزمية عجلة الحظ", callback_data="adm_wheel_algo"), InlineKeyboardButton("🎯 حظ لاعب معين", callback_data="adm_user_boost")],
        [InlineKeyboardButton("🎁 البونص الترحيبي", callback_data="adm_toggle_welcome"), InlineKeyboardButton("🔗 بونص الإحالة", callback_data="adm_toggle_ref_bonus")],
        [InlineKeyboardButton("🎰 منح لفات مجانية", callback_data="adm_grant_spins_menu"), InlineKeyboardButton("📢 قنوات الاشتراك", callback_data="adm_channels_menu")],
        [InlineKeyboardButton("💳 إدارة حسابات الشحن", callback_data="adm_dep_methods"), InlineKeyboardButton("📥 طلبات الشحن", callback_data="adm_deposits")],
        [InlineKeyboardButton("💸 طلبات السحب", callback_data="adm_withdraws"), InlineKeyboardButton("➕ بونص الشحن (%)", callback_data="adm_set_dep_bonus")],
        [InlineKeyboardButton("➖ عمولة السحب (%)", callback_data="adm_set_w_commission"), InlineKeyboardButton("💰 حد الشحن الأدنى", callback_data="adm_set_min_dep")],
        [InlineKeyboardButton("💸 حد السحب الأدنى", callback_data="adm_set_min_w"), InlineKeyboardButton("➕ إضافة رصيد", callback_data="adm_add_bal")],
        [InlineKeyboardButton("➖ خصم رصيد", callback_data="adm_sub_bal"), InlineKeyboardButton("🎟️ توليد أكواد دفعة", callback_data="adm_batch_codes")],
        [InlineKeyboardButton("🎫 إنشاء كود فردي", callback_data="adm_make_gift"), InlineKeyboardButton("🔍 تفاصيل عميل كاملة", callback_data="adm_user_info")],
        [InlineKeyboardButton("📊 سجل ونقاط اللاعبين", callback_data="adm_players_log"), InlineKeyboardButton("👮 عرض قائمة الأدمنية", callback_data="adm_list_admins")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban")],
        [InlineKeyboardButton("📢 إذاعة سريعة (نص)", callback_data="adm_bc_txt"), InlineKeyboardButton("📸 إذاعة سريعة (صورة)", callback_data="adm_bc_img")],
        [InlineKeyboardButton("📩 رسالة خاصة", callback_data="adm_pm_txt"), InlineKeyboardButton("👮 إضافة أدمن", callback_data="adm_add_admin")],
        [InlineKeyboardButton("❌ إزالة أدمن", callback_data="adm_del_admin"), InlineKeyboardButton("📊 الإحصائيات الشاملة", callback_data="adm_stats")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    conn = get_db()
    admins = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    for adm in admins:
        try:
            await context.bot.send_message(chat_id=adm["user_id"], text=text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            pass

# ----------------------------------------------------
# 5. الأوامر والمعالجات الرئيسية
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    # إرسال التفاعل العشوائي المباشر
    await send_start_reaction(context, chat_id, update.message.message_id)

    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **السيرفر حالياً في حالة صيانة وتحديثات دورية.**\nيرجى المحاولة لاحقاً.")
        return

    is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
    if not is_subscribed:
        await update.message.reply_text("⚠️ **يرجى الاشتراك بالقنوات أولاً لاستخدام البوت:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = is_admin_user(user.id)

    if u and u["is_banned"]:
        await update.message.reply_text("❌ حسابك محظور من استخدام السيرفر.")
        conn.close()
        return

    if not u:
        ref_id = None
        if context.args and context.args[0].isdigit():
            ref_id = int(context.args[0])
            if ref_id == user.id:
                ref_id = None
                
        conn.execute("INSERT INTO users (user_id, full_name, referred_by, step) VALUES (?, ?, ?, 'captcha')",
                     (user.id, user.full_name, ref_id))
        conn.commit()
        conn.close()

        await notify_admins(context, f"🔔 **دخول لاعب جديد:**\n👤 **الاسم:** {user.full_name}\n🆔 **المعرف:** `{user.id}`")

        msg = (
            f"👋 **أهلاً بك يا {user.full_name} في منصة Golden Games!** 🎮\n\n"
            f"🛡️ **اختبار الأمان:**\n"
            f"ما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)\n"
            f"✍️ أرسل إجابتك للبدء:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    conn.close()

    if not u["phone_verified"]:
        if u["step"] == "captcha":
            await update.message.reply_text("⚠️ يرجى الإجابة على سؤال الأمان أولاً (حمصية أم حموية؟).")
            return
        elif u["step"] == "phone":
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("📱 يرجى مشاركة رقمك لمرة واحدة فقط لتأكيد الحساب والحصول على البونص:", reply_markup=btn)
            return

    await send_main_dashboard(chat_id, user.id, user.full_name, is_admin, context)

async def send_main_dashboard(chat_id, user_id, full_name, is_admin, context):
    conn = get_db()
    u = conn.execute("SELECT balance, free_spins FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    bal = u["balance"] if u else 0.0
    spins = u["free_spins"] if u else 0
    text = (
        f"👑 **مرحباً بك في منصة الألعاب Golden Games 2026** 🎮\n"
        f"✨ ─────────────────── ✨\n"
        f"👤 **اللاعب:** {full_name}\n"
        f"🆔 **المعرف (ID):** `{user_id}`\n"
        f"💰 **رصيدك الحالي:** `{bal:,.2f}` NSP\n"
        f"🎡 **اللفات المجانية:** `{spins}` لفة\n"
        f"✨ ─────────────────── ✨\n\n"
        f"👇 اختر اللعبة أو القسم المراد من الأزرار أدناه:"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin))

# ----------------------------------------------------
# 6. معالجة الرقم والتوثيق الموحد لمرة واحدة فقط
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact
    
    if contact.user_id != user.id:
        await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي الخاص بك فقط.")
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()

    # التحقق المباشر: هل تم تأكيد الرقم سابقاً؟
    if u and u["phone_verified"] == 1:
        conn.close()
        await update.message.reply_text("⚠️ لقد قمت بتأكيد رقمك سابقاً! لا يمكن تكرار تأكيد الرقم.", reply_markup=ReplyKeyboardRemove())
        await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)
        return

    # التحقق هل الرقم مستخدم في حساب آخر
    check_phone = conn.execute("SELECT user_id FROM users WHERE phone = ? AND phone_verified = 1", (contact.phone_number,)).fetchone()
    if check_phone:
        conn.close()
        await update.message.reply_text("❌ هذا الرقم موثق ومستعمل سابقاً في حساب آخر!", reply_markup=ReplyKeyboardRemove())
        return

    welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
    welcome_bonus = float(conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]) if welcome_enabled else 0.0

    new_bal = u["balance"] + welcome_bonus
    conn.execute(
        "UPDATE users SET phone = ?, is_verified = 1, phone_verified = 1, welcome_bonus_claimed = 1, balance = ?, step = 'main' WHERE user_id = ?",
        (contact.phone_number, new_bal, user.id)
    )
    
    if welcome_bonus > 0:
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, "بونص ترحيبي عند التأكيد لأول مرة", welcome_bonus))

    # معالجة الإحالة
    if u and u["referred_by"]:
        ref = conn.execute("SELECT * FROM users WHERE user_id = ?", (u["referred_by"],)).fetchone()
        if ref:
            conn.execute("UPDATE users SET active_referrals_count = active_referrals_count + 1, referrals_count = referrals_count + 1 WHERE user_id = ?", (u["referred_by"],))
            
            ref_mode = ref["referral_mode"]
            if ref_mode == "free_spin":
                conn.execute("UPDATE users SET free_spins = free_spins + 1 WHERE user_id = ?", (u["referred_by"],))
                try: await context.bot.send_message(u["referred_by"], f"🎉 انضم لاعب جديد عن طريقك وحصلت على لفة مجانية في العجلة!")
                except: pass
            else:
                ref_reward_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"] == "1"
                ref_reward = float(conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]) if ref_reward_enabled else 0.0
                if ref_reward > 0:
                    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ref_reward, u["referred_by"]))
                    conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (u["referred_by"], f"مكافأة إحالة {user.id}", ref_reward))
                    try: await context.bot.send_message(u["referred_by"], f"🎉 قام {user.full_name} بتأكيد حسابه وحصلت على `{ref_reward}` NSP!")
                    except: pass

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ **تم تأكيد رقم هاتفك وحسابك بنجاح!**\n"
        f"🎁 حصلت على البونص الترحيبي لأول مرة قدره `{welcome_bonus}` NSP.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)

# ----------------------------------------------------
# 7. معالجة الرسائل النصية
# ----------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""

    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **السيرفر في حالة صيانة حالياً.**")
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if not u or u["is_banned"]:
        conn.close()
        return

    step = u["step"]

    # اختبار الكابتشا الموحد
    if step == "captcha":
        if text in ["حموية", "حمويه"]:
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            conn.execute("UPDATE users SET step = 'phone', captcha_answer = 'passed' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("✅ إجابة صحيحة 100%! ابن أصول.\n\n📱 يرجى مشاركة رقم هاتفك للتوثيق والبدء:", reply_markup=btn)
        else:
            conn.close()
            await update.message.reply_text("❌ إجابة خاطئة! السؤال: ما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)")
        return

    if step == "user_upload_proof":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await notify_admins(context, f"📸 **إثبات إصابة/فوز من العميل:**\n👤 {user.full_name} (`{user.id}`)\n📝 التفاصيل: {text}")
        await update.message.reply_text("✅ تم إرسال الإثبات للإدارة بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))
        return

    if step == "deposit_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ أدخل مبلغاً صحيحاً بالأرقام.", reply_markup=cancel_keyboard())
            return

        min_dep = float(conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"])
        if amt < min_dep:
            conn.close()
            await update.message.reply_text(f"❌ الحد الأدنى للشحن هو `{min_dep}` NSP.", reply_markup=cancel_keyboard())
            return

        context.user_data["dep_amount"] = amt
        method_name = context.user_data.get("dep_method", "غير محدد")
        acc_details = context.user_data.get("dep_acc_details", "غير متوفر")

        conn.execute("UPDATE users SET step = 'deposit_step_tx' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()

        msg = (
            f"💳 **طريقة الشحن:** {method_name}\n"
            f"💰 **المبلغ المطلوب:** `{amt}` NSP\n\n"
            f"📌 **حساب التحويل:**\n`{acc_details}`\n\n"
            f"✍️ **الآن أدخل رقم العملية/الإشعار أو أرسل صورة الإيصال:**"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if step == "deposit_step_tx":
        amt = context.user_data.get("dep_amount", 0.0)
        method = context.user_data.get("dep_method", "غير محدد")

        cursor = conn.execute("INSERT INTO deposits (user_id, method, amount, tx_id) VALUES (?, ?, ?, ?)", (user.id, method, amt, text))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        dep_id = cursor.lastrowid
        conn.close()

        await update.message.reply_text("✅ تم تقديم طلب الشحن بنجاح وهو قيد المراجعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_dep_{dep_id}")]])
        await notify_admins(context, f"📥 **طلب شحن جديد (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳الطريقة: {method}\n🔢 الإشعار: `{text}`\n💰 المبلغ: `{amt}` NSP", reply_markup=kb)
        return

    if step == "withdraw_step_code":
        context.user_data["withdraw_code"] = text
        conn.execute("UPDATE users SET step = 'withdraw_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✍️ **أدخل المبلغ المراد سحبه (NSP):**", reply_markup=cancel_keyboard())
        return

    if step == "withdraw_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard())
            return

        min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])
        if amt < min_w or amt > u["balance"]:
            conn.close()
            await update.message.reply_text("❌ المبلغ غير متاح أو أقل من حد السحب الأدنى.", reply_markup=cancel_keyboard())
            return

        comm_pct = float(conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"])
        net_amt = amt * (1 - (comm_pct / 100.0))

        method = context.user_data.get("withdraw_method", "غير محدد")
        acc_code = context.user_data.get("withdraw_code", "غير محدد")

        conn.execute("UPDATE users SET balance = balance - ?, step = 'main' WHERE user_id = ?", (amt, user.id))
        cursor = conn.execute("INSERT INTO withdrawals (user_id, method, account_code, amount, net_amount) VALUES (?, ?, ?, ?, ?)",
                              (user.id, method, acc_code, amt, net_amt))
        conn.commit()
        w_id = cursor.lastrowid
        conn.close()

        await update.message.reply_text("✅ تم تقديم طلب السحب بنجاح وهو قيد المراجعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة", callback_data=f"rej_w_{w_id}")]])
        await notify_admins(context, f"📥 **طلب سحب جديد (# {w_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الحساب: `{acc_code}`\n💰 المبلغ الصافي بعد الخصم: `{net_amt:,.2f}` NSP", reply_markup=kb)
        return

    if step == "input_gift_code":
        g = conn.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if not g or g["uses_left"] <= 0:
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("❌ الكود غير صحيح أو منتهي الاستخدام.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))
            return

        amt = g["amount"]
        conn.execute("UPDATE users SET balance = balance + ?, step = 'main' WHERE user_id = ?", (amt, user.id))
        if g["uses_left"] - 1 > 0:
            conn.execute("UPDATE gift_codes SET uses_left = uses_left - 1 WHERE code = ?", (text,))
        else:
            conn.execute("DELETE FROM gift_codes WHERE code = ?", (text,))

        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, f"تفعيل كود هدية {text}", amt))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` NSP لرصيدك!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))
        return

    if step == "input_support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_to_main")]]))
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ الرد المباشر للعميل", callback_data=f"adm_rep_supp_{user.id}")]])
        await notify_admins(context, f"💬 **رسالة دعم جديدة من {user.full_name} (`{user.id}`):**\n\n{text}", reply_markup=kb)
        return

    # ----------------------------------------------------
    # خطوات لوحة الإدارة
    # ----------------------------------------------------
    if is_admin_user(user.id):
        if step == "adm_input_grant_spin_user":
            try:
                parts = text.split()
                tid, num_spins = int(parts[0]), int(parts[1])
                conn.execute("UPDATE users SET free_spins = free_spins + ? WHERE user_id = ?", (num_spins, tid))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم منح `{num_spins}` لفة مجانية للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
                try: await context.bot.send_message(tid, f"🎁 تم منحك `{num_spins}` لفة مجانية في عجلة الحظ من الإدارة!")
                except: pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ الصيغة خاطئة. مثال: `7255100997 5`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_batch_codes":
            try:
                parts = text.split()
                code_prefix, amt, uses_per_code, count = parts[0], float(parts[1]), int(parts[2]), int(parts[3])
                
                generated = []
                for _ in range(count):
                    rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                    full_code = f"{code_prefix}-{rand_str}"
                    conn.execute("INSERT INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (full_code, amt, uses_per_code))
                    generated.append(full_code)

                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()

                codes_fmt = "\n".join([f"🎫 `{c}`" for c in generated])
                msg = (
                    f"🎁 **تم توليد دفعة الأكواد بنجاح!**\n"
                    f"✨ ─────────────────── ✨\n"
                    f"💰 **قيمة الكود:** `{amt}` NSP\n"
                    f"👥 **الاستخدامات لكل كود:** `{uses_per_code}`\n"
                    f"🔢 **عدد الأكواد:** `{count}`\n"
                    f"✨ ─────────────────── ✨\n\n"
                    f"📋 **قائمة الأكواد:**\n{codes_fmt}"
                )
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception as e:
                conn.close()
                await update.message.reply_text(f"❌ خطأ بالبيانات. مثال: `GOLDEN 50 1 10` (الرمز - المبلغ - عدد الأشخاص - عدد الأكواد)", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_dep_bonus":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deposit_bonus_percent', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم ضبط بونص الشحن إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_w_commission":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('withdraw_commission_percent', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم ضبط عمولة السحب إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_wheel_prob":
            try:
                parts = text.split()
                key_name, prob_val = parts[0], float(parts[1])
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"wheel_prob_{key_name}", str(prob_val)))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل احتمال `{key_name}` إلى `{prob_val}%` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 خوارزمية العجلة", callback_data="adm_wheel_algo")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة. اكتب اسم المفتاح ثم النسبة.", reply_markup=cancel_keyboard("adm_wheel_algo"))
            return

        if step == "adm_input_support_reply":
            target_id = context.user_data.get("support_target_id")
            if target_id:
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 مراسلة الدعم مباشرة", callback_data="btn_support")]])
                    await context.bot.send_message(
                        chat_id=int(target_id),
                        text=f"👨‍💻 **رد من الدعم الفني للإدارة:**\n\n{text}",
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    await update.message.reply_text(f"✅ تم إرسال الرد للعميل `{target_id}` وحفظ زر المراسلة لديه بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
                except Exception as e:
                    await update.message.reply_text(f"❌ فشل الإرسال: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

        if step == "adm_input_add_bal":
            try:
                parts = text.split()
                tid, amt = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, tid))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (tid, "إضافة رصيد من الإدارة", amt))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إضافة `{amt}` NSP للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
                try: await context.bot.send_message(tid, f"🎁 تم إضافة `{amt}` NSP لرصيدك من الإدارة!")
                except: pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ مثال: `7255100997 500`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_sub_bal":
            try:
                parts = text.split()
                tid, amt = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amt, tid))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (tid, "خصم رصيد من الإدارة", -amt))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم خصم `{amt}` NSP من اللاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة غير صحيحة.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_make_gift":
            try:
                parts = text.split()
                code_str, amt, uses = parts[0], float(parts[1]), int(parts[2])
                conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code_str, amt, uses))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"🎁 تم إنشاء الكود الفردي: `{code_str}` بقيمة `{amt}` NSP.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ مثال: `VIP100 500 10`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_user_info":
            try:
                tid = int(text)
                u_info = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
                if not u_info:
                    await update.message.reply_text("❌ اللاعب غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                else:
                    dep_stats = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM deposits WHERE user_id = ? AND status = 'approved'", (tid,)).fetchone()
                    w_stats = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM withdrawals WHERE user_id = ? AND status = 'approved'", (tid,)).fetchone()
                    codes_count = conn.execute("SELECT COUNT(*) FROM logs WHERE user_id = ? AND action LIKE 'تفعيل كود هدية%'", (tid,)).fetchone()[0]

                    msg = (
                        f"🔍 **تفاصيل العميل الشاملة:**\n"
                        f"✨ ─────────────────── ✨\n"
                        f"🆔 **ID:** `{u_info['user_id']}`\n"
                        f"👤 **الاسم:** {u_info['full_name']}\n"
                        f"📱 **الهاتف:** `{u_info['phone'] or 'غير مرتبط'}`\n"
                        f"💰 **الرصيد الحالي:** `{u_info['balance']:,.2f}` NSP\n"
                        f"🎡 **اللفات المجانية:** `{u_info['free_spins']}`\n"
                        f"👥 **عدد الإحالات:** `{u_info['referrals_count']}`\n"
                        f"✨ ─────────────────── ✨\n"
                        f"💳 **الشحن الناجح:** {dep_stats[0]} مرة | الإجمالي: `{dep_stats[1]:,.2f}` NSP\n"
                        f"💸 **السحب الناجح:** {w_stats[0]} مرة | الإجمالي: `{w_stats[1]:,.2f}` NSP\n"
                        f"🎁 **البونص المحصل:** {'نعم' if u_info['welcome_bonus_claimed'] else 'لا'}\n"
                        f"🎟️ **الأكواد المستعملة:** {codes_count} كود\n"
                        f"🎰 **إجمالي الرصيد المصروف باللعب:** `{u_info['total_spent']:,.2f}` NSP\n"
                        f"🚫 **الحالة:** {'محظور' if u_info['is_banned'] else 'نشط'}"
                    )
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            except Exception as e:
                await update.message.reply_text(f"❌ أدخل ID صحيح. الخطأ: {e}", reply_markup=cancel_keyboard("open_admin_panel"))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            return

        if step == "adm_input_bc_txt":
            users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            
            # تسريع الإذاعة بتقنية Task Concurrent Batching
            async def send_msg_fast(uid):
                try: await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                except: pass

            tasks = [send_msg_fast(u_item["user_id"]) for u_item in users_list]
            await asyncio.gather(*tasks)
            
            await update.message.reply_text(f"📢 تم إرسال الإذاعة السريعة لـ `{len(users_list)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            return

    conn.close()

# ----------------------------------------------------
# 8. معالجة نقرات الأزرار التفاعلية (Callback Queries)
# ----------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data

    conn = get_db()
    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
    conn.commit()

    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    is_admin = is_admin_user(user.id)

    if not u or u["is_banned"]:
        conn.close()
        return

    if data == "back_to_main":
        conn.close()
        await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
        return

    if data == "btn_offers":
        offers = conn.execute("SELECT * FROM offers").fetchall()
        conn.close()
        if not offers:
            txt = "🎁 **العروض الحالية:**\n\n🔥 بونص شحن مجاني ينزل تلقائياً عند التعبئة!\n🎡 لفات مجانية يومية عبر نظام الإحالات!"
        else:
            txt = "🎁 **العروض الحالية المتاحة:**\n\n"
            for off in offers:
                txt += f"• **{off['title']}**: {off['description']}\n"
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]]))
        return

    if data == "btn_send_proof":
        conn.execute("UPDATE users SET step = 'user_upload_proof' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("📸 **أرسل الآن صورة الإثبات أو تفاصيل إصابة الفوز وستصل فوراً للإدارة:**", reply_markup=cancel_keyboard("back_to_main"))
        return

    if data == "btn_account":
        dep_stats = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM deposits WHERE user_id = ? AND status = 'approved'", (user.id,)).fetchone()
        w_stats = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM withdrawals WHERE user_id = ? AND status = 'approved'", (user.id,)).fetchone()
        conn.close()

        msg = (
            f"👤 **بيانات حسابك الشخصي:**\n"
            f"✨ ─────────────────── ✨\n"
            f"✏️ **الاسم:** {u['full_name']}\n"
            f"🆔 **ID:** `{u['user_id']}`\n"
            f"📱 **الهاتف:** `{u['phone'] or 'غير مرتبط'}`\n"
            f"💰 **الرصيد:** `{u['balance']:,.2f}` NSP\n"
            f"🎡 **اللفات المجانية:** `{u['free_spins']}`\n"
            f"👥 **الإحالات:** `{u['referrals_count']}`\n"
            f"💳 **مجموع الشحن الناجح:** `{dep_stats[1]:,.2f}` NSP ({dep_stats[0]} مرة)\n"
            f"💸 **مجموع السحب الناجح:** `{w_stats[1]:,.2f}` NSP ({w_stats[0]} مرة)\n"
            f"✨ ─────────────────── ✨"
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]]))
        return

    if data == "btn_referral":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        
        if not u["referral_mode"]:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔥 نظام الحرق (10% أرباح عند توفر 3 إحالات نشطة)", callback_data="set_ref_mode_burn")],
                [InlineKeyboardButton("🎰 نظام اللفات المجانية (لفة مجانية لكل إحالة)", callback_data="set_ref_mode_spin")],
                [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]
            ])
            await query.message.edit_text(
                "🔗 **اختر نظام الإحالة الخاص بك (تنبيه: لا يمكن تغيير النظام بعد الاختيار):**\n\n"
                "1️⃣ **نظام الحرق 10%:** تحصل على 10% من رصيد حرق/لعب إحالاتك بشرط وجود 3 إحالات نشطة على الأقل.\n"
                "2️⃣ **نظام اللفة المجانية:** تحصل على لفة مجانية في عجلة الحظ فور تأكيد كل إحالة جديدة.",
                parse_mode="Markdown",
                reply_markup=kb
            )
            conn.close()
            return

        mode_title = "🔥 نظام الحرق (10%)" if u["referral_mode"] == "burn" else "🎰 نظام اللفات المجانية"
        active_status = f"{u['active_referrals_count']} / 3" if u["referral_mode"] == "burn" else f"{u['active_referrals_count']} إحالة"

        msg = (
            f"🔗 **لوحة الإحالات الخاصة بك:**\n"
            f"✨ ─────────────────── ✨\n"
            f"🎯 **النظام المختار:** {mode_title}\n"
            f"👥 **إجمالي الإحالات:** `{u['referrals_count']}`\n"
            f"🔥 **عداد الإحالات النشطة:** `{active_status}`\n\n"
            f"🔗 **رابطك الخاص للنشر:**\n`{ref_link}`\n"
            f"✨ ─────────────────── ✨"
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]]))
        conn.close()
        return

    if data.startswith("set_ref_mode_"):
        selected_mode = data.replace("set_ref_mode_", "")
        conn.execute("UPDATE users SET referral_mode = ? WHERE user_id = ?", (selected_mode, user.id))
        conn.commit()
        conn.close()
        await query.message.edit_text("✅ تم اعتماد نظام الإحالة بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عرض لوحة الإحالة", callback_data="btn_referral")]]))
        return

    if data == "btn_deposit":
        methods = conn.execute("SELECT * FROM deposit_methods").fetchall()
        min_dep = conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"]
        dep_bonus = conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"]
        conn.close()

        kb = []
        for m in methods:
            kb.append([InlineKeyboardButton(f"💳 {m['method_name']}", callback_data=f"dep_meth_id_{m['id']}")])
        kb.append([InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")])

        await query.message.edit_text(
            f"💳 **قسم شحن الرصيد:**\n\n"
            f"💰 **الحد الأدنى للشحن:** `{min_dep}` NSP\n"
            f"🎁 **بونص الشحن المباشر:** `{dep_bonus}%` إضافي!\n\n"
            f"اختر وسيلة الشحن المناسبة:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if data.startswith("dep_meth_id_"):
        m_id = int(data.replace("dep_meth_id_", ""))
        acc = conn.execute("SELECT * FROM deposit_methods WHERE id = ?", (m_id,)).fetchone()
        conn.close()

        if not acc:
            return

        context.user_data["dep_method"] = acc["method_name"]
        context.user_data["dep_acc_details"] = acc["account_details"]
        
        conn.execute("UPDATE users SET step = 'deposit_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()

        await query.message.edit_text(f"💳 **طريقة الشحن:** {acc['method_name']}\n\n✍️ **أدخل المبلغ المراد شحنه بالـ NSP:**", parse_mode="Markdown", reply_markup=cancel_keyboard("btn_deposit"))
        return

    if data == "btn_withdraw":
        min_w = conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"]
        w_comm = conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"]
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_meth_Syriatel Cash")],
            [InlineKeyboardButton("📱 إم تي إن كاش", callback_data="w_meth_MTN Cash")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="w_meth_Bank Cham Cash")],
            [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]
        ])
        await query.message.edit_text(
            f"💸 **قسم سحب الأرباح:**\n\n"
            f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` NSP\n"
            f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NSP\n"
            f"➖ **عمولة السحب:** `{w_comm}%`\n\n"
            f"اختر طريقة السحب:",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    if data.startswith("w_meth_"):
        method = data.replace("w_meth_", "")
        context.user_data["withdraw_method"] = method
        conn.execute("UPDATE users SET step = 'withdraw_step_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text(f"✍️ **وسيلة السحب:** {method}\n\nأدخل رقم الحساب أو المحفظة:", reply_markup=cancel_keyboard("btn_withdraw"))
        return

    if data == "btn_gift":
        conn.execute("UPDATE users SET step = 'input_gift_code' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("🎁 **أدخل كود الهدية الخاص بك:**", reply_markup=cancel_keyboard("back_to_main"))
        return

    if data == "btn_support":
        conn.execute("UPDATE users SET step = 'input_support_msg' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("💬 **اكتب رسالتك وستصل لفريق الدعم فوراً:**", reply_markup=cancel_keyboard("back_to_main"))
        return

    if data == "btn_logs":
        logs = conn.execute("SELECT action, amount, timestamp FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
        conn.close()
        txt = "📜 **آخر 10 عمليات بحسابك:**\n\n" + ("\n".join([f"• `{lg['timestamp']}` | {lg['action']} | `{lg['amount']}` NSP" for lg in logs]) if logs else "لا توجد سجلات.")
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]]))
        return

    if data == "btn_buy_bot":
        conn.close()
        await query.message.edit_text("🤖 **لشراء بوتك وتطوير سيرفرك تواصل مع المبرمج:**\n\n📢 @lerafree", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main")]]))
        return

    # ----------------------------------------------------
    # أزرار الإدارة الشاملة
    # ----------------------------------------------------
    if is_admin:
        if data == "open_admin_panel":
            conn.close()
            await query.message.edit_text("⚙️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_wheel_algo":
            probs = dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'wheel_prob_%'").fetchall())
            conn.close()

            msg = (
                "🎡 **لوحة خوارزميات عجلة الحظ (النسب المئوية):**\n"
                "✨ ─────────────────── ✨\n"
                f"• حظ أوفر: `{probs.get('wheel_prob_luck', '25')}%`\n"
                f"• 5 NSP: `{probs.get('wheel_prob_5', '20')}%`\n"
                f"• 10 NSP: `{probs.get('wheel_prob_10', '15')}%`\n"
                f"• 15 NSP: `{probs.get('wheel_prob_15', '10')}%`\n"
                f"• حاول مجدداً: `{probs.get('wheel_prob_try_again', '15')}%`\n"
                f"• 25 NSP: `{probs.get('wheel_prob_25', '7')}%`\n"
                f"• 50 NSP: `{probs.get('wheel_prob_50', '4')}%`\n"
                f"• 100 NSP: `{probs.get('wheel_prob_100', '2')}%`\n"
                f"• 250 NSP: `{probs.get('wheel_prob_250', '1')}%`\n"
                f"• بونص شحن 20%: `{probs.get('wheel_prob_dep_bonus_20', '0.8')}%`\n"
                f"• 500 NSP: `{probs.get('wheel_prob_500', '0.15')}%`\n"
                f"• 1000 NSP: `{probs.get('wheel_prob_1000', '0.05')}%`\n"
                "✨ ─────────────────── ✨\n"
                "✍️ لتعديل أي عنصر أرسل الاسم مع النسبة المئوية (مثال: `luck 30` أو `1000 0.1`):"
            )
            conn_u = get_db()
            conn_u.execute("UPDATE users SET step = 'adm_input_wheel_prob' WHERE user_id = ?", (user.id,))
            conn_u.commit()
            conn_u.close()
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_grant_spins_menu":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌍 منح لفة مجانية لجميع اللاعبين", callback_data="adm_grant_spins_all")],
                [InlineKeyboardButton("👤 منح لفات للاعب معين", callback_data="adm_grant_spins_user")],
                [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]
            ])
            await query.message.edit_text("🎰 **قسم إهداء اللفات المجانية:**", reply_markup=kb)
            conn.close()
            return

        if data == "adm_grant_spins_all":
            conn.execute("UPDATE users SET free_spins = free_spins + 1")
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم منح لفة مجانية واحدة لجميع اللاعبين بالسيرفر!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            return

        if data == "adm_grant_spins_user":
            conn.execute("UPDATE users SET step = 'adm_input_grant_spin_user' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID اللاعب ثم مسافة ثم عدد اللفات (مثال: `7255100997 5`):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_batch_codes":
            conn.execute("UPDATE users SET step = 'adm_input_batch_codes' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل التنسيق للتوليد التلقائي (الرمز المبلغ عدد_الأشخاص عدد_الأكواد):**\nمثال: `GOLDEN 100 5 10`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_players_log":
            players = conn.execute("SELECT user_id, full_name, balance, referrals_count FROM users ORDER BY balance DESC LIMIT 15").fetchall()
            conn.close()
            txt = "📊 **سجل ونقاط أعلى اللاعبين رصيداً:**\n\n"
            for p in players:
                txt += f"• `{p['user_id']}` | {p['full_name']} | 💰 `{p['balance']:,.2f}` NSP | 👥 `{p['referrals_count']}` إحالة\n"
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            return

        if data == "adm_list_admins":
            admins = conn.execute("SELECT user_id FROM admins").fetchall()
            conn.close()
            txt = "👮 **قائمة الأدمنية المعتمدين بالسيرفر:**\n\n"
            for a in admins:
                txt += f"• `{a['user_id']}` (صلاحيات كاملة)\n"
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="open_admin_panel")]]))
            return

        if data.startswith("adm_rep_supp_"):
            target_id = data.replace("adm_rep_supp_", "")
            context.user_data["support_target_id"] = target_id
            conn.execute("UPDATE users SET step = 'adm_input_support_reply' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"✍️ **اكتب ردك المباشر للعميل (`{target_id}`):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_set_dep_bonus":
            conn.execute("UPDATE users SET step = 'adm_input_dep_bonus' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل نسبة بونص الشحن المباشر (%):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_set_w_commission":
            conn.execute("UPDATE users SET step = 'adm_input_w_commission' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل نسبة عمولة السحب (%):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_user_info":
            conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل معرف ID العميل لعرض التفاصيل الشاملة:**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_bc_txt":
            conn.execute("UPDATE users SET step = 'adm_input_bc_txt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل نص الرسالة الجماعية السريعة:**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

    conn.close()

# ----------------------------------------------------
# 9. تشغيل البوت المباشر
# ----------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", lambda u, c: u.message.reply_text("👮 **لوحة الإدارة:**", reply_markup=admin_panel_keyboard())))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot Server started successfully!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
