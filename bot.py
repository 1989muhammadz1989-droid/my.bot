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
    if DEFAULT_ADMIN_ID and user_id == DEFAULT_ADMIN_ID:
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
    cursor.execute('CREATE TABLE IF NOT EXISTS code_restrictions (user_id INTEGER PRIMARY KEY, last_used INTEGER)')

    if DEFAULT_ADMIN_ID:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
        
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

    cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (1, 'شام كاش', 'test')")
    cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (2, 'سيريتل كاش', 'test')")

    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 3. إرسال الرسائل والتفاعل والإشعارات
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

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    conn = get_db()
    admins = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    for adm in admins:
        try:
            await context.bot.send_message(chat_id=adm["user_id"], text=text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            pass

async def process_welcome_and_referral_rewards(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not u:
        conn.close()
        return

    if u["welcome_bonus_claimed"] == 0:
        welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
        welcome_bonus = float(conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]) if welcome_enabled else 0.0

        conn.execute("UPDATE users SET welcome_bonus_claimed = 1 WHERE user_id = ?", (user_id,))
        if welcome_bonus > 0:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (welcome_bonus, user_id))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user_id, "بونص ترحيبي بعد التثبت من القنوات", welcome_bonus))
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎁 **مبارك! حصلت على البونص الترحيبي قدره `{welcome_bonus}` NSP لإتمام اشتراك القنوات!**",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        if u["referred_by"]:
            ref_id = u["referred_by"]
            ref_spin_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"] == "1"
            
            if ref_spin_enabled:
                conn.execute("UPDATE users SET free_spins = free_spins + 1, referrals_count = referrals_count + 1, active_referrals_count = active_referrals_count + 1 WHERE user_id = ?", (ref_id,))
                try:
                    await context.bot.send_message(
                        chat_id=ref_id,
                        text=f"🎉 **إحالة ناجحة جديدة!**\n👤 تجاوز العميل {u['full_name']} اشتراك القنوات وحصلت على **1 لفة مجانية** 🎡!",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

    conn.commit()
    conn.close()

# ----------------------------------------------------
# 4. لوحات التحكم والقوائم
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    wheel_url = f"{SERVER_URL}/wheel"
    keyboard = [
        [InlineKeyboardButton("💎 دخول صفحة الألعاب | Golden Games 🎰", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("🎡 عجلة الحظ 🎯", web_app=WebAppInfo(url=wheel_url)), InlineKeyboardButton("🎁 العروض الحالية 🔥", callback_data="btn_offers")],
        [InlineKeyboardButton("💳 شحن رصيد ⚡", callback_data="btn_deposit"), InlineKeyboardButton("💸 سحب رصيدي 🪙", callback_data="btn_withdraw")],
        [InlineKeyboardButton("👤 حسابي ورصيدي 📊", callback_data="btn_account"), InlineKeyboardButton("🔗 رابط إحالاتي 🚀", callback_data="btn_referral")],
        [InlineKeyboardButton("📸 إرسال إصابة / إثبات 🏆", callback_data="btn_send_proof"), InlineKeyboardButton("🎟️ إدخال كود هدية 🎁", callback_data="btn_gift")],
        [InlineKeyboardButton("🤖 شراء بوت ⚙️", callback_data="btn_buy_bot"), InlineKeyboardButton("📜 سجلاتي 📑", callback_data="btn_logs")],
        [InlineKeyboardButton("💬 مراسلة الدعم 👨‍💻", callback_data="btn_support"), InlineKeyboardButton("📢 قناة المبرمج الرسمية 🚀", url="https://t.me/lerafree")]
    ]
    if is_admin:
        keyboard.insert(0, [InlineKeyboardButton("⚙️ لوحة الإدارة الشاملة 👮‍♂️", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    conn = get_db()
    maint_status = "🔴 مفعل (البوت مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
    
    welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
    welcome_status = "🟢 مفعل" if welcome_enabled else "🔴 معطل"
    
    ref_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"] == "1"
    ref_status = "🟢 مفعل" if ref_enabled else "🔴 معطل"

    ref_spin_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"] == "1"
    ref_spin_status = "🟢 مفعل" if ref_spin_enabled else "🔴 معطل"
    conn.close()

    keyboard = [
        [InlineKeyboardButton(f"🛠️ وضع الصيانة: {maint_status}", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🔥 الإحالات النشطة والمستحقات 💰", callback_data="adm_active_referrals")],
        [InlineKeyboardButton("🎡 خوارزمية عجلة الحظ 🎯", callback_data="adm_wheel_algo"), InlineKeyboardButton("🎯 حظ لاعب معين ⚡", callback_data="adm_user_boost")],
        [InlineKeyboardButton(f"🎁 البونص الترحيبي: {welcome_status}", callback_data="adm_toggle_welcome"), InlineKeyboardButton(f"🔗 بونص الإحالة: {ref_status}", callback_data="adm_toggle_ref_bonus")],
        [InlineKeyboardButton(f"🎡 لفة الإحالة المجانية: {ref_spin_status}", callback_data="adm_toggle_ref_spin")],
        [InlineKeyboardButton("🎰 منح لفات مجانية 🎁", callback_data="adm_grant_spins_menu"), InlineKeyboardButton("📢 قنوات الاشتراك الصارمة 🚀", callback_data="adm_channels_menu")],
        [InlineKeyboardButton("💳 إدارة حسابات الشحن 📑", callback_data="adm_dep_methods"), InlineKeyboardButton("📥 طلبات الشحن المعلقة ⏳", callback_data="adm_deposits")],
        [InlineKeyboardButton("💸 طلبات السحب المعلقة 💸", callback_data="adm_withdraws"), InlineKeyboardButton("🎁 إدارة العروض الحالية 🔥", callback_data="adm_offers_menu")],
        [InlineKeyboardButton("🔓 إلغاء تقييد الأكواد 🎟️", callback_data="adm_code_restrictions"), InlineKeyboardButton("🎟️ الأكواد النشطة وإلغاء كود ❌", callback_data="adm_active_codes")],
        [InlineKeyboardButton("➕ بونص الشحن (%) ⚡", callback_data="adm_set_dep_bonus"), InlineKeyboardButton("➖ عمولة السحب (%) 🪙", callback_data="adm_set_w_commission")],
        [InlineKeyboardButton("💰 حد الشحن الأدنى 💵", callback_data="adm_set_min_dep"), InlineKeyboardButton("💸 حد السحب الأدنى 💶", callback_data="adm_set_min_w")],
        [InlineKeyboardButton("➕ إضافة رصيد 💎", callback_data="adm_add_bal"), InlineKeyboardButton("➖ خصم رصيد 🔻", callback_data="adm_sub_bal")],
        [InlineKeyboardButton("🎟️ توليد أكواد دفعة 🎁", callback_data="adm_batch_codes"), InlineKeyboardButton("🎫 إنشاء كود فردي ✨", callback_data="adm_make_gift")],
        [InlineKeyboardButton("🔍 تفاصيل عميل كاملة 📊", callback_data="adm_user_info"), InlineKeyboardButton("📊 سجل ونقاط اللاعبين 🏆", callback_data="adm_players_log")],
        [InlineKeyboardButton("👮 عرض قائمة الأدمنية 👑", callback_data="adm_list_admins"), InlineKeyboardButton("📊 الإحصائيات الشاملة 🚀", callback_data="adm_stats")],
        [InlineKeyboardButton("🚫 حظر مستخدم ❌", callback_data="adm_ban"), InlineKeyboardButton("✅ فك الحظر 🔓", callback_data="adm_unban")],
        [InlineKeyboardButton("📢 إذاعة سريعة (نص) 📣", callback_data="adm_bc_txt"), InlineKeyboardButton("📸 إذاعة سريعة (صورة) 🖼️", callback_data="adm_bc_img")],
        [InlineKeyboardButton("📩 رسالة خاصة 💬", callback_data="adm_pm_txt"), InlineKeyboardButton("👮 إضافة أدمن ➕", callback_data="adm_add_admin")],
        [InlineKeyboardButton("❌ إزالة أدمن ➖", callback_data="adm_del_admin")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ----------------------------------------------------
# 5. الأوامر والمعالجات الرئيسية
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

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

        ref_msg = f"\n🔗 **بواسطة الإحالة:** `{ref_id}`" if ref_id else ""
        await notify_admins(context, f"🔔 **دخول لاعب جديد:**\n👤 **الاسم:** {user.full_name}\n🆔 **المعرف:** `{user.id}`{ref_msg}")

        if ref_id:
            try:
                await context.bot.send_message(
                    chat_id=ref_id,
                    text=f"🎉 **انضم لاعب جديد عبر رابط إحالتك!**\n👤 **اللاعب:** {user.full_name}\n🆔 **المعرف:** `{user.id}`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        msg = (
            f"👋 **أهلاً بك يا {user.full_name} في منصة Golden Games!** 🎮\n\n"
            f"🛡️ **اختبار الأمان:**\n"
            f"ما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)\n"
            f"✍️ أرسل إجابتك للبدء:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    conn.close()

    await process_welcome_and_referral_rewards(user.id, context)

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
        f"👑 **مرحباً بك في منصة الألعاب Golden Games 2026** 🎰\n"
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
# 6. معالجة الصور والإثباتات والإيصالات
# ----------------------------------------------------
async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_maintenance_active() and not is_admin_user(user.id):
        await update.message.reply_text("🛠️ **السيرفر في حالة صيانة حالياً.**")
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    
    if not u or u["is_banned"]:
        conn.close()
        return

    step = u["step"]
    photo_file_id = update.message.photo[-1].file_id
    caption = update.message.caption or "بدون وصف"

    if step == "user_upload_proof":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()

        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        conn.close()

        msg_text = (
            f"📸 **إثبات إصابة/فوز جديد من عميل:**\n"
            f"✨ ─────────────────── ✨\n"
            f"👤 **الاسم:** {user.full_name}\n"
            f"🆔 **المعرف:** `{user.id}`\n"
            f"📝 **التفاصيل/الوصف:** {caption}"
        )
        for adm in admins:
            try:
                await context.bot.send_photo(chat_id=adm["user_id"], photo=photo_file_id, caption=msg_text, parse_mode="Markdown")
            except Exception:
                pass

        await update.message.reply_text("✅ **تم إرسال صورة الإثبات إلى الإدارة بنجاح!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        return

    if step == "deposit_step_tx":
        amt = context.user_data.get("dep_amount", 0.0)
        method = context.user_data.get("dep_method", "غير محدد")

        cursor = conn.execute("INSERT INTO deposits (user_id, method, amount, tx_id, photo_file_id) VALUES (?, ?, ?, ?, ?)",
                              (user.id, method, amt, f"إيصال مصور: {caption}", photo_file_id))
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        dep_id = cursor.lastrowid

        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        conn.close()

        await update.message.reply_text("✅ **تم تقديم طلب الشحن مع صورة الإيصال بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
        dep_text = f"📥 **طلب شحن جديد بإيصال مصور (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n💰 المبلغ: `{amt}` NSP\n📝 الوصف: {caption}"
        
        for adm in admins:
            try:
                await context.bot.send_photo(chat_id=adm["user_id"], photo=photo_file_id, caption=dep_text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        return

    if is_admin_user(user.id) and step == "adm_input_bc_img":
        users_list = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()

        bc_caption = update.message.caption or ""

        async def send_photo_fast(uid):
            try:
                await context.bot.send_photo(chat_id=uid, photo=photo_file_id, caption=bc_caption, parse_mode="Markdown")
            except Exception:
                pass

        tasks = [send_photo_fast(u_item["user_id"]) for u_item in users_list]
        await asyncio.gather(*tasks)

        await update.message.reply_text(f"📸 تم إرسال الإذاعة المصورة لـ `{len(users_list)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
        return

    conn.close()

# ----------------------------------------------------
# 7. معالجة الرقم والتوثيق
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact
    
    if contact.user_id != user.id:
        await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي الخاص بك فقط.")
        return

    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()

    if u and u["phone_verified"] == 1:
        conn.close()
        await update.message.reply_text("⚠️ لقد قمت بتأكيد رقمك سابقاً! لا يمكن تكرار تأكيد الرقم.", reply_markup=ReplyKeyboardRemove())
        await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)
        return

    check_phone = conn.execute("SELECT user_id FROM users WHERE phone = ? AND phone_verified = 1", (contact.phone_number,)).fetchone()
    if check_phone:
        conn.close()
        await update.message.reply_text("❌ هذا الرقم موثق ومستعمل سابقاً في حساب آخر!", reply_markup=ReplyKeyboardRemove())
        return

    conn.execute(
        "UPDATE users SET phone = ?, is_verified = 1, phone_verified = 1, step = 'main' WHERE user_id = ?",
        (contact.phone_number, user.id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ **تم تأكيد رقم هاتفك وحسابك بنجاح!**",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    
    await process_welcome_and_referral_rewards(user.id, context)
    await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)

# ----------------------------------------------------
# 8. معالجة الرسائل النصية
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
        await notify_admins(context, f"📸 **إثبات إصابة/فوز نصي من العميل:**\n👤 {user.full_name} (`{user.id}`)\n📝 التفاصيل: {text}")
        await update.message.reply_text("✅ تم إرسال الإثبات النصي للإدارة بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        return

    # معالجة الشحن: إدخال المبلغ وتوجيهه لحساب التحويل
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

        dep_bonus = float(conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"])
        bonus_val = amt * (dep_bonus / 100.0)
        total_expected = amt + bonus_val

        context.user_data["dep_amount"] = amt
        method_name = context.user_data.get("dep_method", "غير محدد")
        acc_details = context.user_data.get("dep_acc_details", "غير متوفر")

        conn.execute("UPDATE users SET step = 'deposit_step_tx' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()

        msg = (
            f"💳 **وسيلة الشحن:** {method_name}\n"
            f"💰 **المبلغ المطلوبة شحنه:** `{amt}` NSP\n"
            f"🎁 **بونص الشحن المباشر ({dep_bonus}%):** `{bonus_val}` NSP\n"
            f"💎 **إجمالي الرصيد الذي سيصلك:** `{total_expected}` NSP\n\n"
            f"📌 **يرجى التحويل إلى الحساب التالي:**\n`{acc_details}`\n\n"
            f"✍️ **الآن أرسل رقم العملية/الإشعار أو أرسل صورة الإيصال مباشرة لتأكيد الطلب:**"
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

        await update.message.reply_text("✅ **تم تقديم طلب الشحن بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
        await notify_admins(context, f"📥 **طلب شحن جديد (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الإشعار/العملية: `{text}`\n💰 المبلغ: `{amt}` NSP", reply_markup=kb)
        return

    # معالجة السحب: رقم المحفظة والمبلغ وإنشاء ملخص مراجعة
    if step == "withdraw_step_code":
        context.user_data["withdraw_code"] = text
        conn.execute("UPDATE users SET step = 'withdraw_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✍️ **أدخل المبلغ المراد سحبه (NSP):**", reply_markup=cancel_keyboard("btn_withdraw"))
        return

    if step == "withdraw_step_amount":
        try:
            amt = float(text)
        except ValueError:
            conn.close()
            await update.message.reply_text("❌ أدخل رقماً صحيحاً.", reply_markup=cancel_keyboard("btn_withdraw"))
            return

        min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])
        if amt < min_w or amt > u["balance"]:
            conn.close()
            await update.message.reply_text(f"❌ المبلغ غير متاح أو أقل من حد السحب الأدنى (`{min_w}` NSP).", reply_markup=cancel_keyboard("btn_withdraw"))
            return

        comm_pct = float(conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"])
        comm_val = amt * (comm_pct / 100.0)
        net_amt = amt - comm_val

        method = context.user_data.get("withdraw_method", "غير محدد")
        acc_code = context.user_data.get("withdraw_code", "غير محدد")

        context.user_data["withdraw_amount"] = amt
        context.user_data["withdraw_net"] = net_amt

        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة وتأكيد الطلب 🚀", callback_data="confirm_withdraw_request")],
            [InlineKeyboardButton("✏️ تعديل / إلغاء 🔄", callback_data="btn_withdraw")]
        ])

        summary_msg = (
            f"📑 **ملخص طلب السحب الخاص بك:**\n"
            f"✨ ─────────────────── ✨\n"
            f"💳 **وسيلة السحب:** {method}\n"
            f"🔢 **رقم الحساب/المحفظة:** `{acc_code}`\n"
            f"💰 **المبلغ المطلوب:** `{amt:,.2f}` NSP\n"
            f"➖ **عمولة السحب ({comm_pct}%):** `{comm_val:,.2f}` NSP\n"
            f"💵 **المبلغ الصافي للتحويل:** `{net_amt:,.2f}` NSP\n"
            f"✨ ─────────────────── ✨\n\n"
            f"هل أنت متأكد من صحة التفاصيل ورغبتك في إرسال الطلب للإدارة؟"
        )
        await update.message.reply_text(summary_msg, parse_mode="Markdown", reply_markup=kb)
        return

    if step == "input_gift_code":
        now_ts = int(time.time())
        restr = conn.execute("SELECT last_used FROM code_restrictions WHERE user_id = ?", (user.id,)).fetchone()
        if restr:
            elapsed = now_ts - restr["last_used"]
            if elapsed < 21600:
                rem_sec = 21600 - elapsed
                hrs = rem_sec // 3600
                mins = (rem_sec % 3600) // 60
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"⚠️ **تقييد الأكواد:**\nيمكنك استخدام كود هدية واحد كل 6 ساعات فقط.\n⏱️ **المتبقي:** `{hrs}` ساعة و `{mins}` دقيقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

        g = conn.execute("SELECT * FROM gift_codes WHERE code = ?", (text,)).fetchone()
        if not g or g["uses_left"] <= 0:
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("❌ الكود غير صحيح أو منتهي الاستخدام.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        amt = g["amount"]
        conn.execute("UPDATE users SET balance = balance + ?, step = 'main' WHERE user_id = ?", (amt, user.id))
        
        if g["uses_left"] - 1 > 0:
            conn.execute("UPDATE gift_codes SET uses_left = uses_left - 1 WHERE code = ?", (text,))
        else:
            conn.execute("DELETE FROM gift_codes WHERE code = ?", (text,))

        conn.execute("INSERT INTO code_restrictions (user_id, last_used) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_used = ?", (user.id, now_ts, now_ts))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, f"تفعيل كود هدية {text}", amt))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` NSP لرصيدك!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        await notify_admins(context, f"🎟️ **تفعيل كود هدية:**\n👤 **اللاعب:** {user.full_name} (`{user.id}`)\n🎫 **الكود:** `{text}`\n💰 **المبلغ:** `{amt}` NSP")
        return

    if step == "input_support_msg":
        conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ الرد المباشر للعميل", callback_data=f"adm_rep_supp_{user.id}")]])
        await notify_admins(context, f"💬 **رسالة دعم جديدة من {user.full_name} (`{user.id}`):**\n\n{text}", reply_markup=kb)
        return

    # ----------------------------------------------------
    # خطوات لوحة الإدارة الشاملة
    # ----------------------------------------------------
    if is_admin_user(user.id):
        if step == "adm_input_add_ref_dues":
            try:
                parts = text.split()
                tid, amt = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, tid))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (tid, "مستحقات نظام الإحالات النشطة (دورية)", amt))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()

                await update.message.reply_text(f"✅ **تم إرسال مستحقات الإحالات بقيمة `{amt}` NSP للاعب `{tid}` بنجاح.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                try:
                    await context.bot.send_message(
                        chat_id=tid,
                        text=f"🎁 **تهانينا! تم إضافة مستحقات نظام الإحالات النشطة الخاصة بك بقيمة `{amt}` NSP إلى رصيدك!** 🚀",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة خاطئة. مثال: `7255100997 500`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step.startswith("adm_edit_dep_acc_"):
            m_id = int(step.replace("adm_edit_dep_acc_", ""))
            conn.execute("UPDATE deposit_methods SET account_details = ? WHERE id = ?", (text, m_id))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم تحديث حساب الشحن بنجاح إلى:\n`{text}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة إدارة الشحن 💳", callback_data="adm_dep_methods")]]))
            return

        if step == "adm_input_del_code_manual":
            c_code = text.strip()
            g = conn.execute("SELECT * FROM gift_codes WHERE code = ?", (c_code,)).fetchone()
            if not g:
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text("❌ الكود غير موجود أو ملغي سابقاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            conn.execute("DELETE FROM gift_codes WHERE code = ?", (c_code,))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إلغاء الكود `{c_code}` وحذفه بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if step == "adm_input_welcome_amt":
            try:
                amt = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('welcome_bonus', ?)", (str(amt),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل قيمة البونص الترحيبي إلى `{amt}` NSP بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_ref_amt":
            try:
                amt = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_reward', ?)", (str(amt),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل مكافأة الإحالة إلى `{amt}` NSP بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_add_dep_meth":
            try:
                parts = text.split("|")
                m_name, m_det = parts[0].strip(), parts[1].strip()
                conn.execute("INSERT INTO deposit_methods (method_name, account_details) VALUES (?, ?)", (m_name, m_det))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إضافة وسيلة الشحن: `{m_name}` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة خاطئة. يجب الفصل بـ `|`. مثال: `شام كاش | test`", reply_markup=cancel_keyboard("adm_dep_methods"))
            return

        if step == "adm_input_add_offer":
            try:
                parts = text.split("|")
                off_t, off_d, off_l = parts[0].strip(), parts[1].strip(), parts[2].strip()
                conn.execute("INSERT INTO offers (title, description, link) VALUES (?, ?, ?)", (off_t, off_d, off_l))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إضافة العرض: **{off_t}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `عنوان العرض | وصف العرض | https://t.me/example`", reply_markup=cancel_keyboard("adm_offers_menu"))
            return

        if step == "adm_input_add_channel":
            try:
                parts = text.split("|")
                ch_id, ch_title, ch_link = parts[0].strip(), parts[1].strip(), parts[2].strip()
                conn.execute("INSERT OR REPLACE INTO channels (channel_id, channel_title, channel_link) VALUES (?, ?, ?)", (ch_id, ch_title, ch_link))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إضافة قناة الاشتراك الصارم: **{ch_title}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `@MyChan | قناة الأخبار | https://t.me/MyChan`", reply_markup=cancel_keyboard("adm_channels_menu"))
            return

        if step == "adm_input_unrestrict_user":
            try:
                tid = int(text)
                conn.execute("DELETE FROM code_restrictions WHERE user_id = ?", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إلغاء تقييد استخدام الكود عن اللاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_clear_boost":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET custom_boost = 0.0 WHERE user_id = ?", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إلغاء الحظ الخاص عن اللاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_user_boost":
            try:
                parts = text.split()
                tid, boost_val = int(parts[0]), float(parts[1])
                conn.execute("UPDATE users SET custom_boost = ? WHERE user_id = ?", (boost_val, tid))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تخصيص نسبة حظ `{boost_val}%` للاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ مثال: `7255100997 50`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_wheel_prob_val":
            target_key = context.user_data.get("edit_wheel_key")
            if target_key:
                try:
                    prob_val = float(text)
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (target_key, str(prob_val)))
                    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                    conn.commit()
                    conn.close()
                    await update.message.reply_text(f"✅ تم تعديل احتمال `{target_key}` إلى `{prob_val}%` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 خوارزمية العجلة 🎡", callback_data="adm_wheel_algo")]]))
                    return
                except Exception:
                    pass
            conn.close()
            await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("adm_wheel_algo"))
            return

        if step == "adm_input_grant_spin_user":
            try:
                parts = text.split()
                tid, num_spins = int(parts[0]), int(parts[1])
                conn.execute("UPDATE users SET free_spins = free_spins + ? WHERE user_id = ?", (num_spins, tid))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم منح `{num_spins}` لفة مجانية للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text(f"❌ خطأ بالبيانات. مثال: `GOLDEN 50 1 10`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_make_gift":
            try:
                parts = text.split()
                code_str, amt, uses = parts[0], float(parts[1]), int(parts[2])
                conn.execute("INSERT OR REPLACE INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (code_str, amt, uses))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"🎁 **تم إنشاء الكود الفردي بنجاح!**\n\n🎫 **الكود:** `{code_str}`\n💰 **المبلغ:** `{amt}` NSP\n👥 **الاستخدامات:** `{uses}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ مثال: `VIP100 500 10`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_dep_bonus":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deposit_bonus_percent', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم ضبط بونص الشحن إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                await update.message.reply_text(f"✅ تم ضبط عمولة السحب إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_min_dep":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('min_deposit', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل حد الشحن الأدنى إلى `{val}` NSP.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_min_w":
            try:
                val = float(text)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('min_withdraw', ?)", (str(val),))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تعديل حد السحب الأدنى إلى `{val}` NSP.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
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
                await update.message.reply_text(f"✅ تم إضافة `{amt}` NSP للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                await update.message.reply_text(f"✅ تم خصم `{amt}` NSP من اللاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ مثال: `7255100997 100`", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_ban":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET is_banned = 1, step = 'main' WHERE user_id = ?", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"🚫 تم حظر المستخدم `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_unban":
            try:
                tid = int(text)
                conn.execute("UPDATE users SET is_banned = 0, step = 'main' WHERE user_id = ?", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم فك حظر المستخدم `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_add_admin":
            try:
                tid = int(text)
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"👮 تم إضافة الأدمن الجديد `{tid}` بنجاح ومنحه كامل الصلاحيات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                try: await context.bot.send_message(tid, "👑 **تهانينا! تم منحك صلاحيات أدمن كاملة في البوت.**")
                except: pass
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_del_admin":
            try:
                tid = int(text)
                if DEFAULT_ADMIN_ID and tid == DEFAULT_ADMIN_ID:
                    conn.close()
                    await update.message.reply_text("❌ لا يمكن إزالة الأدمن الرئيسي المنشئ.", reply_markup=cancel_keyboard("open_admin_panel"))
                    return
                conn.execute("DELETE FROM admins WHERE user_id = ?", (tid,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إزالة الأدمن `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception:
                conn.close()
                await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_pm_txt":
            try:
                parts = text.split(" ", 1)
                tid, msg_content = int(parts[0]), parts[1]
                await context.bot.send_message(chat_id=tid, text=f"📩 **رسالة خاصة من إدارة منصة Golden Games:**\n\n{msg_content}", parse_mode="Markdown")
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم إرسال الرسالة الخاصة إلى `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            except Exception as e:
                conn.close()
                await update.message.reply_text(f"❌ فشل الإرسال. الصيغة: `ID الرسالة` | الخطأ: {e}", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if step == "adm_input_support_reply":
            target_id = context.user_data.get("support_target_id")
            if target_id:
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 مراسلة الدعم مباشرة 👨‍💻", callback_data="btn_support")]])
                    await context.bot.send_message(
                        chat_id=int(target_id),
                        text=f"👨‍💻 **رد من الدعم الفني للإدارة:**\n\n{text}",
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    await update.message.reply_text(f"✅ تم إرسال الرد للعميل `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception as e:
                    await update.message.reply_text(f"❌ فشل الإرسال: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
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
                        f"🎯 **نسبة الحظ الخاص:** `{u_info['custom_boost']}%`\n"
                        f"✨ ─────────────────── ✨\n"
                        f"💳 **الشحن الناجح:** {dep_stats[0]} مرة | الإجمالي: `{dep_stats[1]:,.2f}` NSP\n"
                        f"💸 **السحب الناجح:** {w_stats[0]} مرة | الإجمالي: `{w_stats[1]:,.2f}` NSP\n"
                        f"🎁 **البونص المحصل:** {'نعم' if u_info['welcome_bonus_claimed'] else 'لا'}\n"
                        f"🎟️ **الأكواد المستعملة:** {codes_count} كود\n"
                        f"🎰 **إجمالي الرصيد المصروف باللعب:** `{u_info['total_spent']:,.2f}` NSP\n"
                        f"🚫 **الحالة:** {'محظور' if u_info['is_banned'] else 'نشط'}"
                    )
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
            
            async def send_msg_fast(uid):
                try: await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                except: pass

            tasks = [send_msg_fast(u_item["user_id"]) for u_item in users_list]
            await asyncio.gather(*tasks)
            
            await update.message.reply_text(f"📢 تم إرسال الإذاعة السريعة لـ `{len(users_list)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

    conn.close()

# ----------------------------------------------------
# 9. معالجة نقرات الأزرار التفاعلية (Callback Queries)
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

    if data == "check_subscription_status":
        is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
        if is_subscribed:
            await query.message.edit_text("✅ **تم التأكد من اشتراكك بنجاح! أهلاً بك.**")
            await process_welcome_and_referral_rewards(user.id, context)
            await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
        else:
            await query.message.edit_text("❌ **لم تشترك في جميع القنوات بعد. يرجى الاشتراك أولاً:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
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
                if off['link']:
                    txt += f"🔗 [رابط العرض]({off['link']})\n"
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]]))
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
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        return

    if data == "btn_referral":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        
        if not u["referral_mode"]:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔥 نظام الحرق (10% أرباح عند توفر 3 إحالات نشطة)", callback_data="set_ref_mode_burn")],
                [InlineKeyboardButton("🎰 نظام اللفات المجانية (لفة مجانية لكل إحالة)", callback_data="set_ref_mode_free_spin")],
                [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]
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
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        conn.close()
        return

    if data.startswith("set_ref_mode_"):
        selected_mode = data.replace("set_ref_mode_", "")
        conn.execute("UPDATE users SET referral_mode = ? WHERE user_id = ?", (selected_mode, user.id))
        conn.commit()
        conn.close()
        await query.message.edit_text("✅ تم اعتماد نظام الإحالة بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عرض لوحة الإحالة 🚀", callback_data="btn_referral")]]))
        return

    # عرض طرق الشحن وبونص الشحن المباشر
    if data == "btn_deposit":
        methods = conn.execute("SELECT * FROM deposit_methods").fetchall()
        min_dep = conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"]
        dep_bonus = conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"]
        conn.close()

        kb = []
        for m in methods:
            kb.append([InlineKeyboardButton(f"💳 {m['method_name']}", callback_data=f"dep_meth_id_{m['id']}")])
        kb.append([InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")])

        await query.message.edit_text(
            f"💳 **قسم شحن الرصيد:**\n\n"
            f"💰 **الحد الأدنى للشحن:** `{min_dep}` NSP\n"
            f"🎁 **بونص الشحن المباشر:** `{dep_bonus}%` إضافي عند التعبئة!\n\n"
            f"اختر وسيلة الشحن المناسبة للبدء:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if data.startswith("dep_meth_id_"):
        m_id = int(data.replace("dep_meth_id_", ""))
        acc = conn.execute("SELECT * FROM deposit_methods WHERE id = ?", (m_id,)).fetchone()
        dep_bonus = conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"]
        conn.close()

        if not acc:
            return

        context.user_data["dep_method"] = acc["method_name"]
        context.user_data["dep_acc_details"] = acc["account_details"]
        
        conn.execute("UPDATE users SET step = 'deposit_step_amount' WHERE user_id = ?", (user.id,))
        conn.commit()

        msg = (
            f"💳 **طريقة الشحن:** {acc['method_name']}\n"
            f"🎁 **بونص الشحن المتاح:** `{dep_bonus}%` إضافي مجاناً!\n\n"
            f"✍️ **يرجى إدخال المبلغ الذي تريد شحنه (NSP):**"
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_deposit"))
        return

    # عرض طرق السحب وعمولة السحب
    if data == "btn_withdraw":
        min_w = conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"]
        w_comm = conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"]
        conn.close()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش", callback_data="w_meth_Syriatel Cash")],
            [InlineKeyboardButton("📱 إم تي إن كاش", callback_data="w_meth_MTN Cash")],
            [InlineKeyboardButton("💳 شام كاش", callback_data="w_meth_Bank Cham Cash")],
            [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]
        ])
        await query.message.edit_text(
            f"💸 **قسم سحب الأرباح:**\n\n"
            f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` NSP\n"
            f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NSP\n"
            f"➖ **عمولة السحب:** `{w_comm}%` خصم من المبلغ\n\n"
            f"اختر وسيلة السحب:",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    if data.startswith("w_meth_"):
        method = data.replace("w_meth_", "")
        w_comm = conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"]
        conn.close()

        context.user_data["withdraw_method"] = method
        conn.execute("UPDATE users SET step = 'withdraw_step_code' WHERE user_id = ?", (user.id,))
        conn.commit()

        msg = (
            f"💸 **وسيلة السحب:** {method}\n"
            f"➖ **عمولة السحب الإدارية:** `{w_comm}%`\n\n"
            f"✍️ **يرجى إدخال رقم الحساب/المحفظة التي ترغب بالتحويل إليها:**"
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_withdraw"))
        return

    # تأكيد طلب السحب وإرساله للإدارة
    if data == "confirm_withdraw_request":
        amt = context.user_data.get("withdraw_amount", 0.0)
        net_amt = context.user_data.get("withdraw_net", 0.0)
        method = context.user_data.get("withdraw_method", "غير محدد")
        acc_code = context.user_data.get("withdraw_code", "غير محدد")

        if amt <= 0 or amt > u["balance"]:
            conn.close()
            await query.message.edit_text("❌ رصيدك غير كافٍ أو حدث خطأ في العملية.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, user.id))
        cursor = conn.execute("INSERT INTO withdrawals (user_id, method, account_code, amount, net_amount) VALUES (?, ?, ?, ?, ?)",
                              (user.id, method, acc_code, amt, net_amt))
        conn.commit()
        w_id = cursor.lastrowid
        conn.close()

        await query.message.edit_text("✅ **تم تقديم طلب السحب بنجاح وهو قيد المراجعة لدى الإدارة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة 🔄", callback_data=f"rej_w_{w_id}")]])
        await notify_admins(context, f"📥 **طلب سحب جديد (# {w_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 المحفظة: `{acc_code}`\n💰 المبلغ الإجمالي: `{amt:,.2f}` NSP\n💵 الصافي للدفع: `{net_amt:,.2f}` NSP", reply_markup=kb)
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
        await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        return

    if data == "btn_buy_bot":
        conn.close()
        await query.message.edit_text("🤖 **لشراء بوتك وتطوير سيرفرك تواصل مع المبرمج:**\n\n📢 @lerafree", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية 🏠", callback_data="back_to_main")]]))
        return

    # ----------------------------------------------------
    # أزرار الإدارة الشاملة والإصلاحات
    # ----------------------------------------------------
    if is_admin:
        if data == "open_admin_panel":
            conn.close()
            await query.message.edit_text("⚙️ **لوحة التحكم الإدارية الشاملة:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_toggle_maint":
            curr = conn.execute("SELECT value FROM settings WHERE key='maintenance_mode'").fetchone()["value"]
            new_val = "0" if curr == "1" else "1"
            conn.execute("UPDATE settings SET value = ? WHERE key='maintenance_mode'", (new_val,))
            conn.commit()
            conn.close()
            await query.message.edit_text("⚙️ **تم تغيير حالة وضع الصيانة بنجاح.**", reply_markup=admin_panel_keyboard())
            return

        # قسم الإحالات النشطة والمستحقات في الإدارة
        if data == "adm_active_referrals":
            active_users = conn.execute("SELECT user_id, full_name, referrals_count, active_referrals_count, referral_mode FROM users WHERE active_referrals_count > 0 OR referrals_count > 0 OR referral_mode = 'burn'").fetchall()
            conn.close()

            if not active_users:
                await query.message.edit_text("🔥 **لا يوجد حسابات تمتلك إحالات نشطة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            msg = "🔥 **قائمة الحسابات التي لديها إحالات نشطة:**\n✨ ─────────────────── ✨\n"
            for u_item in active_users:
                mode_str = "الحرق (10%)" if u_item["referral_mode"] == "burn" else "اللفات المجانية"
                msg += f"👤 **الاسم:** {u_item['full_name']}\n🆔 **ID:** `{u_item['user_id']}`\n🎯 **النظام:** {mode_str}\n👥 **الإحالات:** `{u_item['referrals_count']}` | **النشطة:** `{u_item['active_referrals_count']}`\n───────────────\n"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ إضافة مستحقات إحالات للاعب 💰", callback_data="adm_add_ref_dues_input")],
                [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
            ])
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
            return

        if data == "adm_add_ref_dues_input":
            conn.execute("UPDATE users SET step = 'adm_input_add_ref_dues' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID اللاعب ثم مسافة ثم مبلغ المستحقات (NSP):**\n\nمثال:\n`7255100997 500`", reply_markup=cancel_keyboard("adm_active_referrals"))
            return

        # البونص الترحيبي
        if data == "adm_toggle_welcome":
            welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
            welcome_amt = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]
            conn.close()

            status_str = "🟢 مفعل" if welcome_enabled else "🔴 معطل"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تغيير حالة التفعيل / التعطيل", callback_data="adm_switch_welcome")],
                [InlineKeyboardButton("✏️ تعديل مبلغ البونص الترحيبي", callback_data="adm_set_welcome_amt")],
                [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
            ])
            await query.message.edit_text(
                f"🎁 **إدارة البونص الترحيبي:**\n\n"
                f"📌 **الحالة الحالية:** {status_str}\n"
                f"💰 **قيمة البونص:** `{welcome_amt}` NSP",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return

        if data == "adm_switch_welcome":
            curr = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"]
            new_val = "0" if curr == "1" else "1"
            conn.execute("UPDATE settings SET value = ? WHERE key='welcome_bonus_enabled'", (new_val,))
            conn.commit()
            
            if new_val == "1":
                welcome_amt = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]
                asyncio.create_task(notify_admins(context, f"🎁 **تم تفعيل البونص الترحيبي بقيمة `{welcome_amt}` NSP.**"))

            conn.close()
            await query.message.edit_text("✅ تم تغيير حالة البونص الترحيبي بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if data == "adm_set_welcome_amt":
            conn.execute("UPDATE users SET step = 'adm_input_welcome_amt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل المبلغ الجديد للبونص الترحيبي (NSP):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        # بونص الإحالة
        if data == "adm_toggle_ref_bonus":
            ref_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"] == "1"
            ref_amt = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]
            conn.close()

            status_str = "🟢 مفعل" if ref_enabled else "🔴 معطل"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تغيير حالة التفعيل / التعطيل", callback_data="adm_switch_ref")],
                [InlineKeyboardButton("✏️ تعديل مكافأة الإحالة", callback_data="adm_set_ref_amt")],
                [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
            ])
            await query.message.edit_text(
                f"🔗 **إدارة بونص الإحالة:**\n\n"
                f"📌 **الحالة الحالية:** {status_str}\n"
                f"💰 **مكافأة الإحالة:** `{ref_amt}` NSP",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return

        if data == "adm_switch_ref":
            curr = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"]
            new_val = "0" if curr == "1" else "1"
            conn.execute("UPDATE settings SET value = ? WHERE key='referral_reward_enabled'", (new_val,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم تغيير حالة بونص الإحالة بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if data == "adm_toggle_ref_spin":
            curr = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"]
            new_val = "0" if curr == "1" else "1"
            conn.execute("UPDATE settings SET value = ? WHERE key='referral_spin_enabled'", (new_val,))
            conn.commit()
            conn.close()
            status_text = "🟢 تم تفعيل منح اللفات المجانية للإحالة!" if new_val == "1" else "🔴 تم تعطيل اللفات المجانية للإحالة!"
            await query.message.edit_text(f"✅ **{status_text}**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if data == "adm_set_ref_amt":
            conn.execute("UPDATE users SET step = 'adm_input_ref_amt' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل قيمة مكافأة الإحالة الجديدة (NSP):**", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        # إدارة حسابات الشحن التفاعلية مع خيار التعديل المباشر
        if data == "adm_dep_methods":
            methods = conn.execute("SELECT * FROM deposit_methods").fetchall()
            conn.close()

            kb = [[InlineKeyboardButton("➕ إضافة وسيلة/حساب شحن جديد", callback_data="adm_add_dep_meth")]]
            for m in methods:
                kb.append([
                    InlineKeyboardButton(f"💳 {m['method_name']}: {m['account_details']}", callback_data="none"),
                    InlineKeyboardButton("✏️ تعديل الحساب", callback_data=f"edit_dep_meth_{m['id']}"),
                    InlineKeyboardButton("❌ حذف", callback_data=f"del_dep_meth_{m['id']}")
                ])
            kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

            await query.message.edit_text("💳 **لوحة إدارة وتعديل حسابات وسائل الشحن:**", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("edit_dep_meth_"):
            m_id = int(data.replace("edit_dep_meth_", ""))
            conn.execute("UPDATE users SET step = ? WHERE user_id = ?", (f"adm_edit_dep_acc_{m_id}", user.id))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل رقم أو بيانات الحساب الجديدة فوراً:**", reply_markup=cancel_keyboard("adm_dep_methods"))
            return

        if data == "adm_add_dep_meth":
            conn.execute("UPDATE users SET step = 'adm_input_add_dep_meth' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل تفاصيل الحساب الجديد بالشكل التالي:**\n`اسم الوسيلة | الرقم أو بيانات الحساب`\n\nمثال:\n`شام كاش | test`", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_dep_methods"))
            return

        if data.startswith("del_dep_meth_"):
            m_id = int(data.replace("del_dep_meth_", ""))
            conn.execute("DELETE FROM deposit_methods WHERE id = ?", (m_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم حذف وسيلة الشحن بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة إدارة الشحن 💳", callback_data="adm_dep_methods")]]))
            return

        # خوارزمية العجلة
        if data == "adm_wheel_algo":
            probs = dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'wheel_prob_%'").fetchall())
            conn.close()

            items = [
                ("🍀 حظ أوفر", "wheel_prob_luck"),
                ("💰 5 NSP", "wheel_prob_5"),
                ("💰 10 NSP", "wheel_prob_10"),
                ("💰 15 NSP", "wheel_prob_15"),
                ("🔄 حاول مجدداً", "wheel_prob_try_again"),
                ("💰 25 NSP", "wheel_prob_25"),
                ("💰 50 NSP", "wheel_prob_50"),
                ("💰 100 NSP", "wheel_prob_100"),
                ("💰 250 NSP", "wheel_prob_250"),
                ("🎁 بونص شحن 20%", "wheel_prob_dep_bonus_20"),
                ("💎 500 NSP", "wheel_prob_500"),
                ("👑 1000 NSP", "wheel_prob_1000")
            ]

            kb = []
            for label, key_name in items:
                curr_p = probs.get(key_name, "0")
                kb.append([InlineKeyboardButton(f"{label} ({curr_p}%)", callback_data=f"set_wprob_{key_name}")])
            
            kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

            await query.message.edit_text("🎡 **خوارزمية العجلة التفاعلية (اضغط على أي زر لتغيير نسبته):**", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("set_wprob_"):
            target_key = data.replace("set_wprob_", "")
            context.user_data["edit_wheel_key"] = target_key
            conn.execute("UPDATE users SET step = 'adm_input_wheel_prob_val' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"✍️ **أدخل النسبة المئوية الجديدة لـ `{target_key}`:**", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_wheel_algo"))
            return

        if data == "adm_user_boost":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 تحديد حظ خاص للاعب", callback_data="adm_set_boost_user")],
                [InlineKeyboardButton("❌ إلغاء حظ لاعب معين", callback_data="adm_clear_boost_user")],
                [InlineKeyboardButton("🌐 إلغاء الحظ الخاص للجميع", callback_data="adm_clear_boost_all")],
                [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
            ])
            await query.message.edit_text("🎯 **لوحة التحكم بحظ اللاعبين المخصص:**", reply_markup=kb)
            conn.close()
            return

        if data == "adm_set_boost_user":
            conn.execute("UPDATE users SET step = 'adm_input_user_boost' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID اللاعب ثم مسافة ثم النسبة المئوية للحظ (مثال: `7255100997 50`):**", reply_markup=cancel_keyboard("adm_user_boost"))
            return

        if data == "adm_clear_boost_user":
            conn.execute("UPDATE users SET step = 'adm_input_clear_boost' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل ID اللاعب لإلغاء الحظ الخاص عنه:**", reply_markup=cancel_keyboard("adm_user_boost"))
            return

        if data == "adm_clear_boost_all":
            conn.execute("UPDATE users SET custom_boost = 0.0")
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم إلغاء الحظ الخاص عن جميع اللاعبين بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if data == "adm_channels_menu":
            chans = conn.execute("SELECT * FROM channels").fetchall()
            conn.close()

            kb = [[InlineKeyboardButton("➕ إضافة قناة اشتراك صارمة", callback_data="adm_add_chan")]]
            for c in chans:
                kb.append([
                    InlineKeyboardButton(f"📢 {c['channel_title']}", url=c['channel_link']),
                    InlineKeyboardButton("❌ حذف", callback_data=f"del_chan_{c['channel_id']}")
                ])
            kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

            await query.message.edit_text("📢 **إدارة قنوات الاشتراك الإجباري والصارم:**", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data == "adm_add_chan":
            conn.execute("UPDATE users SET step = 'adm_input_add_channel' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل بيانات القناة بالشكل التالي:**\n`معرف_القناة | عنوان_القناة | رابط_القناة`\n\nمثال:\n`@MyChan | قناة الأخبار | https://t.me/MyChan`", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_channels_menu"))
            return

        if data.startswith("del_chan_"):
            c_id = data.replace("del_chan_", "")
            conn.execute("DELETE FROM channels WHERE channel_id = ?", (c_id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✅ تم حذف القناة بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة القنوات 📢", callback_data="adm_channels_menu")]]))
            return

        # قبول الشحن تلقائياً مع زيادة البونص والإشعار
        if data == "adm_deposits":
            deps = conn.execute("SELECT d.*, u.full_name FROM deposits d JOIN users u ON d.user_id = u.user_id WHERE d.status = 'pending' ORDER BY d.id DESC LIMIT 10").fetchall()
            conn.close()

            if not deps:
                await query.message.edit_text("📥 **لا توجد طلبات شحن معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            await query.message.edit_text("📥 **قائمة طلبات الشحن المعلقة:**", parse_mode="Markdown")
            for d in deps:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{d['id']}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{d['id']}")]])
                txt = f"💳 **طلب شحن (# {d['id']}):**\n👤 {d['full_name']} (`{d['user_id']}`)\n💰 المبلغ: `{d['amount']}` NSP\n📌 الطريقة: {d['method']}\n🔢 الإشعار: `{d['tx_id']}`"
                await context.bot.send_message(chat_id=query.message.chat_id, text=txt, parse_mode="Markdown", reply_markup=kb)
            return

        if data.startswith("app_dep_"):
            dep_id = int(data.replace("app_dep_", ""))
            dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
            if dep and dep["status"] == "pending":
                bonus_pct = float(conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"])
                bonus_amount = dep["amount"] * (bonus_pct / 100.0)
                total_credit = dep["amount"] + bonus_amount
                
                conn.execute("UPDATE deposits SET status = 'approved' WHERE id = ?", (dep_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total_credit, dep["user_id"]))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (dep["user_id"], f"شحن رصيد ناجح #{dep_id}", total_credit))
                conn.commit()

                await query.edit_message_text(f"✅ **تمت الموافقة على الشحن #{dep_id} وإضافة `{total_credit:,.2f}` NSP للعميل تلقائياً مع البونص.**")
                try:
                    if bonus_amount > 0:
                        notify_msg = (
                            f"🎉 **تمت الموافقة على طلب الشحن الخاص بك!**\n\n"
                            f"💳 **المبلغ المشحون:** `{dep['amount']:,.2f}` NSP\n"
                            f"🎁 **البونص الإضافي ({bonus_pct}%):** `{bonus_amount:,.2f}` NSP\n"
                            f"💰 **إجمالي الرصيد المضاف:** `{total_credit:,.2f}` NSP"
                        )
                    else:
                        notify_msg = (
                            f"🎉 **تمت الموافقة على طلب الشحن الخاص بك!**\n\n"
                            f"💰 **تم إضافة:** `{total_credit:,.2f}` NSP إلى رصيدك."
                        )
                    await context.bot.send_message(dep["user_id"], notify_msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Failed to notify user on deposit approval: {e}")
            conn.close()
            return

        if data.startswith("rej_dep_"):
            dep_id = int(data.replace("rej_dep_", ""))
            dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
            if dep and dep["status"] == "pending":
                conn.execute("UPDATE deposits SET status = 'rejected' WHERE id = ?", (dep_id,))
                conn.commit()
                await query.edit_message_text(f"❌ **تم رفض طلب الشحن #{dep_id}.**")
                try: await context.bot.send_message(dep["user_id"], "❌ **تم رفض طلب الشحن الخاص بك.**")
                except: pass
            conn.close()
            return

        if data == "adm_withdraws":
            ws = conn.execute("SELECT w.*, u.full_name FROM withdrawals w JOIN users u ON w.user_id = u.user_id WHERE w.status = 'pending' ORDER BY w.id DESC LIMIT 10").fetchall()
            conn.close()

            if not ws:
                await query.message.edit_text("💸 **لا توجد طلبات سحب معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            await query.message.edit_text("💸 **قائمة طلبات السحب المعلقة:**", parse_mode="Markdown")
            for w in ws:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w['id']}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد 🔄", callback_data=f"rej_w_{w['id']}")]])
                txt = f"💸 **طلب سحب (# {w['id']}):**\n👤 {w['full_name']} (`{w['user_id']}`)\n💳 الطريقة: {w['method']}\n🔢 الحساب/المحفظة: `{w['account_code']}`\n💰 الصافي للدفع: `{w['net_amount']:,.2f}` NSP"
                await context.bot.send_message(chat_id=query.message.chat_id, text=txt, parse_mode="Markdown", reply_markup=kb)
            return

        if data.startswith("app_w_"):
            w_id = int(data.replace("app_w_", ""))
            w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
            if w and w["status"] == "pending":
                conn.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (w["user_id"], f"سحب رصيد ناجح #{w_id}", -w["amount"]))
                conn.commit()
                await query.edit_message_text(f"✅ **تمت الموافقة على السحب #{w_id}.**")
                try: await context.bot.send_message(w["user_id"], f"🎉 **تمت الموافقة على طلب السحب الخاص بك بمبلغ `{w['net_amount']:,.2f}` NSP!**")
                except: pass
            conn.close()
            return

        if data.startswith("rej_w_"):
            w_id = int(data.replace("rej_w_", ""))
            w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
            if w and w["status"] == "pending":
                conn.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w["amount"], w["user_id"]))
                conn.commit()
                await query.edit_message_text(f"❌ **تم رفض طلب السحب #{w_id} وإعادة الرصيد للعميل.**")
                try: await context.bot.send_message(w["user_id"], f"❌ **تم رفض طلب السحب وإعادة `{w['amount']:,.2f}` NSP إلى رصيدك.**")
                except: pass
            conn.close()
            return

        if data == "adm_active_codes":
            codes = conn.execute("SELECT code, amount, uses_left FROM gift_codes WHERE uses_left > 0").fetchall()
            conn.close()

            if not codes:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎫 إنشاء كود فردي", callback_data="adm_make_gift")],
                    [InlineKeyboardButton("🎟️ توليد دفعة أكواد", callback_data="adm_batch_codes")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ])
                await query.message.edit_text("🎟️ **لا توجد أكواد هدايا نشطة حالياً.**", reply_markup=kb)
                return

            kb = []
            txt = "🎟️ **قائمة الأكواد النشطة المتاحة:**\n\n"
            for c in codes:
                txt += f"• الكود: `{c['code']}` | المبلغ: `{c['amount']}` NSP | المتبقي: `{c['uses_left']}`\n"
                kb.append([
                    InlineKeyboardButton(f"🎫 {c['code']} ({c['amount']} NSP)", callback_data="none"),
                    InlineKeyboardButton("❌ إلغاء", callback_data=f"del_code_{c['code']}")
                ])
            
            kb.append([InlineKeyboardButton("🗑️ إلغاء كود يدوي (كتابة)", callback_data="adm_del_code_manual")])
            kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("del_code_"):
            code_str = data.replace("del_code_", "")
            conn.execute("DELETE FROM gift_codes WHERE code = ?", (code_str,))
            conn.commit()
            conn.close()
            await query.message.edit_text(f"✅ تم إلغاء وحذف الكود `{code_str}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الأكواد 🎟️", callback_data="adm_active_codes")]]))
            return

        if data == "adm_del_code_manual":
            conn.execute("UPDATE users SET step = 'adm_input_del_code_manual' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("✍️ **أدخل الكود المراد إلغاؤه وحذفه:**", reply_markup=cancel_keyboard("adm_active_codes"))
            return

        if data == "adm_list_admins":
            adms = conn.execute("SELECT user_id FROM admins").fetchall()
            conn.close()
            txt = "👮 **قائمة مدراء النظام (الأدمنية):**\n\n"
            for a in adms:
                txt += f"• ID: `{a['user_id']}`\n"
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

        if data == "adm_stats":
            u_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            tot_dep = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status = 'approved'").fetchone()[0]
            tot_w = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'approved'").fetchone()[0]
            tot_spent = conn.execute("SELECT COALESCE(SUM(total_spent), 0) FROM users").fetchone()[0]
            conn.close()

            txt = (
                f"📊 **إحصائيات المنصة الشاملة:**\n"
                f"✨ ─────────────────── ✨\n"
                f"👥 **إجمالي المستخدمين:** `{u_count}` لاعب\n"
                f"💳 **إجمالي الشحن الناجح:** `{tot_dep:,.2f}` NSP\n"
                f"💸 **إجمالي السحب الناجح:** `{tot_w:,.2f}` NSP\n"
                f"🎰 **إجمالي المصروف باللعب:** `{tot_spent:,.2f}` NSP\n"
                f"✨ ─────────────────── ✨"
            )
            await query.message.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

    conn.close()

# ----------------------------------------------------
# 10. تشغيل البوت
# ----------------------------------------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Bot started successfully...")
    application.run_polling()

if __name__ == "__main__":
    main()
