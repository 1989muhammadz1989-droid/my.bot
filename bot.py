import os
import sys
import random
import string
import logging
import asyncio
import re
import json
import time
import sqlite3

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
# 1. إعدادات التسجيل والبيئة والتوكن
# ----------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8867057441:AAH1CFeEJl8LJft-C9oC9DNfU_gtyD-H7EU")
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "7255100997"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

# ----------------------------------------------------
# 2. حماية ضد ضغط الأزرار المتكرر والسريع (Anti-Spam Rate Limiter)
# ----------------------------------------------------
USER_LAST_CLICK = {}
RATE_LIMIT_INTERVAL = 0.6  # ثواني بين الضغطات

def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last_time = USER_LAST_CLICK.get(user_id, 0)
    if now - last_time < RATE_LIMIT_INTERVAL:
        return True
    USER_LAST_CLICK[user_id] = now
    return False

# ----------------------------------------------------
# 3. الربط المباشر بقاعدة بيانات SQLite الموحدة (database.db)
# ----------------------------------------------------
DB_FILE = "database.db"

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. جدول المستخدمين الموحد مع تطبيق حقول البوت والموقع
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            balance REAL DEFAULT 0.0,
            free_spins INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0.0,
            referred_by INTEGER,
            referrals_count INTEGER DEFAULT 0,
            active_referrals_count INTEGER DEFAULT 0,
            referral_mode TEXT,
            is_verified INTEGER DEFAULT 0,
            phone_verified INTEGER DEFAULT 0,
            welcome_bonus_claimed INTEGER DEFAULT 0,
            referral_credited INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            captcha_answer TEXT,
            step TEXT DEFAULT 'start',
            custom_boost REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # التأكد من وجود كافة الأعمدة المطلوبة للبوت بأمان
    existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()]
    cols_to_add = [
        ("referred_by", "INTEGER"),
        ("referrals_count", "INTEGER DEFAULT 0"),
        ("active_referrals_count", "INTEGER DEFAULT 0"),
        ("referral_mode", "TEXT"),
        ("is_verified", "INTEGER DEFAULT 0"),
        ("phone_verified", "INTEGER DEFAULT 0"),
        ("welcome_bonus_claimed", "INTEGER DEFAULT 0"),
        ("referral_credited", "INTEGER DEFAULT 0"),
        ("is_banned", "INTEGER DEFAULT 0"),
        ("captcha_answer", "TEXT"),
        ("step", "TEXT DEFAULT 'start'"),
        ("custom_boost", "REAL DEFAULT 0.0")
    ]
    for col_name, col_type in cols_to_add:
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                logger.warning(f"Note on adding {col_name}: {e}")

    # 2. جدول الإعدادات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # 3. جدول المسؤولين الأدمنية
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)

    # 4. جدول أكواد الهدايا
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount REAL,
            uses_left INTEGER
        )
    """)

    # 5. جدول القنوات الإجبارية
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_title TEXT,
            channel_link TEXT
        )
    """)

    # 6. جدول طرق الشحن
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deposit_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            method_name TEXT,
            account_details TEXT
        )
    """)

    # 7. جدول طلبات الشحن
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            amount REAL,
            tx_id TEXT,
            photo_file_id TEXT,
            status TEXT DEFAULT 'pending',
            timestamp TEXT,
            channel_msg_id INTEGER DEFAULT 0
        )
    """)

    # 8. جدول طلبات السحب
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            acc_code TEXT,
            amount REAL,
            net_amount REAL,
            fee REAL,
            status TEXT DEFAULT 'pending',
            timestamp TEXT,
            channel_msg_id INTEGER DEFAULT 0
        )
    """)

    # 9. جدول السجلات والعمليات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            action TEXT,
            amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 10. جدول العروض
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            link TEXT
        )
    """)

    # 11. جدول تقييدات الأكواد الزمانية
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_restrictions (
            user_id INTEGER PRIMARY KEY,
            last_used INTEGER
        )
    """)

    # تعبئة الإعدادات الافتراضية
    defaults = [
        ('maintenance_mode', '1'),
        ('welcome_bonus', '50'),
        ('welcome_bonus_enabled', '1'),
        ('referral_reward', '50'),
        ('referral_reward_enabled', '0'),
        ('referral_spin_enabled', '1'),
        ('deposit_bonus_percent', '0'),
        ('withdraw_commission_percent', '0'),
        ('min_withdraw', '1500'),
        ('min_deposit', '50'),
        ('tx_channel_id', ''),
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
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))

    if DEFAULT_ADMIN_ID and DEFAULT_ADMIN_ID != 0:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))

    # طرق الشحن الافتراضية
    cursor.execute("SELECT COUNT(*) FROM deposit_methods")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO deposit_methods (id, method_name, account_details) VALUES (1, 'شام كاش', 'a612b862fc9960e61333d48b86d0dcd3')")
        cursor.execute("INSERT INTO deposit_methods (id, method_name, account_details) VALUES (2, 'سيريتل كاش', '00973427')")

    # القنوات الافتراضية
    req_channels = [
        ('@lerafree', 'قناة المبرمج', 'https://t.me/lerafree'),
        ('@golden_game_b', 'قناة البوت', 'https://t.me/golden_game_b'),
        ('@goldennlera', 'كروب المسابقات', 'https://t.me/goldennlera')
    ]
    for ch_id, ch_title, ch_link in req_channels:
        cursor.execute("INSERT OR IGNORE INTO channels (channel_id, channel_title, channel_link) VALUES (?, ?, ?)", (ch_id, ch_title, ch_link))

    conn.commit()
    conn.close()

init_db()

# المرجع الرئيسي لـ Asyncio Event Loop
bot_loop = None
tg_app = None

def run_coro_in_bot_loop(coro):
    if bot_loop and bot_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return None

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def is_maintenance_active() -> bool:
    return get_setting("maintenance_mode", "1") == "1"

def is_admin_user(user_id: int) -> bool:
    if DEFAULT_ADMIN_ID and user_id == DEFAULT_ADMIN_ID:
        return True
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_user(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def save_user(user_data: dict):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (
            user_id, full_name, phone, balance, free_spins, games_played, total_spent,
            referred_by, referrals_count, active_referrals_count, referral_mode,
            is_verified, phone_verified, welcome_bonus_claimed, referral_credited,
            is_banned, captcha_answer, step, custom_boost, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, CURRENT_TIMESTAMP
        )
    """, (
        user_data["user_id"],
        user_data.get("full_name", "لاعب"),
        user_data.get("phone", ""),
        float(user_data.get("balance", 0.0)),
        int(user_data.get("free_spins", 0)),
        int(user_data.get("games_played", 0)),
        float(user_data.get("total_spent", 0.0)),
        user_data.get("referred_by"),
        int(user_data.get("referrals_count", 0)),
        int(user_data.get("active_referrals_count", 0)),
        user_data.get("referral_mode"),
        int(user_data.get("is_verified", 0)),
        int(user_data.get("phone_verified", 0)),
        int(user_data.get("welcome_bonus_claimed", 0)),
        int(user_data.get("referral_credited", 0)),
        int(user_data.get("is_banned", 0)),
        user_data.get("captcha_answer", ""),
        user_data.get("step", "start"),
        float(user_data.get("custom_boost", 0.0))
    ))
    conn.commit()
    conn.close()

def create_user(user_id: int, full_name: str, referred_by=None, step="start"):
    existing = get_user(user_id)
    if existing:
        return existing
    u = {
        "user_id": user_id,
        "full_name": full_name or "لاعب",
        "phone": "",
        "balance": 0.0,
        "free_spins": 0,
        "games_played": 0,
        "total_spent": 0.0,
        "referred_by": referred_by,
        "referrals_count": 0,
        "active_referrals_count": 0,
        "referral_mode": None,
        "is_verified": 0,
        "phone_verified": 0,
        "welcome_bonus_claimed": 0,
        "referral_credited": 0,
        "is_banned": 0,
        "captcha_answer": "",
        "step": step,
        "custom_boost": 0.0
    }
    save_user(u)
    return u

def add_log(user_id: int, action: str, amount: float = 0.0):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                   (str(user_id), action, float(amount)))
    conn.commit()
    conn.close()

# ----------------------------------------------------
# 4. إشعارات القناة والتحقق
# ----------------------------------------------------
async def is_bot_admin_in_channel(bot, channel_id_or_username: str) -> bool:
    try:
        if not channel_id_or_username:
            return False
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=channel_id_or_username, user_id=me.id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        logger.warning(f"Bot status check in channel '{channel_id_or_username}' failed: {e}")
        return False

async def send_deposit_to_channel(bot, dep_id: int, user, method: str, amount: float, tx_id: str, photo_file_id: str = None):
    try:
        ch_id = get_setting("tx_channel_id", "").strip()
        if not ch_id or not await is_bot_admin_in_channel(bot, ch_id):
            return 0

        caption = (
            f"📥 **طلب شحن جديد (# {dep_id})** ⚡\n"
            f"✨ ─────────────────── ✨\n"
            f"👤 **اللاعب:** {user.full_name}\n"
            f"🆔 **المعرف:** `{user.id}`\n"
            f"💳 **وسيلة الشحن:** {method}\n"
            f"💰 **المبلغ المطلوب:** `{amount:,.2f}` NPS\n"
            f"📝 **الإشعار/العملية:** `{tx_id}`\n"
            f"⏳ **الحالة:** قيد المراجعة\n"
            f"✨ ─────────────────── ✨"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"),
                InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")
            ]
        ])
        if photo_file_id:
            msg = await bot.send_photo(chat_id=ch_id, photo=photo_file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            msg = await bot.send_message(chat_id=ch_id, text=caption, parse_mode="Markdown", reply_markup=kb)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE deposits SET channel_msg_id = ? WHERE id = ?", (msg.message_id, dep_id))
        conn.commit()
        conn.close()

        return msg.message_id
    except Exception as e:
        logger.error(f"Error sending deposit notice to channel: {e}")
    return 0

async def notify_channel_deposit_status(bot, dep: dict, status: str):
    try:
        ch_id = get_setting("tx_channel_id", "").strip()
        if not ch_id or not dep.get("channel_msg_id"):
            return
        status_text = "✅ **تمت الموافقة على الشحن وتعبئة الرصيد بنجاح!**" if status == "approved" else "❌ **تم رفض طلب الشحن.**"
        try:
            await bot.send_message(
                chat_id=ch_id,
                text=f"{status_text}\n📋 طلب رقم #{dep['id']} للعميل `{dep['user_id']}`",
                reply_to_message_id=dep["channel_msg_id"],
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send deposit channel reply: {e}")
    except Exception as e:
        logger.error(f"Error in notify_channel_deposit_status: {e}")

async def send_withdraw_to_channel(bot, w_id: int, user, method: str, acc_code: str, amount: float, net_amount: float):
    try:
        ch_id = get_setting("tx_channel_id", "").strip()
        if not ch_id or not await is_bot_admin_in_channel(bot, ch_id):
            return 0

        caption = (
            f"💸 **طلب سحب جديد (# {w_id})** 🪙\n"
            f"✨ ─────────────────── ✨\n"
            f"👤 **اللاعب:** {user.full_name}\n"
            f"🆔 **المعرف:** `{user.id}`\n"
            f"💳 **وسيلة السحب:** {method}\n"
            f"🔢 **الحساب/المحفظة:** `{acc_code}`\n"
            f"💰 **المبلغ المطلوب:** `{amount:,.2f}` NPS\n"
            f"💵 **الصافي للدفع:** `{net_amount:,.2f}` NPS\n"
            f"⏳ **الحالة:** قيد المراجعة\n"
            f"✨ ─────────────────── ✨"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w_id}"),
                InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_w_{w_id}")
            ]
        ])
        msg = await bot.send_message(chat_id=ch_id, text=caption, parse_mode="Markdown", reply_markup=kb)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE withdrawals SET channel_msg_id = ? WHERE id = ?", (msg.message_id, w_id))
        conn.commit()
        conn.close()

        return msg.message_id
    except Exception as e:
        logger.error(f"Error sending withdraw notice to channel: {e}")
    return 0

async def notify_channel_withdrawal_status(bot, w: dict, status: str):
    try:
        ch_id = get_setting("tx_channel_id", "").strip()
        if not ch_id or not w.get("channel_msg_id"):
            return
        status_text = "✅ **تمت الموافقة على السحب وتحويل الأرباح!**" if status == "approved" else "❌ **تم رفض طلب السحب وإعادة الرصيد للعميل.**"
        try:
            await bot.send_message(
                chat_id=ch_id,
                text=f"{status_text}\n📋 طلب رقم #{w['id']} للعميل `{w['user_id']}`",
                reply_to_message_id=w["channel_msg_id"],
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send withdraw channel reply: {e}")
    except Exception as e:
        logger.error(f"Error in notify_channel_withdrawal_status: {e}")

# ----------------------------------------------------
# 5. التفاعل والإشعارات والاشتراك الإجباري
# ----------------------------------------------------
async def send_start_reaction(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        reactions = ["🔥", "⚡", "🌙", "👑"]
        selected_emoji = random.choice(reactions)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[{"type": "emoji", "emoji": selected_emoji}]
        )
    except Exception as e:
        logger.warning(f"Reaction failed: {e}")

async def check_user_channels_subscription(bot, user_id: int) -> tuple[bool, list]:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels")
        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()

        if not channels:
            return True, []

        unsubscribed = []
        for ch in channels:
            try:
                member = await bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
                if member.status in ['left', 'kicked']:
                    unsubscribed.append(ch)
            except Exception as e:
                logger.warning(f"Failed to check membership for channel {ch['channel_id']}: {e}")

        return (len(unsubscribed) == 0), unsubscribed
    except Exception as e:
        logger.error(f"Error checking channel sub: {e}")
        return True, []

def build_sub_keyboard(unsubscribed_channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in unsubscribed_channels:
        title = ch.get("channel_title") or "📢 قناة الاشتراك الإجباري"
        keyboard.append([InlineKeyboardButton(f"🔗 {title}", url=ch["channel_link"])])
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الآن", callback_data="check_subscription_status")])
    return InlineKeyboardMarkup(keyboard)

def cancel_keyboard(target="back_to_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية والعودة", callback_data=target)]])

async def notify_admins(context_or_bot, text: str, reply_markup=None):
    try:
        bot = context_or_bot.bot if hasattr(context_or_bot, "bot") else context_or_bot
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        adm_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        for adm_id in adm_ids:
            try:
                await bot.send_message(chat_id=adm_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error notifying admins: {e}")

async def process_referral_on_captcha_passed(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        u = get_user(user_id)
        if not u or not u.get("referred_by") or u.get("referral_credited") == 1:
            return

        ref_id = u["referred_by"]
        ref_reward_enabled = get_setting("referral_reward_enabled", "0") == "1"
        ref_reward_amt = float(get_setting("referral_reward", "0")) if ref_reward_enabled else 0.0
        ref_spin_enabled = get_setting("referral_spin_enabled", "1") == "1"

        ref_user = get_user(ref_id)
        if ref_user:
            ref_user["referrals_count"] = ref_user.get("referrals_count", 0) + 1
            ref_user["active_referrals_count"] = ref_user.get("active_referrals_count", 0) + 1
            if ref_spin_enabled:
                ref_user["free_spins"] = ref_user.get("free_spins", 0) + 1
            if ref_reward_amt > 0:
                ref_user["balance"] = ref_user.get("balance", 0.0) + ref_reward_amt
                add_log(ref_id, f"مكافأة إحالة العميل {user_id}", ref_reward_amt)
            save_user(ref_user)

        u["referral_credited"] = 1
        save_user(u)

        try:
            ref_text = f"🎉 **إحالة ناجحة جديدة!**\n👤 **اللاعب:** {u['full_name']}\n🆔 **المعرف:** `{user_id}`\n✅ **اجتاز العميل سؤال الأمان بنجاح وتم احتساب الإحالة لحسابك!**"
            if ref_spin_enabled:
                ref_text += "\n🎡 **حصلت على 1 لفة مجانية!**"
            if ref_reward_amt > 0:
                ref_text += f"\n💰 **حصلت على `{ref_reward_amt}` NPS مكافأة!**"

            await context.bot.send_message(chat_id=ref_id, text=ref_text, parse_mode="Markdown")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error processing referral: {e}")

async def process_welcome_bonus(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        u = get_user(user_id)
        if not u:
            return

        if u.get("welcome_bonus_claimed", 0) == 0:
            welcome_enabled = get_setting("welcome_bonus_enabled", "1") == "1"
            welcome_bonus = float(get_setting("welcome_bonus", "50")) if welcome_enabled else 0.0

            u["welcome_bonus_claimed"] = 1
            if welcome_bonus > 0:
                u["balance"] = u.get("balance", 0.0) + welcome_bonus
                add_log(user_id, "بونص ترحيبي بعد التثبت من القنوات", welcome_bonus)
                save_user(u)
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🎁 **مبارك! حصلت على البونص الترحيبي قدره `{welcome_bonus}` NPS لإتمام الانضمام بنجاح!**",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            else:
                save_user(u)
    except Exception as e:
        logger.error(f"Error processing welcome bonus: {e}")

# ----------------------------------------------------
# 6. القوائم ولوحات التحكم مع خوارزمية العجلة المكتملة
# ----------------------------------------------------
def main_menu_keyboard(is_admin=False):
    games_url = f"{SERVER_URL}/games"
    wheel_url = f"{SERVER_URL}/wheel"
    
    keyboard = [
        [InlineKeyboardButton("🔴 🥊 💎 دخول موقع الألعاب | Golden Games 🎰 🥊 🔴", web_app=WebAppInfo(url=games_url))],
        [InlineKeyboardButton("🟢 🎡 عجلة الحظ الكبرى 🎯 🟢", web_app=WebAppInfo(url=wheel_url))],
        [InlineKeyboardButton("🔴 🎁 العروض الحالية 🔥", callback_data="btn_offers"), InlineKeyboardButton("🚨 💳 شحن حسابك ⚡", callback_data="btn_deposit")],
        [InlineKeyboardButton("🥊 💸 سحب الأرباح 🪙", callback_data="btn_withdraw"), InlineKeyboardButton("📌 👤 ملف الحساب 📊", callback_data="btn_account")],
        [InlineKeyboardButton("🎯 🔗 رابط الإحالة 🚀", callback_data="btn_referral"), InlineKeyboardButton("🏮 📸 إرسال إثبات الفوز 🏆", callback_data="btn_send_proof")],
        [InlineKeyboardButton("🧨 🎟️ استخدام كود هدية 🎁", callback_data="btn_gift"), InlineKeyboardButton("🍷 🤖 طلب بوت خاص ⚙️", callback_data="btn_buy_bot")],
        [InlineKeyboardButton("🛑 📜 سجل العمليات 📑", callback_data="btn_logs"), InlineKeyboardButton("🌶️ 💬 الدعم الفني المباشر 👨‍💻", callback_data="btn_support")],
        [InlineKeyboardButton("🎒 📢 قناة المبرمج 🚀", url="https://t.me/lerafree")]
    ]
    if is_admin:
        keyboard.insert(0, [InlineKeyboardButton("🛑 ⚙️ لوحة الإدارة العليا 👮‍♂️", callback_data="open_admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_wheel_algo_keyboard():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings WHERE key LIKE 'wheel_prob_%'")
    probs = {row['key']: row['value'] for row in cursor.fetchall()}
    conn.close()

    def p(k, default="0"):
        return probs.get(k, default)

    keyboard = [
        [InlineKeyboardButton(f"💔 حظ أوفر: {p('wheel_prob_luck', '25')}%", callback_data="adm_set_wheel_prob_luck")],
        [InlineKeyboardButton(f"🔄 حاول مرة أخرى: {p('wheel_prob_try_again', '15')}%", callback_data="adm_set_wheel_prob_try_again")],
        [InlineKeyboardButton(f"🎁 5 NPS: {p('wheel_prob_5', '20')}%", callback_data="adm_set_wheel_prob_5"), InlineKeyboardButton(f"🎁 10 NPS: {p('wheel_prob_10', '15')}%", callback_data="adm_set_wheel_prob_10")],
        [InlineKeyboardButton(f"🎁 15 NPS: {p('wheel_prob_15', '10')}%", callback_data="adm_set_wheel_prob_15"), InlineKeyboardButton(f"🎁 25 NPS: {p('wheel_prob_25', '7')}%", callback_data="adm_set_wheel_prob_25")],
        [InlineKeyboardButton(f"🎁 50 NPS: {p('wheel_prob_50', '4')}%", callback_data="adm_set_wheel_prob_50"), InlineKeyboardButton(f"🎁 100 NPS: {p('wheel_prob_100', '2')}%", callback_data="adm_set_wheel_prob_100")],
        [InlineKeyboardButton(f"🎁 250 NPS: {p('wheel_prob_250', '1')}%", callback_data="adm_set_wheel_prob_250"), InlineKeyboardButton(f"🎁 بونص 20%: {p('wheel_prob_dep_bonus_20', '0.8')}%", callback_data="adm_set_wheel_prob_dep_bonus_20")],
        [InlineKeyboardButton(f"🎁 500 NPS: {p('wheel_prob_500', '0.15')}%", callback_data="adm_set_wheel_prob_500"), InlineKeyboardButton(f"🎁 1000 NPS: {p('wheel_prob_1000', '0.05')}%", callback_data="adm_set_wheel_prob_1000")],
        [InlineKeyboardButton("🔙 لوحة الإدارة العليا ⚙️", callback_data="open_admin_panel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_panel_keyboard():
    try:
        maint_status = "🔴 مفعل (الصيانة مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
        
        welcome_enabled = get_setting("welcome_bonus_enabled", "1") == "1"
        welcome_status = "🟢 مفعل" if welcome_enabled else "🔴 معطل"
        welcome_amt = get_setting("welcome_bonus", "50")
        
        ref_enabled = get_setting("referral_reward_enabled", "0") == "1"
        ref_status = "🟢 مفعل" if ref_enabled else "🔴 معطل"
        ref_amt = get_setting("referral_reward", "50")

        ref_spin_enabled = get_setting("referral_spin_enabled", "1") == "1"
        ref_spin_status = "🟢 مفعل" if ref_spin_enabled else "🔴 معطل"

        tx_ch_val = get_setting("tx_channel_id", "") or "غير محددة"
    except Exception:
        maint_status, welcome_status, welcome_amt, ref_status, ref_amt, ref_spin_status, tx_ch_val = "غير معروف", "غير معروف", "50", "غير معروف", "50", "غير معروف", "غير محددة"

    keyboard = [
        [InlineKeyboardButton(f"🛠️ وضع الصيانة: {maint_status}", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🎡 خوارزمية عجلة الحظ (تعديل النسب) 🎯", callback_data="adm_wheel_algo"), InlineKeyboardButton("🎯 حظ لاعب معين ⚡", callback_data="adm_user_boost")],
        [InlineKeyboardButton(f"🎁 الترحيبي ({welcome_amt} NPS): {welcome_status}", callback_data="adm_toggle_welcome"), InlineKeyboardButton("✏️ تعديل سعر الترحيبي ✏️", callback_data="adm_set_welcome_amt")],
        [InlineKeyboardButton(f"🔗 الإحالة ({ref_amt} NPS): {ref_status}", callback_data="adm_toggle_ref_bonus"), InlineKeyboardButton("✏️ تعديل سعر الإحالة ✏️", callback_data="adm_set_ref_amt")],
        [InlineKeyboardButton(f"🎡 لفة الإحالة المجانية: {ref_spin_status}", callback_data="adm_toggle_ref_spin")],
        [InlineKeyboardButton(f"📢 قناة الشحن والسحب: {tx_ch_val}", callback_data="adm_set_tx_channel")],
        [InlineKeyboardButton("👥 الإحالات النشطة والمستحقات 📊", callback_data="adm_active_referrals")],
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
# 7. المعالجات والأوامر الرئيسية
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        chat_id = update.effective_chat.id

        await send_start_reaction(context, chat_id, update.message.message_id)

        if is_maintenance_active() and not is_admin_user(user.id):
            await update.message.reply_text("🛠️ **السيرفر حالياً في حالة صيانة وتحديثات دورية.**\nيرجى المحاولة لاحقاً.")
            return

        u = get_user(user.id)
        is_admin = is_admin_user(user.id)

        if u and u.get("is_banned"):
            await update.message.reply_text("❌ حسابك محظور من استخدام السيرفر.")
            return

        if not u:
            ref_id = None
            if context.args and context.args[0].isdigit():
                ref_id = int(context.args[0])
                if ref_id == user.id:
                    ref_id = None

            u = create_user(user.id, user.full_name, referred_by=ref_id, step='captcha')

            username_str = f" (@{user.username})" if user.username else ""
            ref_msg = f"\n🔗 **تمت الإحالة بواسطة:** `{ref_id}`" if ref_id else ""

            admin_entry_msg = (
                f"🔔 **إشعار دخول لاعب جديد للبوت:** ✨\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **الاسم:** {user.full_name}{username_str}\n"
                f"🆔 **المعرف (ID):** `{user.id}`{ref_msg}\n"
                f"📅 **الوقت:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
                f"✨ ─────────────────── ✨"
            )
            await notify_admins(context, admin_entry_msg)

            msg = (
                f"👋 **أهلاً بك يا {user.full_name} في منصة Golden Games!** 🎮\n\n"
                f"🛡️ **اختبار الأمان:**\n"
                f"ما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)\n"
                f"✍️ أرسل إجابتك للبدء:"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        step = u.get("step", "main")

        if step == "captcha":
            await update.message.reply_text("⚠️ **يرجى الإجابة على سؤال الأمان أولاً:**\nما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)")
            return

        if not u.get("phone_verified") or step == "phone":
            btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("📱 **يرجى مشاركة رقمك لمرة واحدة فقط لتأكيد الحساب والبدء:**", reply_markup=btn)
            return

        is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
        if not is_subscribed:
            await update.message.reply_text("⚠️ **يرجى الاشتراك بالقنوات أولاً للحصول على البونص واستخدام البوت:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
            return

        await process_welcome_bonus(user.id, context)
        await send_main_dashboard(chat_id, user.id, user.full_name, is_admin, context)
    except Exception as e:
        logger.error(f"Error in start_command: {e}")

async def send_main_dashboard(chat_id, user_id, full_name, is_admin, context):
    try:
        u = get_user(user_id)
        bal = u.get("balance", 0.0) if u else 0.0
        spins = u.get("free_spins", 0) if u else 0
        maint_msg = "\n🛠️ **ملاحظة:** البوت في وضع الصيانة حالياً (يمكنك التحكم من لوحة الإدارة)." if is_maintenance_active() else ""

        text = (
            f"👑 **مرحباً بك في منصة الألعاب Golden Games 2026** 🎰{maint_msg}\n"
            f"✨ ─────────────────── ✨\n"
            f"👤 **اللاعب:** {full_name}\n"
            f"🆔 **المعرف (ID):** `{user_id}`\n"
            f"💰 **رصيدك الحالي:** `{bal:,.2f}` NPS\n"
            f"🎡 **اللفات المجانية:** `{spins}` لفة\n"
            f"✨ ─────────────────── ✨\n\n"
            f"👇 اختر اللعبة أو القسم المراد من الأزرار أدناه:"
        )
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin))
    except Exception as e:
        logger.error(f"Error in send_main_dashboard: {e}")

# ----------------------------------------------------
# 8. معالجة الصور والإثباتات
# ----------------------------------------------------
async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if is_rate_limited(user.id):
            return

        if is_maintenance_active() and not is_admin_user(user.id):
            await update.message.reply_text("🛠️ **السيرفر في حالة صيانة حالياً.**")
            return

        u = get_user(user.id)
        if not u or u.get("is_banned"):
            return

        step = u.get("step", "main")
        photo_file_id = update.message.photo[-1].file_id
        caption = update.message.caption or "بدون وصف"

        if step == "user_upload_proof":
            u["step"] = "main"
            save_user(u)

            msg_text = (
                f"📸 **إثبات إصابة/فوز جديد من عميل:**\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **الاسم:** {user.full_name}\n"
                f"🆔 **المعرف:** `{user.id}`\n"
                f"📝 **التفاصيل/الوصف:** {caption}"
            )
            await notify_admins(context, msg_text)
            await update.message.reply_text("✅ **تم إرسال صورة الإثبات إلى الإدارة بنجاح!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if step == "deposit_step_tx":
            amt = context.user_data.get("dep_amount", 0.0)
            method = context.user_data.get("dep_method", "غير محدد")

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO deposits (user_id, method, amount, tx_id, photo_file_id, status, timestamp, channel_msg_id)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, 0)
            """, (user.id, method, float(amt), f"إيصال مصور: {caption}", photo_file_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            dep_id = cursor.lastrowid
            conn.commit()
            conn.close()

            u["step"] = "main"
            save_user(u)

            await update.message.reply_text("✅ **تم تقديم طلب الشحن مع صورة الإيصال بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, f"إيصال مصور: {caption}", photo_file_id)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            dep_text = f"📥 **طلب شحن جديد بإيصال مصور (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n💰 المبلغ: `{amt}` NPS\n📝 الوصف: {caption}"
            await notify_admins(context, dep_text, reply_markup=kb)
            return

        if is_admin_user(user.id) and step == "adm_input_bc_img":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
            user_ids = [row[0] for row in cursor.fetchall()]
            conn.close()

            u["step"] = "main"
            save_user(u)

            bc_caption = update.message.caption or ""

            async def send_photo_fast(uid):
                try:
                    await context.bot.send_photo(chat_id=uid, photo=photo_file_id, caption=bc_caption, parse_mode="Markdown")
                except Exception:
                    pass

            tasks = [send_photo_fast(uid) for uid in user_ids]
            await asyncio.gather(*tasks)

            await update.message.reply_text(f"📸 تم إرسال الإذاعة المصورة لـ `{len(user_ids)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
            return

    except Exception as e:
        logger.error(f"Error handling photo messages: {e}")

# ----------------------------------------------------
# 9. معالجة التواصل والتوثيق
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if is_rate_limited(user.id):
            return

        contact = update.message.contact

        if contact.user_id != user.id:
            await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي الخاص بك فقط.")
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE phone = ? AND phone_verified = 1 AND user_id != ?", (contact.phone_number, user.id))
        existing_phone = cursor.fetchone()
        conn.close()

        if existing_phone:
            await update.message.reply_text("❌ هذا الرقم موثق ومستعمل سابقاً في حساب آخر!", reply_markup=ReplyKeyboardRemove())
            return

        u = get_user(user.id) or create_user(user.id, user.full_name)
        u["phone"] = contact.phone_number
        u["is_verified"] = 1
        u["phone_verified"] = 1
        u["step"] = "channels"
        save_user(u)

        await update.message.reply_text(
            f"✅ **تم توثيق رقم هاتفك بنجاح!**",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )

        is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
        if not is_subscribed:
            await update.message.reply_text("⚠️ **الخطوة الأخيرة: يرجى الاشتراك بالقنوات لاستلام البونص ودخول البوت:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
        else:
            await process_welcome_bonus(user.id, context)
            await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)
    except Exception as e:
        logger.error(f"Error handling contact: {e}")

# ----------------------------------------------------
# 10. معالجة الرسائل النصية الشاملة والتحكم بالأدمن
# ----------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if is_rate_limited(user.id):
            return

        text = update.message.text.strip() if update.message.text else ""

        if text.lower() in ["/start", "ستارت", "البدء", "بدء"]:
            await send_start_reaction(context, update.effective_chat.id, update.message.message_id)

        if is_maintenance_active() and not is_admin_user(user.id):
            await update.message.reply_text("🛠️ **السيرفر في حالة صيانة حالياً.**")
            return

        u = get_user(user.id)
        if not u or u.get("is_banned"):
            return

        step = u.get("step", "main")

        if step == "captcha":
            if text in ["حموية", "حمويه"]:
                u["step"] = "phone"
                u["captcha_answer"] = "passed"
                save_user(u)

                await process_referral_on_captcha_passed(user.id, context)

                btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
                await update.message.reply_text("✅ **إجابة صحيحة 100%! ابن أصول.**\n\n📱 **الخطوة التالية: يرجى مشاركة رقم هاتفك للتوثيق:**", reply_markup=btn)
            else:
                await update.message.reply_text("❌ إجابة خاطئة! السؤال: ما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)")
            return

        if step == "user_upload_proof":
            u["step"] = "main"
            save_user(u)
            await notify_admins(context, f"📸 **إثبات إصابة/فوز نصي من العميل:**\n👤 {user.full_name} (`{user.id}`)\n📝 التفاصيل: {text}")
            await update.message.reply_text("✅ تم إرسال الإثبات النصي للإدارة بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if step == "deposit_step_amount":
            try:
                amt = float(text)
            except ValueError:
                await update.message.reply_text("❌ أدخل مبلغاً صحيحاً بالأرقام.", reply_markup=cancel_keyboard())
                return

            min_dep = float(get_setting("min_deposit", "50"))
            if amt < min_dep:
                await update.message.reply_text(f"❌ الحد الأدنى للشحن هو `{min_dep}` NPS.", reply_markup=cancel_keyboard())
                return

            dep_bonus_pct = float(get_setting("deposit_bonus_percent", "0"))
            bonus_val = amt * (dep_bonus_pct / 100.0)
            total_expected = amt + bonus_val

            context.user_data["dep_amount"] = amt
            method_name = context.user_data.get("dep_method", "غير محدد")
            acc_details = context.user_data.get("dep_acc_details", "غير متوفر")

            u["step"] = "deposit_step_tx"
            save_user(u)

            msg = (
                f"💳 **طريقة الشحن:** {method_name}\n"
                f"💰 **المبلغ المطلوب:** `{amt:,.2f}` NPS\n"
                f"🎁 **البونص الإضافي ({dep_bonus_pct}%):** `{bonus_val:,.2f}` NPS\n"
                f"💵 **إجمالي الرصيد المستلم عند القبول:** `{total_expected:,.2f}` NPS\n\n"
                f"📌 **حساب التحويل:**\n`{acc_details}`\n\n"
                f"✍️ **الآن أدخل رقم العملية/الإشعار أو أرسل صورة الإيصال مباشرة:**"
            )
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
            return

        if step == "deposit_step_tx":
            amt = context.user_data.get("dep_amount", 0.0)
            method = context.user_data.get("dep_method", "غير محدد")

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO deposits (user_id, method, amount, tx_id, photo_file_id, status, timestamp, channel_msg_id)
                VALUES (?, ?, ?, ?, NULL, 'pending', ?, 0)
            """, (user.id, method, float(amt), text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            dep_id = cursor.lastrowid
            conn.commit()
            conn.close()

            u["step"] = "main"
            save_user(u)

            await update.message.reply_text("✅ تم تقديم طلب الشحن بنجاح وهو قيد المراجعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, text, None)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            await notify_admins(context, f"📥 **طلب شحن جديد (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الإشعار: `{text}`\n💰 المبلغ: `{amt}` NPS", reply_markup=kb)
            return

        if step == "withdraw_step_code":
            context.user_data["withdraw_code"] = text
            min_w = float(get_setting("min_withdraw", "1500"))
            comm_pct = float(get_setting("withdraw_commission_percent", "0"))

            u["step"] = "withdraw_step_amount"
            save_user(u)

            msg = (
                f"✍️ **أدخل المبلغ المراد سحبه (NPS):**\n\n"
                f"💰 **رصيدك الحالي:** `{u.get('balance', 0.0):,.2f}` NPS\n"
                f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS\n"
                f"➖ **نسبة عمولة السحب:** `{comm_pct}%`"
            )
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_withdraw"))
            return

        if step == "withdraw_step_amount":
            try:
                amt = float(text)
            except ValueError:
                await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("btn_withdraw"))
                return

            min_w = float(get_setting("min_withdraw", "1500"))
            if amt < min_w or amt > u.get("balance", 0.0):
                await update.message.reply_text(f"❌ المبلغ غير متاح في رصيدك الحالي (`{u.get('balance', 0.0):,.2f}` NPS) أو أقل من حد السحب الأدنى (`{min_w}` NPS).", reply_markup=cancel_keyboard("btn_withdraw"))
                return

            comm_pct = float(get_setting("withdraw_commission_percent", "0"))
            comm_fee = amt * (comm_pct / 100.0)
            net_amt = amt - comm_fee

            context.user_data["withdraw_amount"] = amt
            context.user_data["withdraw_net"] = net_amt
            context.user_data["withdraw_fee"] = comm_fee
            context.user_data["withdraw_comm_pct"] = comm_pct

            method = context.user_data.get("withdraw_method", "غير محدد")
            acc_code = context.user_data.get("withdraw_code", "غير محدد")

            u["step"] = "main"
            save_user(u)

            summary_text = (
                f"📊 **مراجعة وتأكيد تفاصيل طلب السحب:**\n"
                f"✨ ─────────────────── ✨\n"
                f"💳 **طريقة السحب:** {method}\n"
                f"🔢 **رقم الحساب/المحفظة:** `{acc_code}`\n"
                f"💰 **المبلغ المطلوب سحبه:** `{amt:,.2f}` NPS\n"
                f"➖ **عمولة السحب ({comm_pct}%):** `{comm_fee:,.2f}` NPS\n"
                f"💵 **المبلغ الصافي المستلم:** `{net_amt:,.2f}` NPS\n"
                f"✨ ─────────────────── ✨\n\n"
                f"⚠️ **يرجى التأكد من صحة بيانات الحساب والمبلغ قبل الضغط على موافقة.**"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ موافقة وتأكيد الطلب 🚀", callback_data="confirm_withdraw")],
                [InlineKeyboardButton("✏️ تعديل البيانات 🔄", callback_data="btn_withdraw")],
                [InlineKeyboardButton("❌ إلغاء والعودة 🏠", callback_data="back_to_main")]
            ])

            await update.message.reply_text(summary_text, parse_mode="Markdown", reply_markup=kb)
            return

        if step == "input_gift_code":
            now_ts = int(time.time())
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT last_used FROM code_restrictions WHERE user_id = ?", (user.id,))
            res_row = cursor.fetchone()
            last_used = res_row[0] if res_row else 0

            if last_used and (now_ts - last_used) < 21600:
                rem_sec = 21600 - (now_ts - last_used)
                hrs = rem_sec // 3600
                mins = (rem_sec % 3600) // 60
                u["step"] = "main"
                save_user(u)
                conn.close()
                await update.message.reply_text(f"⚠️ **تقييد الأكواد:**\nيمكنك استخدام كود هدية واحد كل 6 ساعات فقط.\n⏱️ **المتبقي:** `{hrs}` ساعة و `{mins}` دقيقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            cursor.execute("SELECT code, amount, uses_left FROM gift_codes WHERE code = ?", (text,))
            g = cursor.fetchone()

            if not g or g["uses_left"] <= 0:
                u["step"] = "main"
                save_user(u)
                conn.close()
                await update.message.reply_text("❌ الكود غير صحيح أو منتهي الاستخدام.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            bal_before = u.get("balance", 0.0)
            amt = float(g["amount"])
            bal_after = bal_before + amt

            u["balance"] = bal_after
            u["step"] = "main"
            save_user(u)

            new_uses = g["uses_left"] - 1
            if new_uses <= 0:
                cursor.execute("DELETE FROM gift_codes WHERE code = ?", (text,))
            else:
                cursor.execute("UPDATE gift_codes SET uses_left = ? WHERE code = ?", (new_uses, text))

            cursor.execute("INSERT OR REPLACE INTO code_restrictions (user_id, last_used) VALUES (?, ?)", (user.id, now_ts))
            conn.commit()
            conn.close()

            add_log(user.id, f"تفعيل كود هدية {text}", amt)

            await update.message.reply_text(f"🎉 تم تفعيل الكود بنجاح وإضافة `{amt}` NPS لرصيدك!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            msg_admin = (
                f"🎟️ **إشعار تفعيل كود هدية جديد:** ✨\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **اللاعب:** {user.full_name}\n"
                f"🆔 **المعرف:** `{user.id}`\n"
                f"🎫 **الكود المستخدم:** `{text}`\n"
                f"💰 **قيمة الكود:** `{amt:,.2f}` NPS\n"
                f"📉 **الرصيد قبل الكود:** `{bal_before:,.2f}` NPS\n"
                f"📈 **الرصيد بعد الكود:** `{bal_after:,.2f}` NPS\n"
                f"✨ ─────────────────── ✨"
            )
            await notify_admins(context, msg_admin)
            return

        if step == "input_support_msg":
            u["step"] = "main"
            save_user(u)
            await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ الرد المباشر للعميل", callback_data=f"adm_rep_supp_{user.id}")]])
            await notify_admins(context, f"💬 **رسالة دعم جديدة من {user.full_name} (`{user.id}`):**\n\n{text}", reply_markup=kb)
            return

        # ----------------------------------------------------
        # خطوات الأدمن الشاملة
        # ----------------------------------------------------
        if is_admin_user(user.id):
            if step == "adm_input_welcome_amt":
                try:
                    amt = float(text)
                    set_setting("welcome_bonus", str(amt))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ **تم تعديل قيمة البونص الترحيبي بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_ref_amt":
                try:
                    amt = float(text)
                    set_setting("referral_reward", str(amt))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ **تم تعديل قيمة مكافأة الإحالة بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_tx_channel":
                ch_val = text.strip()
                set_setting("tx_channel_id", ch_val)
                u["step"] = "main"
                save_user(u)

                is_admin_in_ch = await is_bot_admin_in_channel(context.bot, ch_val)
                status_str = "✅ والبوت مشرف فيها بنجاح!" if is_admin_in_ch else "⚠️ تذكير: يرجى رفع البوت مشرفاً فيها برتبة إرسال رسائل لتفعيل الإشعارات تلقائياً."

                await update.message.reply_text(
                    f"📢 **تم حفظ قناة طلبات الشحن والسحب إلى:** `{ch_val}`\n{status_str}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]])
                )
                return

            if step.startswith("adm_edit_dep_acc_"):
                m_id = step.replace("adm_edit_dep_acc_", "")
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("UPDATE deposit_methods SET account_details = ? WHERE id = ?", (text, m_id))
                conn.commit()
                conn.close()

                u["step"] = "main"
                save_user(u)
                await update.message.reply_text(f"✅ تم تحديث حساب الشحن بنجاح إلى:\n`{text}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة إدارة الشحن 💳", callback_data="adm_dep_methods")]]))
                return

            if step == "adm_input_ref_payout":
                try:
                    parts = text.split()
                    tid, amt = int(parts[0]), float(parts[1])
                    t_user = get_user(tid)
                    if t_user:
                        t_user["balance"] = t_user.get("balance", 0.0) + amt
                        save_user(t_user)
                        add_log(tid, "مستحقات إحالة نشطة", amt)

                    u["step"] = "main"
                    save_user(u)

                    await update.message.reply_text(f"✅ تم إضافة مستحقات الإحالة قدرها `{amt}` NPS للاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة الإحالات النشطة 👥", callback_data="adm_active_referrals")]]))
                    try:
                        await context.bot.send_message(
                            tid,
                            f"🎁 **مبارك! تم إضافة مستحقات نظام الإحالات النشطة قدرها `{amt}` NPS إلى رصيدك من قبل الإدارة!**",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                except Exception:
                    await update.message.reply_text("❌ الصيغة خاطئة. مثال: `7255100997 500`", reply_markup=cancel_keyboard("adm_active_referrals"))
                return

            if step == "adm_input_del_code_manual":
                c_code = text.strip()
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM gift_codes WHERE code = ?", (c_code,))
                affected = cursor.rowcount
                conn.commit()
                conn.close()

                u["step"] = "main"
                save_user(u)

                if affected > 0:
                    await update.message.reply_text(f"✅ تم إلغاء الكود `{c_code}` وحذفه بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    await update.message.reply_text("❌ الكود غير موجود أو ملغي سابقاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if step == "adm_input_add_dep_meth":
                try:
                    parts = text.split("|")
                    m_name, m_det = parts[0].strip(), parts[1].strip()
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO deposit_methods (method_name, account_details) VALUES (?, ?)", (m_name, m_det))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة وسيلة الشحن: `{m_name}` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. يجب الفصل بـ `|`. مثال: `شام كاش | test`", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if step == "adm_input_add_offer":
                try:
                    parts = text.split("|")
                    off_t = parts[0].strip()
                    off_d = parts[1].strip()
                    off_l = parts[2].strip() if len(parts) > 2 else ""

                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO offers (title, description, link) VALUES (?, ?, ?)", (off_t, off_d, off_l))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة العرض: **{off_t}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `عنوان العرض | وصف العرض | https://t.me/example`", reply_markup=cancel_keyboard("adm_offers_menu"))
                return

            if step == "adm_input_add_channel":
                try:
                    parts = text.split("|")
                    ch_id = parts[0].strip()
                    ch_title = parts[1].strip()
                    ch_link = parts[2].strip()

                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR REPLACE INTO channels (channel_id, channel_title, channel_link) VALUES (?, ?, ?)", (ch_id, ch_title, ch_link))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة القناة الإجبارية: **{ch_title}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `@mychannel | قناة المسابقات | https://t.me/mychannel`", reply_markup=cancel_keyboard("adm_channels_menu"))
                return

            if step.startswith("adm_input_wheel_prob_"):
                prob_key = step.replace("adm_input_wheel_prob_", "wheel_prob_")
                try:
                    val = float(text)
                    if val < 0:
                        raise ValueError()
                    set_setting(prob_key, str(val))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تحديث نسبة الخيار بنجاح إلى: `{val}%`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 خوارزمية العجلة 🎡", callback_data="adm_wheel_algo")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل نسبة مئوية صحيحة بالأرقام (مثال: 15.5).", reply_markup=cancel_keyboard("adm_wheel_algo"))
                return

            if step == "adm_input_user_boost":
                try:
                    parts = text.split()
                    target_id, boost_val = int(parts[0]), float(parts[1])
                    t_user = get_user(target_id)
                    if not t_user:
                        await update.message.reply_text("❌ العميل غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    t_user["custom_boost"] = boost_val
                    save_user(t_user)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل حظ العميل `{target_id}` بمقدار إضافي: `{boost_val}%` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `7255100997 10`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_grant_spins_all":
                try:
                    spins_num = int(text)
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET free_spins = free_spins + ? WHERE is_banned = 0", (spins_num,))
                    updated_count = cursor.rowcount
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🎁 تم منح `{spins_num}` لفة مجانية لجميع اللاعبين ({updated_count} لاعب) بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل عدداً صحيحاً بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_grant_spins_user":
                try:
                    parts = text.split()
                    target_id, spins_num = int(parts[0]), int(parts[1])
                    t_user = get_user(target_id)
                    if not t_user:
                        await update.message.reply_text("❌ العميل غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    t_user["free_spins"] = t_user.get("free_spins", 0) + spins_num
                    save_user(t_user)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🎁 تم منح `{spins_num}` لفة مجانية للاعب `{target_id}` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try:
                        await context.bot.send_message(target_id, f"🎁 **مبارك! تم منحك `{spins_num}` لفة مجانية من قِبل الإدارة!**", parse_mode="Markdown")
                    except Exception:
                        pass
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `7255100997 5`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_dep_bonus":
                try:
                    pct = float(text)
                    set_setting("deposit_bonus_percent", str(pct))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل نسبة بونص الشحن إلى `{pct}%` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل نسبة مئوية صحيحة.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_w_commission":
                try:
                    pct = float(text)
                    set_setting("withdraw_commission_percent", str(pct))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل نسبة عمولة السحب إلى `{pct}%` بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل نسبة مئوية صحيحة.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_min_dep":
                try:
                    val = float(text)
                    set_setting("min_deposit", str(val))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل حد الشحن الأدنى إلى `{val}` NPS بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_min_w":
                try:
                    val = float(text)
                    set_setting("min_withdraw", str(val))
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل حد السحب الأدنى إلى `{val}` NPS بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقماً صحيحاً.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_add_bal":
                try:
                    parts = text.split()
                    target_id, amt = int(parts[0]), float(parts[1])
                    t_user = get_user(target_id)
                    if not t_user:
                        await update.message.reply_text("❌ العميل غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    t_user["balance"] = t_user.get("balance", 0.0) + amt
                    save_user(t_user)
                    add_log(target_id, "شحن رصيد بواسطة الأدمن", amt)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"➕ تم إضافة `{amt}` NPS لرصيد العميل `{target_id}` بنجاح! الرصيد الجديد: `{t_user['balance']}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try:
                        await context.bot.send_message(target_id, f"💎 **مبارك! تم إضافة `{amt:,.2f}` NPS إلى رصيدك بواسطة الإدارة!**\n💰 رصيدك الحالي: `{t_user['balance']:,.2f}` NPS", parse_mode="Markdown")
                    except Exception:
                        pass
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `7255100997 100`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_sub_bal":
                try:
                    parts = text.split()
                    target_id, amt = int(parts[0]), float(parts[1])
                    t_user = get_user(target_id)
                    if not t_user:
                        await update.message.reply_text("❌ العميل غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    t_user["balance"] = max(0.0, t_user.get("balance", 0.0) - amt)
                    save_user(t_user)
                    add_log(target_id, "خصم رصيد بواسطة الأدمن", -amt)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"➖ تم خصم `{amt}` NPS من رصيد العميل `{target_id}` بنجاح! الرصيد الجديد: `{t_user['balance']}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `7255100997 50`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_batch_codes":
                try:
                    parts = text.split()
                    count, amt, uses = int(parts[0]), float(parts[1]), int(parts[2])
                    generated = []
                    conn = get_db()
                    cursor = conn.cursor()
                    for _ in range(count):
                        c_code = "GOLDEN-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                        cursor.execute("INSERT INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (c_code, amt, uses))
                        generated.append(c_code)
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    codes_str = "\n".join([f"`{c}`" for c in generated])
                    await update.message.reply_text(f"🎁 **تم إنشاء `{count}` كود بنجاح!**\n💰 القيمة لكل كود: `{amt}` NPS\n🔢 الاستخدامات لكل كود: `{uses}`\n\n📋 **الأكواد:**\n{codes_str}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `5 100 1` (عدد الأكواد القيمة عدد الاستخدامات)", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_make_gift":
                try:
                    parts = text.split()
                    c_code, amt, uses = parts[0].strip(), float(parts[1]), int(parts[2])
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR REPLACE INTO gift_codes (code, amount, uses_left) VALUES (?, ?, ?)", (c_code, amt, uses))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🎫 **تم إنشاء الكود بنجاح!**\n🎫 **الكود:** `{c_code}`\n💰 **القيمة:** `{amt}` NPS\n🔢 **عدد مرات الاستخدام:** `{uses}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. مثال: `VIP100 100 1`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_user_info":
                try:
                    target_id = int(text)
                    t_user = get_user(target_id)
                    if not t_user:
                        await update.message.reply_text("❌ العميل غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    
                    u["step"] = "main"
                    save_user(u)

                    ref_str = f"`{t_user.get('referred_by')}`" if t_user.get('referred_by') else "بدون"
                    banned_str = "🔴 محظور" if t_user.get('is_banned') else "🟢 نشط"

                    info_txt = (
                        f"🔍 **تفاصيل العميل `# {target_id}`:**\n"
                        f"✨ ─────────────────── ✨\n"
                        f"👤 **الاسم الكامل:** {t_user.get('full_name', 'غير محدد')}\n"
                        f"📱 **رقم الهاتف:** `{t_user.get('phone', 'غير موثق')}`\n"
                        f"💰 **الرصيد الحالي:** `{t_user.get('balance', 0.0):,.2f}` NPS\n"
                        f"🎡 **اللفات المجانية:** `{t_user.get('free_spins', 0)}` لفة\n"
                        f"🎯 **حظ العميل الإضافي:** `+{t_user.get('custom_boost', 0.0)}%`\n"
                        f"🎮 **عدد الألعاب الملعوبة:** `{t_user.get('games_played', 0)}` لعبة\n"
                        f"💸 **إجمالي الإنفاق:** `{t_user.get('total_spent', 0.0):,.2f}` NPS\n"
                        f"👥 **عدد الإحالات:** `{t_user.get('referrals_count', 0)}` (النشطة: `{t_user.get('active_referrals_count', 0)}`)\n"
                        f"🔗 **تم إحالته بواسطة:** {ref_str}\n"
                        f"🚫 **الحالة:** {banned_str}\n"
                        f"✨ ─────────────────── ✨"
                    )
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ إضافة رصيد", callback_data=f"quick_add_{target_id}"), InlineKeyboardButton("➖ خصم رصيد", callback_data=f"quick_sub_{target_id}")],
                        [InlineKeyboardButton("🚫 حظر العميل", callback_data=f"quick_ban_{target_id}"), InlineKeyboardButton("🔓 فك حظر العميل", callback_data=f"quick_unban_{target_id}")],
                        [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                    ])
                    await update.message.reply_text(info_txt, parse_mode="Markdown", reply_markup=kb)
                except Exception:
                    await update.message.reply_text("❌ أدخل معرف ID صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_ban_id":
                try:
                    target_id = int(text)
                    t_user = get_user(target_id)
                    if t_user:
                        t_user["is_banned"] = 1
                        save_user(t_user)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🔴 تم حظر العميل `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_unban_id":
                try:
                    target_id = int(text)
                    t_user = get_user(target_id)
                    if t_user:
                        t_user["is_banned"] = 0
                        save_user(t_user)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🟢 تم فك حظر العميل `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_bc_txt":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
                user_ids = [row[0] for row in cursor.fetchall()]
                conn.close()

                u["step"] = "main"
                save_user(u)

                async def send_txt_fast(uid):
                    try:
                        await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                    except Exception:
                        pass

                tasks = [send_txt_fast(uid) for uid in user_ids]
                await asyncio.gather(*tasks)

                await update.message.reply_text(f"📣 تم إرسال الإذاعة النصية لـ `{len(user_ids)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if step == "adm_input_pm_txt":
                try:
                    parts = text.split("|")
                    target_id, pm_msg = int(parts[0].strip()), parts[1].strip()
                    await context.bot.send_message(chat_id=target_id, text=f"📩 **رسالة من الإدارة:**\n\n{pm_msg}", parse_mode="Markdown")
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إرسال الرسالة الخاصة للعميل `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `7255100997 | أهلاً بك في منصتنا`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_add_admin":
                try:
                    new_adm = int(text)
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_adm,))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"👮 تم إعطاء صلاحيات الأدمن للمستخدم `{new_adm}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_del_admin":
                try:
                    del_adm = int(text)
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM admins WHERE user_id = ?", (del_adm,))
                    conn.commit()
                    conn.close()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"❌ تم سحب صلاحيات الأدمن من المستخدم `{del_adm}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step.startswith("adm_input_rep_supp_"):
                target_id = int(step.replace("adm_input_rep_supp_", ""))
                try:
                    await context.bot.send_message(chat_id=target_id, text=f"💬 **رد الدعم الفني:**\n\n{text}", parse_mode="Markdown")
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إرسال الرد للعميل `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ تعذر إرسال الرد للعميل.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

    except Exception as e:
        logger.error(f"Error handling text messages: {e}")

# ----------------------------------------------------
# 11. معالجة الضغط على الأزرار (Callback Query Handler)
# ----------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if is_rate_limited(user.id):
        await query.answer("⏱️ يرجى الانتظار لحظة...", show_alert=False)
        return

    data = query.data
    u = get_user(user.id) or create_user(user.id, user.full_name)

    if u.get("is_banned"):
        await query.answer("❌ حسابك محظور.", show_alert=True)
        return

    if is_maintenance_active() and not is_admin_user(user.id):
        await query.answer("🛠️ السيرفر في حالة صيانة حالياً.", show_alert=True)
        return

    try:
        await query.answer()

        if data == "back_to_main":
            u["step"] = "main"
            save_user(u)
            bal = u.get("balance", 0.0)
            spins = u.get("free_spins", 0)
            maint_msg = "\n🛠️ **ملاحظة:** البوت في وضع الصيانة حالياً." if is_maintenance_active() else ""
            
            text = (
                f"👑 **مرحباً بك في منصة الألعاب Golden Games 2026** 🎰{maint_msg}\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **اللاعب:** {user.full_name}\n"
                f"🆔 **المعرف (ID):** `{user.id}`\n"
                f"💰 **رصيدك الحالي:** `{bal:,.2f}` NPS\n"
                f"🎡 **اللفات المجانية:** `{spins}` لفة\n"
                f"✨ ─────────────────── ✨\n\n"
                f"👇 اختر اللعبة أو القسم المراد من الأزرار أدناه:"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard(is_admin_user(user.id)))
            return

        if data == "check_subscription_status":
            is_sub, unsub = await check_user_channels_subscription(context.bot, user.id)
            if is_sub:
                await process_welcome_bonus(user.id, context)
                await query.edit_message_text("✅ **تم التحقق من اشتراكك بنجاح! مرحباً بك.**", parse_mode="Markdown")
                await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin_user(user.id), context)
            else:
                await query.edit_message_text("⚠️ **لم تقم بالاشتراك بجميع القنوات المطلوب الاشتراك بها بعد:**", reply_markup=build_sub_keyboard(unsub), parse_mode="Markdown")
            return

        if data == "open_admin_panel" and is_admin_user(user.id):
            u["step"] = "main"
            save_user(u)
            await query.edit_message_text("🛑 **أهلاً بك في لوحة الإدارة العليا والتحكم:**", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_wheel_algo" and is_admin_user(user.id):
            await query.edit_message_text("🎡 **إعدادات ونسب خوارزمية عجلة الحظ:**\nاضغط على أي زر لتعديل النسبة المئوية فوراً:", parse_mode="Markdown", reply_markup=admin_wheel_algo_keyboard())
            return

        if data.startswith("adm_set_wheel_prob_") and is_admin_user(user.id):
            prob_key = data.replace("adm_set_wheel_prob_", "")
            names_map = {
                "luck": "حظ أوفر",
                "try_again": "حاول مرة أخرى",
                "5": "5 NPS",
                "10": "10 NPS",
                "15": "15 NPS",
                "25": "25 NPS",
                "50": "50 NPS",
                "100": "100 NPS",
                "250": "250 NPS",
                "dep_bonus_20": "بونص 20%",
                "500": "500 NPS",
                "1000": "1000 NPS"
            }
            label = names_map.get(prob_key, prob_key)
            u["step"] = f"adm_input_wheel_prob_{prob_key}"
            save_user(u)
            await query.edit_message_text(f"✍️ **أدخل النسبة المئوية الجديدة الخيار ({label}) بالأرقام (مثال: 25):**", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_wheel_algo"))
            return

        if data == "adm_toggle_maint" and is_admin_user(user.id):
            curr = get_setting("maintenance_mode", "1")
            new_val = "0" if curr == "1" else "1"
            set_setting("maintenance_mode", new_val)
            await query.edit_message_text("🛑 **تم تحديث وضع الصيانة!**", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_toggle_welcome" and is_admin_user(user.id):
            curr = get_setting("welcome_bonus_enabled", "1")
            new_val = "0" if curr == "1" else "1"
            set_setting("welcome_bonus_enabled", new_val)
            await query.edit_message_text("🛑 **تم تحديث حالة البونص الترحيبي!**", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_set_welcome_amt" and is_admin_user(user.id):
            u["step"] = "adm_input_welcome_amt"
            save_user(u)
            await query.edit_message_text("✍️ **أدخل القيمة الجديدة للبونص الترحيبي بالأرقام (NPS):**", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_toggle_ref_bonus" and is_admin_user(user.id):
            curr = get_setting("referral_reward_enabled", "0")
            new_val = "0" if curr == "1" else "1"
            set_setting("referral_reward_enabled", new_val)
            await query.edit_message_text("🛑 **تم تحديث حالة مكافأة الإحالة!**", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_set_ref_amt" and is_admin_user(user.id):
            u["step"] = "adm_input_ref_amt"
            save_user(u)
            await query.edit_message_text("✍️ **أدخل القيمة الجديدة لمكافأة الإحالة بالأرقام (NPS):**", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "adm_toggle_ref_spin" and is_admin_user(user.id):
            curr = get_setting("referral_spin_enabled", "1")
            new_val = "0" if curr == "1" else "1"
            set_setting("referral_spin_enabled", new_val)
            await query.edit_message_text("🛑 **تم تحديث حالة لفة الإحالة المجانية!**", reply_markup=admin_panel_keyboard())
            return

        if data == "adm_set_tx_channel" and is_admin_user(user.id):
            u["step"] = "adm_input_tx_channel"
            save_user(u)
            await query.edit_message_text("✍️ **أدخل معرّف أو ID قناة طلبات الشحن والسحب (مثال: `@my_tx_channel` أو `-100123456789`):**", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
            return

        if data == "btn_offers":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM offers")
            offers = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if not offers:
                await query.edit_message_text("🔴 **لا توجد عروض ترويجية متاحة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                text = "🎁 **العروض الترويجية الحالية 🔥:**\n✨ ─────────────────── ✨\n"
                kb = []
                for off in offers:
                    text += f"📌 **{off['title']}**\n📝 {off['description']}\n\n"
                    if off.get('link'):
                        kb.append([InlineKeyboardButton(f"🔗 {off['title']}", url=off['link'])])
                kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data == "btn_deposit":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM deposit_methods")
            methods = [dict(row) for row in cursor.fetchall()]
            conn.close()

            min_dep = float(get_setting("min_deposit", "50"))
            dep_bonus_pct = float(get_setting("deposit_bonus_percent", "0"))

            if not methods:
                await query.edit_message_text("⚠️ **لا توجد وسائل شحن متاحة حالياً. يرجى التواصل مع الدعم الفني.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            kb = []
            for m in methods:
                kb.append([InlineKeyboardButton(f"💳 {m['method_name']}", callback_data=f"dep_meth_{m['id']}")])
            kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])

            bonus_txt = f"\n🎁 **بونص شحن إضافي حالي:** `{dep_bonus_pct}%`" if dep_bonus_pct > 0 else ""
            text = (
                f"🚨 **قسم شحن الحساب ⚡**\n"
                f"✨ ─────────────────── ✨\n"
                f"💰 **حد الشحن الأدنى:** `{min_dep}` NPS{bonus_txt}\n\n"
                f"👇 **اختر وسيلة الشحن المناسبة لك:**"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("dep_meth_"):
            m_id = int(data.replace("dep_meth_", ""))
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM deposit_methods WHERE id = ?", (m_id,))
            method = cursor.fetchone()
            conn.close()

            if not method:
                await query.edit_message_text("❌ وسيلة الشحن غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["dep_method"] = method["method_name"]
            context.user_data["dep_acc_details"] = method["account_details"]

            u["step"] = "deposit_step_amount"
            save_user(u)

            min_dep = float(get_setting("min_deposit", "50"))
            text = (
                f"💳 **وسيلة الشحن المختارة:** {method['method_name']}\n"
                f"📌 **رقم/حساب التحويل:**\n`{method['account_details']}`\n\n"
                f"✍️ **أدخل المبلغ المراد شحنه (NPS):**\n"
                f"⚠️ **الحد الأدنى للشحن:** `{min_dep}` NPS"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_deposit"))
            return

        if data == "btn_withdraw":
            min_w = float(get_setting("min_withdraw", "1500"))
            comm_pct = float(get_setting("withdraw_commission_percent", "0"))
            bal = u.get("balance", 0.0)

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM deposit_methods")
            methods = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if bal < min_w:
                await query.edit_message_text(
                    f"❌ **رصيدك غير كافٍ للسحب!**\n\n"
                    f"💰 **رصيدك الحالي:** `{bal:,.2f}` NPS\n"
                    f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]])
                )
                return

            kb = []
            for m in methods:
                kb.append([InlineKeyboardButton(f"💸 {m['method_name']}", callback_data=f"w_meth_{m['id']}")])
            kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])

            text = (
                f"🥊 **قسم سحب الأرباح 🪙**\n"
                f"✨ ─────────────────── ✨\n"
                f"💰 **رصيدك القابل للسحب:** `{bal:,.2f}` NPS\n"
                f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS\n"
                f"➖ **عمولة السحب:** `{comm_pct}%`\n\n"
                f"👇 **اختر وسيلة تحويل الأرباح:**"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("w_meth_"):
            m_id = int(data.replace("w_meth_", ""))
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM deposit_methods WHERE id = ?", (m_id,))
            method = cursor.fetchone()
            conn.close()

            if not method:
                await query.edit_message_text("❌ وسيلة السحب غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["withdraw_method"] = method["method_name"]
            u["step"] = "withdraw_step_code"
            save_user(u)

            text = (
                f"💳 **طريقة السحب المختارة:** {method['method_name']}\n\n"
                f"✍️ **أدخل رقم المحفظة / الحساب الخاص بك للتحويل:**"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_withdraw"))
            return

        if data == "confirm_withdraw":
            amt = context.user_data.get("withdraw_amount", 0.0)
            net_amt = context.user_data.get("withdraw_net", 0.0)
            comm_fee = context.user_data.get("withdraw_fee", 0.0)
            method = context.user_data.get("withdraw_method", "غير محدد")
            acc_code = context.user_data.get("withdraw_code", "غير محدد")

            bal = u.get("balance", 0.0)
            if amt <= 0 or amt > bal:
                await query.edit_message_text("❌ **رصيدك الحالي لا يكفي لإتمام الطلب.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            # Dynamic deduction from balance
            u["balance"] = bal - amt
            save_user(u)

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO withdrawals (user_id, method, acc_code, amount, net_amount, fee, status, timestamp, channel_msg_id)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0)
            """, (user.id, method, acc_code, float(amt), float(net_amt), float(comm_fee), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            w_id = cursor.lastrowid
            conn.commit()
            conn.close()

            add_log(user.id, f"طلب سحب أرباح #{w_id}", -amt)

            await query.edit_message_text(
                f"✅ **تم تقديم طلب السحب (# {w_id}) بنجاح!**\n\n"
                f"💰 **المبلغ المخصوم تلقائياً:** `{amt:,.2f}` NPS\n"
                f"💵 **الصافي المستلم عند الموافقة:** `{net_amt:,.2f}` NPS\n"
                f"⏳ الطلب قيد المراجعة والمعالجة من قبل الإدارة.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]])
            )

            await send_withdraw_to_channel(context.bot, w_id, user, method, acc_code, amt, net_amt)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض وإعادة الرصيد 🗑️", callback_data=f"rej_w_{w_id}")]])
            w_text = (
                f"💸 **طلب سحب جديد (# {w_id}):**\n"
                f"👤 {user.full_name} (`{user.id}`)\n"
                f"💳 الطريقة: {method}\n"
                f"🔢 الحساب: `{acc_code}`\n"
                f"💰 المبلغ: `{amt:,.2f}` NPS (الصافي: `{net_amt:,.2f}` NPS)"
            )
            await notify_admins(context, w_text, reply_markup=kb)
            return

        if data == "btn_account":
            bal = u.get("balance", 0.0)
            spins = u.get("free_spins", 0)
            games = u.get("games_played", 0)
            spent = u.get("total_spent", 0.0)
            refs = u.get("referrals_count", 0)
            active_refs = u.get("active_referrals_count", 0)
            ver = "🟢 موثق" if u.get("is_verified") else "🔴 غير موثق"
            phone_str = u.get("phone") or "غير مسجل"

            text = (
                f"📌 **الملف الشخصي للعميل 📊**\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **الاسم:** {u.get('full_name', 'لاعب')}\n"
                f"🆔 **المعرف:** `{user.id}`\n"
                f"📱 **رقم الهاتف:** `{phone_str}`\n"
                f"🛡️ **حالة التوثيق:** {ver}\n"
                f"💰 **الرصيد الحالي:** `{bal:,.2f}` NPS\n"
                f"🎡 **اللفات المجانية:** `{spins}` لفة\n"
                f"🎯 **حظك الإضافي:** `+{u.get('custom_boost', 0.0)}%`\n"
                f"🎮 **الألعاب الملعوبة:** `{games}`\n"
                f"💸 **إجمالي الإنفاق:** `{spent:,.2f}` NPS\n"
                f"👥 **عدد الإحالات:** `{refs}` (النشطة: `{active_refs}`)\n"
                f"✨ ─────────────────── ✨"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_referral":
            bot_me = await context.bot.get_me()
            ref_link = f"https://t.me/{bot_me.username}?start={user.id}"
            refs = u.get("referrals_count", 0)
            active_refs = u.get("active_referrals_count", 0)

            ref_reward_enabled = get_setting("referral_reward_enabled", "0") == "1"
            ref_reward_amt = float(get_setting("referral_reward", "50")) if ref_reward_enabled else 0.0
            ref_spin_enabled = get_setting("referral_spin_enabled", "1") == "1"

            rew_str = []
            if ref_spin_enabled:
                rew_str.append("🎡 1 لفة مجانية")
            if ref_reward_amt > 0:
                rew_str.append(f"💰 `{ref_reward_amt}` NPS")
            
            rewards_desc = " + ".join(rew_str) if rew_str else "لا يوجد بونص حالياً"

            text = (
                f"🎯 **نظام الإحالة ودعوة الأصدقاء 🚀**\n"
                f"✨ ─────────────────── ✨\n"
                f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
                f"🎁 **مكافأة كل صديق ينضم عبر رابطك:**\n{rewards_desc}\n\n"
                f"📊 **إحصائيات الإحالات:**\n"
                f"👥 إجمالي المدعوين: `{refs}`\n"
                f"🟢 الإحالات النشطة: `{active_refs}`\n"
                f"✨ ─────────────────── ✨"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 مشاركة الرابط 🚀", url=f"https://t.me/share/url?url={ref_link}&text=انضم%20إلى%20بوت%20Golden%20Games%20واحصل%20على%20مكافآت%20مذهلة!")],
                [InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]
            ])
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            return

        if data == "btn_send_proof":
            u["step"] = "user_upload_proof"
            save_user(u)
            text = (
                f"🏮 **إرسال إثبات الفوز أو الإصابة 🏆**\n\n"
                f"✍️ **أرسل الآن صورة الإثبات أو النص الذي يثبت فوزك لتقوم الإدارة بمراجعته ونشره:**"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_gift":
            u["step"] = "input_gift_code"
            save_user(u)
            await query.edit_message_text("🎟️ **أدخل كود الهدية الخاص بك هنا:**", parse_mode="Markdown", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_buy_bot":
            text = (
                f"🍷 **خدمة إنشاء وتطوير البوتات الخاصة ⚙️**\n"
                f"✨ ─────────────────── ✨\n"
                f"هل تريد بوت ألعاب، شحن، أو سحب خاص بك بمواصفات عالية؟\n\n"
                f"🚀 للتواصل المباشر مع المبرمج لتنفيذ مشروعك:\n"
                f"👨‍💻 **قناة المبرمج:** @lerafree\n"
                f"✨ ─────────────────── ✨"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎒 قناة المبرمج 🚀", url="https://t.me/lerafree")],
                [InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]
            ])
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            return

        if data == "btn_logs":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT action, amount, created_at FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (str(user.id),))
            logs = cursor.fetchall()
            conn.close()

            if not logs:
                await query.edit_message_text("🛑 **لا توجد عمليات مسجلة في حسابك حتى الآن.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                text = "🛑 **سجل العمليات الأخير 📑:**\n✨ ─────────────────── ✨\n"
                for l in logs:
                    amt_str = f" (`{l['amount']:+,.2f}` NPS)" if l['amount'] != 0 else ""
                    text += f"🔹 {l['action']}{amt_str}\n⏱️ `{l['created_at']}`\n\n"
                text += "✨ ─────────────────── ✨"
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_support":
            u["step"] = "input_support_msg"
            save_user(u)
            await query.edit_message_text("🌶️ **قسم الدعم الفني المباشر 👨‍💻**\n\n✍️ **أكتب استفسارك أو مشكلتك وسيقوم فريق الدعم بالرد عليك فوراً:**", parse_mode="Markdown", reply_markup=cancel_keyboard("back_to_main"))
            return

        # ----------------------------------------------------
        # Admin Buttons Handlers
        # ----------------------------------------------------
        if is_admin_user(user.id):
            if data.startswith("app_dep_"):
                dep_id = int(data.replace("app_dep_", ""))
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,))
                dep = cursor.fetchone()

                if not dep:
                    await query.answer("❌ طلب الشحن غير موجود.", show_alert=True)
                    conn.close()
                    return

                dep_dict = dict(dep)
                if dep_dict["status"] != "pending":
                    await query.answer("⚠️ تم معالجة هذا الطلب سابقاً.", show_alert=True)
                    conn.close()
                    return

                cursor.execute("UPDATE deposits SET status = 'approved' WHERE id = ?", (dep_id,))
                conn.commit()

                dep_user_id = dep_dict["user_id"]
                dep_amount = float(dep_dict["amount"])

                dep_bonus_pct = float(get_setting("deposit_bonus_percent", "0"))
                bonus_amt = dep_amount * (dep_bonus_pct / 100.0)
                total_credit = dep_amount + bonus_amt

                target_user = get_user(dep_user_id)
                if target_user:
                    target_user["balance"] = target_user.get("balance", 0.0) + total_credit
                    save_user(target_user)
                    add_log(dep_user_id, f"تعبئة رصيد شحن مقبول #{dep_id}", total_credit)

                conn.close()

                await notify_channel_deposit_status(context.bot, dep_dict, "approved")

                try:
                    await context.bot.send_message(
                        chat_id=dep_user_id,
                        text=(
                            f"✅ **تم قبول طلب الشحن الخاص بك (# {dep_id}) بنجاح!**\n\n"
                            f"💰 **المبلغ المضاف:** `{dep_amount:,.2f}` NPS\n"
                            f"🎁 **البونص الإضافي:** `{bonus_amt:,.2f}` NPS\n"
                            f"💳 **إجمالي الرصيد المضاف تلقائياً:** `{total_credit:,.2f}` NPS\n"
                            f"📈 **رصيدك الحالي:** `{target_user['balance']:,.2f}` NPS"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await query.edit_message_text(f"✅ **تمت الموافقة على طلب الشحن #{dep_id} وتعبئة الرصيد بـ `{total_credit:,.2f}` NPS بنجاح!**", parse_mode="Markdown")
                return

            if data.startswith("rej_dep_"):
                dep_id = int(data.replace("rej_dep_", ""))
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,))
                dep = cursor.fetchone()

                if not dep:
                    await query.answer("❌ طلب الشحن غير موجود.", show_alert=True)
                    conn.close()
                    return

                dep_dict = dict(dep)
                if dep_dict["status"] != "pending":
                    await query.answer("⚠️ تم معالجة هذا الطلب سابقاً.", show_alert=True)
                    conn.close()
                    return

                cursor.execute("UPDATE deposits SET status = 'rejected' WHERE id = ?", (dep_id,))
                conn.commit()
                conn.close()

                await notify_channel_deposit_status(context.bot, dep_dict, "rejected")

                try:
                    await context.bot.send_message(
                        chat_id=dep_dict["user_id"],
                        text=f"❌ **عذراً، تم رفض طلب الشحن الخاص بك (# {dep_id}).**\nلأي استفسار يمكنك التواصل مع الدعم الفني.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await query.edit_message_text(f"❌ **تم رفض طلب الشحن #{dep_id}.**", parse_mode="Markdown")
                return

            if data.startswith("app_w_"):
                w_id = int(data.replace("app_w_", ""))
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,))
                w = cursor.fetchone()

                if not w:
                    await query.answer("❌ طلب السحب غير موجود.", show_alert=True)
                    conn.close()
                    return

                w_dict = dict(w)
                if w_dict["status"] != "pending":
                    await query.answer("⚠️ تم معالجة هذا الطلب سابقاً.", show_alert=True)
                    conn.close()
                    return

                cursor.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
                conn.commit()
                conn.close()

                await notify_channel_withdrawal_status(context.bot, w_dict, "approved")

                try:
                    await context.bot.send_message(
                        chat_id=w_dict["user_id"],
                        text=(
                            f"✅ **تمت الموافقة على طلب السحب الخاص بك (# {w_id}) وتحويل الأرباح!**\n\n"
                            f"💵 **الصافي المحول لحسابك:** `{w_dict['net_amount']:,.2f}` NPS\n"
                            f"💳 **الحساب:** `{w_dict['acc_code']}`"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await query.edit_message_text(f"✅ **تمت الموافقة على طلب السحب #{w_id} وتأكيد التحويل!**", parse_mode="Markdown")
                return

            if data.startswith("rej_w_"):
                w_id = int(data.replace("rej_w_", ""))
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,))
                w = cursor.fetchone()

                if not w:
                    await query.answer("❌ طلب السحب غير موجود.", show_alert=True)
                    conn.close()
                    return

                w_dict = dict(w)
                if w_dict["status"] != "pending":
                    await query.answer("⚠️ تم معالجة هذا الطلب سابقاً.", show_alert=True)
                    conn.close()
                    return

                cursor.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
                conn.commit()
                conn.close()

                refund_amt = float(w_dict["amount"])
                target_user = get_user(w_dict["user_id"])
                if target_user:
                    target_user["balance"] = target_user.get("balance", 0.0) + refund_amt
                    save_user(target_user)
                    add_log(w_dict["user_id"], f"إعادة رصيد سحب مرفوض #{w_id}", refund_amt)

                await notify_channel_withdrawal_status(context.bot, w_dict, "rejected")

                try:
                    await context.bot.send_message(
                        chat_id=w_dict["user_id"],
                        text=(
                            f"❌ **تم رفض طلب السحب الخاص بك (# {w_id}).**\n\n"
                            f"💰 **تم إعادة المبلغ قدره `{refund_amt:,.2f}` NPS إلى رصيدك تلقائياً.**"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await query.edit_message_text(f"❌ **تم رفض طلب السحب #{w_id} وإعادة المبلغ `{refund_amt:,.2f}` NPS إلى العميل تلقائياً.**", parse_mode="Markdown")
                return

            if data.startswith("quick_add_"):
                target_id = int(data.replace("quick_add_", ""))
                u["step"] = "adm_input_add_bal"
                save_user(u)
                await query.edit_message_text(f"✍️ **أدخل المبلغ المراد إضافته للاعب `{target_id}` (مثال: `{target_id} 100`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data.startswith("quick_sub_"):
                target_id = int(data.replace("quick_sub_", ""))
                u["step"] = "adm_input_sub_bal"
                save_user(u)
                await query.edit_message_text(f"✍️ **أدخل المبلغ المراد خصمه من اللاعب `{target_id}` (مثال: `{target_id} 50`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data.startswith("quick_ban_"):
                target_id = int(data.replace("quick_ban_", ""))
                t_user = get_user(target_id)
                if t_user:
                    t_user["is_banned"] = 1
                    save_user(t_user)
                await query.edit_message_text(f"🔴 تم حظر اللاعب `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data.startswith("quick_unban_"):
                target_id = int(data.replace("quick_unban_", ""))
                t_user = get_user(target_id)
                if t_user:
                    t_user["is_banned"] = 0
                    save_user(t_user)
                await query.edit_message_text(f"🟢 تم فك حظر اللاعب `{target_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data.startswith("adm_rep_supp_"):
                target_id = int(data.replace("adm_rep_supp_", ""))
                u["step"] = f"adm_input_rep_supp_{target_id}"
                save_user(u)
                await query.edit_message_text(f"✍️ **أدخل الرد المراد إرساله للعميل `{target_id}`:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_active_referrals":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, full_name, referrals_count, active_referrals_count FROM users WHERE referrals_count > 0 ORDER BY referrals_count DESC LIMIT 15")
                rows = cursor.fetchall()
                conn.close()

                if not rows:
                    txt = "👥 **لا توجد إحالات نشطة حالياً.**"
                else:
                    txt = "👥 **قائمة أعضاء الإحالات النشطة الأكثر استخداماً 📊:**\n✨ ─────────────────── ✨\n"
                    for r in rows:
                        txt += f"👤 {r['full_name']} (`{r['user_id']}`)\n👥 الإجمالي: `{r['referrals_count']}` | 🟢 النشطة: `{r['active_referrals_count']}`\n\n"
                    txt += "✨ ─────────────────── ✨\nلصرف مستحقات إحالة يدوي أرسل: `المعرف المبلغ`"

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 صرف مستحقات إحالة 💵", callback_data="adm_pay_ref_due")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ])
                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)
                return

            if data == "adm_pay_ref_due":
                u["step"] = "adm_input_ref_payout"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل معرف العميل والمبلغ المستحق (مثال: `7255100997 500`):**", reply_markup=cancel_keyboard("adm_active_referrals"))
                return

            if data == "adm_grant_spins_menu":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎁 منح لفات لجميع اللاعبين 🌐", callback_data="adm_grant_spins_all")],
                    [InlineKeyboardButton("🎯 منح لفات للاعب معين 👤", callback_data="adm_grant_spins_user")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ])
                await query.edit_message_text("🎰 **منح اللفات المجانية:**\nاختر فئة المنح المطلوب:", reply_markup=kb)
                return

            if data == "adm_grant_spins_all":
                u["step"] = "adm_input_grant_spins_all"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل عدد اللفات المراد منحها لجميع اللاعبين غير المحظورين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_grant_spins_user":
                u["step"] = "adm_input_grant_spins_user"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل معرف العميل وعدد اللفات (مثال: `7255100997 5`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_channels_menu":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM channels")
                chans = cursor.fetchall()
                conn.close()

                txt = "📢 **قنوات الاشتراك الصارمة الحالية:**\n✨ ─────────────────── ✨\n"
                kb = []
                for ch in chans:
                    txt += f"🔹 **{ch['channel_title']}** (`{ch['channel_id']}`)\n🔗 {ch['channel_link']}\n\n"
                    kb.append([InlineKeyboardButton(f"❌ حذف {ch['channel_title']}", callback_data=f"adm_del_ch_{ch['channel_id']}")])

                kb.append([InlineKeyboardButton("➕ إضافة قناة إجبارية جديد 📢", callback_data="adm_add_channel")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_channel":
                u["step"] = "adm_input_add_channel"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل بيانات القناة بالصيغة:**\n`ID_أو_معرف | العنوان | الرابط`\n\nمثال:\n`@lerafree | قناة المبرمج | https://t.me/lerafree`", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_channels_menu"))
                return

            if data.startswith("adm_del_ch_"):
                ch_id = data.replace("adm_del_ch_", "")
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
                conn.commit()
                conn.close()
                await query.edit_message_text(f"✅ تم حذف القناة `{ch_id}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة القنوات 📢", callback_data="adm_channels_menu")]]))
                return

            if data == "adm_dep_methods":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM deposit_methods")
                methods = cursor.fetchall()
                conn.close()

                txt = "💳 **حسابات ووسائل الشحن الحالية:**\n✨ ─────────────────── ✨\n"
                kb = []
                for m in methods:
                    txt += f"💳 **{m['method_name']}** (ID: `{m['id']}`)\n📌 `{m['account_details']}`\n\n"
                    kb.append([
                        InlineKeyboardButton(f"✏️ تعديل {m['method_name']}", callback_data=f"adm_edit_dep_m_{m['id']}"),
                        InlineKeyboardButton(f"❌ حذف", callback_data=f"adm_del_dep_m_{m['id']}")
                    ])

                kb.append([InlineKeyboardButton("➕ إضافة وسيلة شحن جديدة 💳", callback_data="adm_add_dep_meth")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_dep_meth":
                u["step"] = "adm_input_add_dep_meth"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل اسم وسيلة الشحن والتفاصيل بالصيغة:**\n`اسم الوسيلة | تفاصيل الحساب`\n\nمثال:\n`سيريتل كاش | 00973427`", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if data.startswith("adm_edit_dep_m_"):
                m_id = data.replace("adm_edit_dep_m_", "")
                u["step"] = f"adm_edit_dep_acc_{m_id}"
                save_user(u)
                await query.edit_message_text(f"✍️ **أدخل التفاصيل/الحساب الجديد لوسيلة الشحن #{m_id}:**", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if data.startswith("adm_del_dep_m_"):
                m_id = data.replace("adm_del_dep_m_", "")
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM deposit_methods WHERE id = ?", (m_id,))
                conn.commit()
                conn.close()
                await query.edit_message_text(f"✅ تم حذف وسيلة الشحن #{m_id}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 وسائل الشحن 💳", callback_data="adm_dep_methods")]]))
                return

            if data == "adm_deposits":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM deposits WHERE status = 'pending' ORDER BY id DESC LIMIT 10")
                deps = cursor.fetchall()
                conn.close()

                if not deps:
                    await query.edit_message_text("📥 **لا توجد طلبات شحن معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    txt = "📥 **طلبات الشحن المعلقة ⏳:**\n✨ ─────────────────── ✨\n"
                    kb = []
                    for d in deps:
                        txt += f"📋 **طلب #{d['id']}** | العميل: `{d['user_id']}`\n💳 {d['method']} | 💰 `{d['amount']}` NPS\n📝 الإشعار: `{d['tx_id']}`\n⏱️ `{d['timestamp']}`\n\n"
                        kb.append([
                            InlineKeyboardButton(f"✅ قبول #{d['id']}", callback_data=f"app_dep_{d['id']}"),
                            InlineKeyboardButton(f"❌ رفض #{d['id']}", callback_data=f"rej_dep_{d['id']}")
                        ])
                    kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
                    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_withdraws":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY id DESC LIMIT 10")
                ws = cursor.fetchall()
                conn.close()

                if not ws:
                    await query.edit_message_text("💸 **لا توجد طلبات سحب معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    txt = "💸 **طلبات السحب المعلقة ⏳:**\n✨ ─────────────────── ✨\n"
                    kb = []
                    for w in ws:
                        txt += f"📋 **طلب #{w['id']}** | العميل: `{w['user_id']}`\n💳 {w['method']} | 🔢 الحساب: `{w['acc_code']}`\n💰 المبلغ: `{w['amount']}` NPS (الصافي: `{w['net_amount']}` NPS)\n⏱️ `{w['timestamp']}`\n\n"
                        kb.append([
                            InlineKeyboardButton(f"✅ موافقة ودفع #{w['id']}", callback_data=f"app_w_{w['id']}"),
                            InlineKeyboardButton(f"❌ رفض واسترجاع #{w['id']}", callback_data=f"rej_w_{w['id']}")
                        ])
                    kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
                    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_offers_menu":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM offers")
                offs = cursor.fetchall()
                conn.close()

                txt = "🎁 **قائمة العروض الحالية 🔥:**\n✨ ─────────────────── ✨\n"
                kb = []
                for o in offs:
                    txt += f"📌 **{o['title']}** (ID: `{o['id']}`)\n📝 {o['description']}\n\n"
                    kb.append([InlineKeyboardButton(f"❌ حذف {o['title']}", callback_data=f"adm_del_offer_{o['id']}")])

                kb.append([InlineKeyboardButton("➕ إضافة عرض جديد 🔥", callback_data="adm_add_offer")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_offer":
                u["step"] = "adm_input_add_offer"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل تفاصيل العرض بالصيغة:**\n`العنوان | الوصف | الرابط (اختياري)`\n\nمثال:\n`عرض الشحن المضاعف | اشحن 1000 واصل على 500 بونص | https://t.me/example`", parse_mode="Markdown", reply_markup=cancel_keyboard("adm_offers_menu"))
                return

            if data.startswith("adm_del_offer_"):
                off_id = data.replace("adm_del_offer_", "")
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM offers WHERE id = ?", (off_id,))
                conn.commit()
                conn.close()
                await query.edit_message_text(f"✅ تم حذف العرض #{off_id}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العروض 🔥", callback_data="adm_offers_menu")]]))
                return

            if data == "adm_code_restrictions":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 إلغاء جميع التقييدات الزمنية للجميع", callback_data="adm_reset_code_restrictions")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ])
                await query.edit_message_text("🔓 **إلغاء تقييدات الأكواد الزمنية (6 ساعات):**", reply_markup=kb)
                return

            if data == "adm_reset_code_restrictions":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM code_restrictions")
                conn.commit()
                conn.close()
                await query.edit_message_text("✅ **تم إلغاء التقييد الزمني لاستخدام الأكواد لجميع المستخدمين بنجاح!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_active_codes":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM gift_codes")
                codes = cursor.fetchall()
                conn.close()

                if not codes:
                    txt = "🎟️ **لا توجد أكواد هدايا نشطة حالياً.**"
                    kb = [
                        [InlineKeyboardButton("🎫 إلغاء كود يدوياً ❌", callback_data="adm_del_code_manual")],
                        [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                    ]
                else:
                    txt = "🎟️ **الأكواد النشطة الحالية 🎁:**\n✨ ─────────────────── ✨\n"
                    kb = []
                    for c in codes:
                        txt += f"🎫 الكود: `{c['code']}` | 💰 القيمة: `{c['amount']}` | 🔢 المتبقي: `{c['uses_left']}`\n"
                        kb.append([InlineKeyboardButton(f"❌ إلغاء الكود {c['code']}", callback_data=f"adm_del_code_{c['code']}")])

                    kb.append([InlineKeyboardButton("🎫 إلغاء كود يدوياً ❌", callback_data="adm_del_code_manual")])
                    kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data.startswith("adm_del_code_") and data != "adm_del_code_manual":
                c_code = data.replace("adm_del_code_", "")
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM gift_codes WHERE code = ?", (c_code,))
                conn.commit()
                conn.close()
                await query.edit_message_text(f"✅ تم إلغاء الكود `{c_code}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 الأكواد النشطة 🎟️", callback_data="adm_active_codes")]]))
                return

            if data == "adm_del_code_manual":
                u["step"] = "adm_input_del_code_manual"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل رمز الكود المراد حذفه وإلغائه يدوياً:**", reply_markup=cancel_keyboard("adm_active_codes"))
                return

            if data == "adm_set_dep_bonus":
                u["step"] = "adm_input_dep_bonus"
                save_user(u)
                curr = get_setting("deposit_bonus_percent", "0")
                await query.edit_message_text(f"✍️ **النسبة الحالية لبونص الشحن هي `{curr}%`.**\nأدخل النسبة المئوية الجديدة بالأرقام (مثال: `10`):", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_w_commission":
                u["step"] = "adm_input_w_commission"
                save_user(u)
                curr = get_setting("withdraw_commission_percent", "0")
                await query.edit_message_text(f"✍️ **النسبة الحالية لعمولة السحب هي `{curr}%`.**\nأدخل النسبة المئوية الجديدة بالأرقام (مثال: `5`):", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_dep":
                u["step"] = "adm_input_min_dep"
                save_user(u)
                curr = get_setting("min_deposit", "50")
                await query.edit_message_text(f"✍️ **الحد الأدنى الحالي للشحن هو `{curr}` NPS.**\nأدخل الحد الأدنى الجديد بالأرقام:", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_w":
                u["step"] = "adm_input_min_w"
                save_user(u)
                curr = get_setting("min_withdraw", "1500")
                await query.edit_message_text(f"✍️ **الحد الأدنى الحالي للسحب هو `{curr}` NPS.**\nأدخل الحد الأدنى الجديد بالأرقام:", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_bal":
                u["step"] = "adm_input_add_bal"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID العميل والمبلغ المراد إضافته (مثال: `7255100997 100`):**", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_sub_bal":
                u["step"] = "adm_input_sub_bal"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID العميل والمبلغ المراد خصمه (مثال: `7255100997 50`):**", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_batch_codes":
                u["step"] = "adm_input_batch_codes"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل بيانات الدفعة بالصيغة:**\n`عدد_الأكواد المبلغ عدد_الاستخدامات`\n\nمثال:\n`5 100 1`", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_make_gift":
                u["step"] = "adm_input_make_gift"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل بيانات الكود الفردي بالصيغة:**\n`رمز_الكود المبلغ عدد_الاستخدامات`\n\nمثال:\n`GOLDEN2026 500 1`", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_user_info":
                u["step"] = "adm_input_user_info"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل معرف ID العميل لعرض تفاصيله الكاملة:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_players_log":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT full_name, user_id, games_played, total_spent, balance FROM users ORDER BY games_played DESC LIMIT 10")
                top_players = cursor.fetchall()
                conn.close()

                txt = "🏆 **أبرز اللاعبين الأكثر نشاطاً في المنصة 📊:**\n✨ ─────────────────── ✨\n"
                for p in top_players:
                    txt += f"👤 {p['full_name']} (`{p['user_id']}`)\n🎮 لعب: `{p['games_played']}` مرة | 💸 أنفق: `{p['total_spent']:,.2f}` NPS | 💰 رصيده: `{p['balance']:,.2f}` NPS\n\n"
                txt += "✨ ─────────────────── ✨"
                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_list_admins":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM admins")
                adms = [row[0] for row in cursor.fetchall()]
                conn.close()

                txt = "👮 **قائمة المسؤولين (الأدمنية) الحاليين:**\n✨ ─────────────────── ✨\n"
                for a in adms:
                    txt += f"👑 ID: `{a}`\n"
                txt += "✨ ─────────────────── ✨"

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("👮 إضافة أدمن ➕", callback_data="adm_add_admin"), InlineKeyboardButton("❌ إزالة أدمن ➖", callback_data="adm_del_admin")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ])
                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)
                return

            if data == "adm_stats":
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), SUM(balance), SUM(games_played), SUM(total_spent) FROM users")
                total_users, total_bal, total_games, total_spent = cursor.fetchone()

                cursor.execute("SELECT SUM(amount) FROM deposits WHERE status = 'approved'")
                approved_deps = cursor.fetchone()[0] or 0.0

                cursor.execute("SELECT SUM(amount) FROM withdrawals WHERE status = 'approved'")
                approved_ws = cursor.fetchone()[0] or 0.0

                conn.close()

                total_users = total_users or 0
                total_bal = total_bal or 0.0
                total_games = total_games or 0
                total_spent = total_spent or 0.0

                txt = (
                    f"📊 **الإحصائيات الشاملة للمنصة 🚀**\n"
                    f"✨ ─────────────────── ✨\n"
                    f"👥 **إجمالي المستخدمين:** `{total_users}` لاعب\n"
                    f"💰 **مجموع أرصدة اللاعبين:** `{total_bal:,.2f}` NPS\n"
                    f"📥 **إجمالي المقبول للشحن:** `{approved_deps:,.2f}` NPS\n"
                    f"💸 **إجمالي المقبول للسحب:** `{approved_ws:,.2f}` NPS\n"
                    f"🎮 **إجمالي الألعاب الملعوبة:** `{total_games}` لعبة\n"
                    f"💵 **إجمالي الإنفاق في الألعاب:** `{total_spent:,.2f}` NPS\n"
                    f"✨ ─────────────────── ✨"
                )
                await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_user_boost":
                u["step"] = "adm_input_user_boost"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID العميل ونسبة الحظ الإضافية (مثال: `7255100997 10`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_ban":
                u["step"] = "adm_input_ban_id"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID العميل المراد حظره:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_unban":
                u["step"] = "adm_input_unban_id"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID العميل المراد فك حظره:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_txt":
                u["step"] = "adm_input_bc_txt"
                save_user(u)
                await query.edit_message_text("✍️ **أرسل النص المراد إذاعته لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_img":
                u["step"] = "adm_input_bc_img"
                save_user(u)
                await query.edit_message_text("🖼️ **أرسل الصورة مع الوصف المراد إذاعتها لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_pm_txt":
                u["step"] = "adm_input_pm_txt"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل بيانات الرسالة بالصيغة:**\n`ID_العميل | النص`\n\nمثال:\n`7255100997 | تم شحن حسابك بنجاح`", parse_mode="Markdown", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_admin":
                u["step"] = "adm_input_add_admin"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID المستخدم لمنحه صلاحيات الأدمن:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_del_admin":
                u["step"] = "adm_input_del_admin"
                save_user(u)
                await query.edit_message_text("✍️ **أدخل ID المستخدم لسحب صلاحيات الأدمن منه:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

    except Exception as e:
        logger.error(f"Error in handle_callback_query: {e}")

# ----------------------------------------------------
# 12. نقطة التشغيل الرئيسية (Main Function)
# ----------------------------------------------------
def main():
    global bot_loop, tg_app
    
    application = Application.builder().token(BOT_TOKEN).build()
    tg_app = application

    # تسجيل كافة المعالجات والروابط
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Golden Games Telegram Bot initialized successfully!")
    
    loop = asyncio.get_event_loop()
    global bot_loop
    bot_loop = loop

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
