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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8704181888:AAGf1iuRI9ZTSN6_lkCzELMyrQzU_lAsWec")
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
    try:
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
                referral_credited INTEGER DEFAULT 0,
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
            ("referral_credited", "INTEGER DEFAULT 0"),
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
        cursor.execute('CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, amount REAL, tx_id TEXT, photo_file_id TEXT, status TEXT DEFAULT "pending", timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, channel_msg_id INTEGER DEFAULT 0)')
        cursor.execute('CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, account_code TEXT, amount REAL, net_amount REAL, status TEXT DEFAULT "pending", timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, channel_msg_id INTEGER DEFAULT 0)')
        cursor.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, amount REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
        cursor.execute('CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, link TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, image_url TEXT, game_url TEXT, is_active INTEGER DEFAULT 1)')
        cursor.execute('CREATE TABLE IF NOT EXISTS code_restrictions (user_id INTEGER PRIMARY KEY, last_used INTEGER)')

        try: cursor.execute("ALTER TABLE deposits ADD COLUMN channel_msg_id INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE withdrawals ADD COLUMN channel_msg_id INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass

        if DEFAULT_ADMIN_ID:
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
            
        defaults = [
            ('maintenance_mode', '1'),  # تفعيل وضع الصيانة تلقائياً عند التشغيل
            ('welcome_bonus', '50'),
            ('welcome_bonus_enabled', '1'),
            ('referral_reward', '50'),
            ('referral_reward_enabled', '0'),
            ('referral_spin_enabled', '1'),
            ('referral_burn_percent', '10'),
            ('deposit_bonus_percent', '0'),
            ('withdraw_commission_percent', '0'),
            ('min_withdraw', '1500'),
            ('min_deposit', '50'),
            ('tx_channel_id', ''),
            ('wheel_prob_luck', '90'),
            ('wheel_prob_5', '15'),
            ('wheel_prob_10', '10'),
            ('wheel_prob_15', '0'),
            ('wheel_prob_try_again', '5'),
            ('wheel_prob_25', '1'),
            ('wheel_prob_50', '0'),
            ('wheel_prob_100', '0'),
            ('wheel_prob_250', '0'),
            ('wheel_prob_dep_bonus_20', '0'),
            ('wheel_prob_500', '0'),
            ('wheel_prob_1000', '0')
        ]
        for key, val in defaults:
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

        # حسابات الشحن الافتراضية
        cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (1, 'شام كاش', 'a612b862fc9960e61333d48b86d0dcd3')")
        cursor.execute("UPDATE deposit_methods SET account_details = 'a612b862fc9960e61333d48b86d0dcd3' WHERE id = 1")

        cursor.execute("INSERT OR IGNORE INTO deposit_methods (id, method_name, account_details) VALUES (2, 'سيريتل كاش', '00973427')")
        cursor.execute("UPDATE deposit_methods SET account_details = '00973427' WHERE id = 2")

        # حصر قنوات الاشتراك الإجباري في القنوات الرسمية الثلاث فقط وحذف أي قناة غريبة تلقائياً
        req_channels = [
            ('@lerafree', 'قناة المبرمج', 'https://t.me/lerafree'),
            ('@golden_game_b', 'قناة البوت', 'https://t.me/golden_game_b'),
            ('@goldennlera', 'كروب المسابقات', 'https://t.me/goldennlera')
        ]
        valid_ids = [ch[0] for ch in req_channels]
        cursor.execute("DELETE FROM channels WHERE channel_id NOT IN (?, ?, ?)", tuple(valid_ids))
        for ch_id, ch_title, ch_link in req_channels:
            cursor.execute("INSERT OR IGNORE INTO channels (channel_id, channel_title, channel_link) VALUES (?, ?, ?)", (ch_id, ch_title, ch_link))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")

init_db()

# ----------------------------------------------------
# 3. إشعارات القناة والتحقق
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
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='tx_channel_id'").fetchone()
        conn.close()
        if not row or not row["value"]:
            return 0

        ch_id = row["value"].strip()
        if await is_bot_admin_in_channel(bot, ch_id):
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
            if photo_file_id:
                msg = await bot.send_photo(chat_id=ch_id, photo=photo_file_id, caption=caption, parse_mode="Markdown")
            else:
                msg = await bot.send_message(chat_id=ch_id, text=caption, parse_mode="Markdown")

            conn = get_db()
            conn.execute("UPDATE deposits SET channel_msg_id = ? WHERE id = ?", (msg.message_id, dep_id))
            conn.commit()
            conn.close()
            return msg.message_id
    except Exception as e:
        logger.error(f"Error sending deposit notice to channel: {e}")
    return 0

async def notify_channel_approved_dep(bot, dep_id: int):
    try:
        conn = get_db()
        dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
        row = conn.execute("SELECT value FROM settings WHERE key='tx_channel_id'").fetchone()
        conn.close()

        if not row or not row["value"] or not dep or not dep["channel_msg_id"]:
            return

        ch_id = row["value"].strip()
        msg_id = dep["channel_msg_id"]

        if await is_bot_admin_in_channel(bot, ch_id):
            try:
                await bot.send_message(
                    chat_id=ch_id,
                    text=f"✅ **تمت الموافقة** على طلب الشحن رقم #{dep_id}",
                    reply_to_message_id=msg_id,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error sending channel dep approval notice: {e}")
    except Exception as e:
        logger.error(f"Error in notify_channel_approved_dep: {e}")

async def send_withdraw_to_channel(bot, w_id: int, user, method: str, acc_code: str, amount: float, net_amount: float):
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='tx_channel_id'").fetchone()
        conn.close()
        if not row or not row["value"]:
            return 0

        ch_id = row["value"].strip()
        if await is_bot_admin_in_channel(bot, ch_id):
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
            msg = await bot.send_message(chat_id=ch_id, text=caption, parse_mode="Markdown")

            conn = get_db()
            conn.execute("UPDATE withdrawals SET channel_msg_id = ? WHERE id = ?", (msg.message_id, w_id))
            conn.commit()
            conn.close()
            return msg.message_id
    except Exception as e:
        logger.error(f"Error sending withdraw notice to channel: {e}")
    return 0

async def notify_channel_approved_w(bot, w_id: int):
    try:
        conn = get_db()
        w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
        row = conn.execute("SELECT value FROM settings WHERE key='tx_channel_id'").fetchone()
        conn.close()

        if not row or not row["value"] or not w or not w["channel_msg_id"]:
            return

        ch_id = row["value"].strip()
        msg_id = w["channel_msg_id"]

        if await is_bot_admin_in_channel(bot, ch_id):
            try:
                await bot.send_message(
                    chat_id=ch_id,
                    text=f"✅ **تمت الموافقة** على طلب السحب رقم #{w_id}",
                    reply_to_message_id=msg_id,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error sending channel w approval notice: {e}")
    except Exception as e:
        logger.error(f"Error in notify_channel_approved_w: {e}")

# ----------------------------------------------------
# 4. التفاعل والإشعارات والاشتراك الإجباري
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
            except Exception as e:
                logger.warning(f"Failed to check membership for channel {ch['channel_id']}: {e}")

        return (len(unsubscribed) == 0), unsubscribed
    except Exception as e:
        logger.error(f"Error checking channel sub: {e}")
        return True, []

def build_sub_keyboard(unsubscribed_channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in unsubscribed_channels:
        title = ch["channel_title"] or "📢 قناة الاشتراك الإجباري"
        keyboard.append([InlineKeyboardButton(f"🔗 {title}", url=ch["channel_link"])])
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك الآن", callback_data="check_subscription_status")])
    return InlineKeyboardMarkup(keyboard)

def cancel_keyboard(target="back_to_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية والعودة", callback_data=target)]])

async def notify_admins(context_or_bot, text: str, reply_markup=None):
    try:
        bot = context_or_bot.bot if hasattr(context_or_bot, "bot") else context_or_bot
        conn = get_db()
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
        conn.close()
        for adm in admins:
            try:
                await bot.send_message(chat_id=adm["user_id"], text=text, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error notifying admins: {e}")

async def process_referral_on_captcha_passed(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not u or not u["referred_by"] or u["referral_credited"] == 1:
            conn.close()
            return

        ref_id = u["referred_by"]
        ref_reward_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"] == "1"
        ref_reward_amt = float(conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]) if ref_reward_enabled else 0.0
        ref_spin_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"] == "1"

        conn.execute("UPDATE users SET referrals_count = referrals_count + 1, active_referrals_count = active_referrals_count + 1 WHERE user_id = ?", (ref_id,))
        
        if ref_spin_enabled:
            conn.execute("UPDATE users SET free_spins = free_spins + 1 WHERE user_id = ?", (ref_id,))
        if ref_reward_amt > 0:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ref_reward_amt, ref_id))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (ref_id, f"مكافأة إحالة العميل {user_id}", ref_reward_amt))

        conn.execute("UPDATE users SET referral_credited = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

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
                conn.commit()
                conn.close()
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🎁 **مبارك! حصلت على البونص الترحيبي قدره `{welcome_bonus}` NPS لإتمام الانضمام بنجاح!**",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                return
        conn.close()
    except Exception as e:
        logger.error(f"Error processing welcome bonus: {e}")

async def process_wheel_win(bot, user_id: int, prize_amount: float, prize_title: str):
    try:
        conn = get_db()
        u = conn.execute("SELECT full_name, balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not u:
            conn.close()
            return

        bal_before = float(u["balance"])
        bal_after = bal_before + float(prize_amount)

        conn.execute("UPDATE users SET balance = ?, games_played = games_played + 1 WHERE user_id = ?", (bal_after, user_id))
        conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user_id, f"ربح في عجلة الحظ ({prize_title})", prize_amount))
        conn.commit()
        conn.close()

        try:
            user_msg = (
                f"🎉 **مبروك! لقد فزت في عجلة الحظ الكبرى!** 🎡\n"
                f"✨ ─────────────────── ✨\n"
                f"🏆 **الجائزة المكتسبة:** {prize_title}\n"
                f"💰 **المبلغ المضاف:** `{prize_amount:,.2f}` NPS\n"
                f"💳 **رصيدك الحالي:** `{bal_after:,.2f}` NPS\n"
                f"✨ ─────────────────── ✨"
            )
            await bot.send_message(chat_id=user_id, text=user_msg, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not send wheel win notice to user {user_id}: {e}")

        admin_msg = (
            f"🎡 **إشعار فوز جديد في عجلة الحظ!** 🎰\n"
            f"✨ ─────────────────── ✨\n"
            f"👤 **اللاعب:** {u['full_name']}\n"
            f"🆔 **المعرف (ID):** `{user_id}`\n"
            f"🏆 **الجائزة / الربح:** `{prize_amount:,.2f}` NPS ({prize_title})\n"
            f"📉 **الرصيد قبل الربح:** `{bal_before:,.2f}` NPS\n"
            f"📈 **الرصيد بعد الربح:** `{bal_after:,.2f}` NPS\n"
            f"📅 **الوقت:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"✨ ─────────────────── ✨"
        )
        await notify_admins(bot, admin_msg)
    except Exception as e:
        logger.error(f"Error in process_wheel_win: {e}")

# ----------------------------------------------------
# 5. القوائم ولوحات التحكم
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

def admin_panel_keyboard():
    try:
        conn = get_db()
        maint_status = "🔴 مفعل (الصيانة مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
        
        welcome_enabled = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"] == "1"
        welcome_status = "🟢 مفعل" if welcome_enabled else "🔴 معطل"
        welcome_amt = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus'").fetchone()["value"]
        
        ref_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"] == "1"
        ref_status = "🟢 مفعل" if ref_enabled else "🔴 معطل"
        ref_amt = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()["value"]

        ref_spin_enabled = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"] == "1"
        ref_spin_status = "🟢 مفعل" if ref_spin_enabled else "🔴 معطل"

        tx_ch = conn.execute("SELECT value FROM settings WHERE key='tx_channel_id'").fetchone()
        tx_ch_val = tx_ch["value"] if tx_ch and tx_ch["value"] else "غير محددة"
        conn.close()
    except Exception:
        maint_status, welcome_status, welcome_amt, ref_status, ref_amt, ref_spin_status, tx_ch_val = "غير معروف", "غير معروف", "50", "غير معروف", "50", "غير معروف", "غير محددة"

    keyboard = [
        [InlineKeyboardButton(f"🛠️ وضع الصيانة: {maint_status}", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🎡 خوارزمية عجلة الحظ 🎯", callback_data="adm_wheel_algo"), InlineKeyboardButton("🎯 حظ لاعب معين ⚡", callback_data="adm_user_boost")],
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
# 6. المعالجات والأوامر الرئيسية
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        chat_id = update.effective_chat.id

        await send_start_reaction(context, chat_id, update.message.message_id)

        if is_maintenance_active() and not is_admin_user(user.id):
            await update.message.reply_text("🛠️ **السيرفر حالياً في حالة صيانة وتحديثات دورية.**\nيرجى المحاولة لاحقاً.")
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

        step = u["step"]
        conn.close()

        if step == "captcha":
            await update.message.reply_text("⚠️ **يرجى الإجابة على سؤال الأمان أولاً:**\nما هي الحلاوة الجبن الأصلية؟ (حمصية أم حموية؟)")
            return

        if not u["phone_verified"] or step == "phone":
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
        conn = get_db()
        u = conn.execute("SELECT balance, free_spins FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        
        bal = u["balance"] if u else 0.0
        spins = u["free_spins"] if u else 0
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
# 7. معالجة الصور والإثباتات
# ----------------------------------------------------
async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
            conn.close()

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

            cursor = conn.execute("INSERT INTO deposits (user_id, method, amount, tx_id, photo_file_id) VALUES (?, ?, ?, ?, ?)",
                                  (user.id, method, amt, f"إيصال مصور: {caption}", photo_file_id))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            dep_id = cursor.lastrowid
            conn.close()

            await update.message.reply_text("✅ **تم تقديم طلب الشحن مع صورة الإيصال بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            
            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, f"إيصال مصور: {caption}", photo_file_id)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            dep_text = f"📥 **طلب شحن جديد بإيصال مصور (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n💰 المبلغ: `{amt}` NPS\n📝 الوصف: {caption}"
            await notify_admins(context, dep_text, reply_markup=kb)
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
    except Exception as e:
        logger.error(f"Error handling photo messages: {e}")

# ----------------------------------------------------
# 8. معالجة التواصل والتوثيق
# ----------------------------------------------------
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        contact = update.message.contact
        
        if contact.user_id != user.id:
            await update.message.reply_text("❌ يرجى مشاركة رقم هاتفك الشخصي الخاص بك فقط.")
            return

        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()

        check_phone = conn.execute("SELECT user_id FROM users WHERE phone = ? AND phone_verified = 1 AND user_id != ?", (contact.phone_number, user.id)).fetchone()
        if check_phone:
            conn.close()
            await update.message.reply_text("❌ هذا الرقم موثق ومستعمل سابقاً في حساب آخر!", reply_markup=ReplyKeyboardRemove())
            return

        conn.execute(
            "UPDATE users SET phone = ?, is_verified = 1, phone_verified = 1, step = 'channels' WHERE user_id = ?",
            (contact.phone_number, user.id)
        )
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✅ **تم توثيق رقم هاتفك بنجاح!**",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        
        is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
        if not is_subscribed:
            await update.message.reply_text("⚠️ **الخطوة الأخيرة: يرجى الاشتراك القنوات لاستلام البونص ودخول البوت:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
        else:
            await process_welcome_bonus(user.id, context)
            await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)
    except Exception as e:
        logger.error(f"Error handling contact: {e}")

# ----------------------------------------------------
# 9. معالجة الرسائل النصية
# ----------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        text = update.message.text.strip() if update.message.text else ""

        if text.lower() in ["/start", "ستارت", "البدء", "بدء"]:
            await send_start_reaction(context, update.effective_chat.id, update.message.message_id)

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
                conn.execute("UPDATE users SET step = 'phone', captcha_answer = 'passed' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()

                await process_referral_on_captcha_passed(user.id, context)

                btn = ReplyKeyboardMarkup([[KeyboardButton("📱 مشاركة الرقم للتوثيق", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
                await update.message.reply_text("✅ **إجابة صحيحة 100%! ابن أصول.**\n\n📱 **الخطوة التالية: يرجى مشاركة رقم هاتفك للتوثيق:**", reply_markup=btn)
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
                await update.message.reply_text(f"❌ الحد الأدنى للشحن هو `{min_dep}` NPS.", reply_markup=cancel_keyboard())
                return

            dep_bonus_pct = float(conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"])
            bonus_val = amt * (dep_bonus_pct / 100.0)
            total_expected = amt + bonus_val

            context.user_data["dep_amount"] = amt
            method_name = context.user_data.get("dep_method", "غير محدد")
            acc_details = context.user_data.get("dep_acc_details", "غير متوفر")

            conn.execute("UPDATE users SET step = 'deposit_step_tx' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()

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

            cursor = conn.execute("INSERT INTO deposits (user_id, method, amount, tx_id) VALUES (?, ?, ?, ?)", (user.id, method, amt, text))
            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            dep_id = cursor.lastrowid
            conn.close()

            await update.message.reply_text("✅ تم تقديم طلب الشحن بنجاح وهو قيد المراجعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            
            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, text, None)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            await notify_admins(context, f"📥 **طلب شحن جديد (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الإشعار: `{text}`\n💰 المبلغ: `{amt}` NPS", reply_markup=kb)
            return

        if step == "withdraw_step_code":
            context.user_data["withdraw_code"] = text
            min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])
            comm_pct = float(conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"])
            
            conn.execute("UPDATE users SET step = 'withdraw_step_amount' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()

            msg = (
                f"✍️ **أدخل المبلغ المراد سحبه (NPS):**\n\n"
                f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` NPS\n"
                f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS\n"
                f"➖ **نسبة عمولة السحب:** `{comm_pct}%`"
            )
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard("btn_withdraw"))
            return

        if step == "withdraw_step_amount":
            try:
                amt = float(text)
            except ValueError:
                conn.close()
                await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("btn_withdraw"))
                return

            min_w = float(conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"])
            if amt < min_w or amt > u["balance"]:
                conn.close()
                await update.message.reply_text(f"❌ المبلغ غير متاح في رصيدك الحالي (`{u['balance']:,.2f}` NPS) أو أقل من حد السحب الأدنى (`{min_w}` NPS).", reply_markup=cancel_keyboard("btn_withdraw"))
                return

            comm_pct = float(conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"])
            comm_fee = amt * (comm_pct / 100.0)
            net_amt = amt - comm_fee

            context.user_data["withdraw_amount"] = amt
            context.user_data["withdraw_net"] = net_amt
            context.user_data["withdraw_fee"] = comm_fee
            context.user_data["withdraw_comm_pct"] = comm_pct

            method = context.user_data.get("withdraw_method", "غير محدد")
            acc_code = context.user_data.get("withdraw_code", "غير محدد")

            conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()

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

            bal_before = u["balance"]
            amt = g["amount"]
            bal_after = bal_before + amt

            conn.execute("UPDATE users SET balance = balance + ?, step = 'main' WHERE user_id = ?", (amt, user.id))
            
            if g["uses_left"] - 1 > 0:
                conn.execute("UPDATE gift_codes SET uses_left = uses_left - 1 WHERE code = ?", (text,))
            else:
                conn.execute("DELETE FROM gift_codes WHERE code = ?", (text,))

            conn.execute("INSERT INTO code_restrictions (user_id, last_used) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_used = ?", (user.id, now_ts, now_ts))
            conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (user.id, f"تفعيل كود هدية {text}", amt))
            conn.commit()
            conn.close()

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
            if step == "adm_input_welcome_amt":
                try:
                    amt = float(text)
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('welcome_bonus', ?)", (str(amt),))
                    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                    conn.commit()
                    conn.close()
                    await update.message.reply_text(f"✅ **تم تعديل قيمة البونص الترحيبي بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    conn.close()
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_ref_amt":
                try:
                    amt = float(text)
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_reward', ?)", (str(amt),))
                    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                    conn.commit()
                    conn.close()
                    await update.message.reply_text(f"✅ **تم تعديل قيمة مكافأة الإحالة بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    conn.close()
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_tx_channel":
                ch_val = text.strip()
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('tx_channel_id', ?)", (ch_val,))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()

                is_admin_in_ch = await is_bot_admin_in_channel(context.bot, ch_val)
                status_str = "✅ والبوت مشرف فيها بنجاح!" if is_admin_in_ch else "⚠️ تذكير: يرجى رفع البوت مشرفاً فيها برتبة إرسال رسائل لتفعيل الإشعارات تلقائياً."

                await update.message.reply_text(
                    f"📢 **تم حفظ قناة طلبات الشحن والسحب إلى:** `{ch_val}`\n{status_str}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]])
                )
                return

            if step.startswith("adm_edit_dep_acc_"):
                m_id = int(step.replace("adm_edit_dep_acc_", ""))
                conn.execute("UPDATE deposit_methods SET account_details = ? WHERE id = ?", (text, m_id))
                conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ تم تحديث حساب الشحن بنجاح إلى:\n`{text}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة إدارة الشحن 💳", callback_data="adm_dep_methods")]]))
                return

            if step == "adm_input_ref_payout":
                try:
                    parts = text.split()
                    tid, amt = int(parts[0]), float(parts[1])
                    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, tid))
                    conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (tid, "مستحقات إحالة نشطة 10 أيام", amt))
                    conn.execute("UPDATE users SET step = 'main' WHERE user_id = ?", (user.id,))
                    conn.commit()
                    conn.close()

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
                    conn.close()
                    await update.message.reply_text("❌ الصيغة خاطئة. مثال: `7255100997 500`", reply_markup=cancel_keyboard("adm_active_referrals"))
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
                    off_t = parts[0].strip()
                    off_d = parts[1].strip()
                    off_l = parts[2].strip() if len(parts) > 2 else ""
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
                        f"💰 **قيمة الكود:** `{amt}` NPS\n"
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
                    await update.message.reply_text(f"🎁 **تم إنشاء الكود الفردي بنجاح!**\n\n🎫 **الكود:** `{code_str}`\n💰 **المبلغ:** `{amt}` NPS\n👥 **الاستخدامات:** `{uses}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                    await update.message.reply_text(f"✅ تم تعديل حد الشحن الأدنى إلى `{val}` NPS.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                    await update.message.reply_text(f"✅ تم تعديل حد السحب الأدنى إلى `{val}` NPS.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                    await update.message.reply_text(f"✅ تم إضافة `{amt}` NPS للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try: await context.bot.send_message(tid, f"🎁 تم إضافة `{amt}` NPS لرصيدك من الإدارة!")
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
                    await update.message.reply_text(f"✅ تم خصم `{amt}` NPS من اللاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
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
                            f"💰 **الرصيد الحالي:** `{u_info['balance']:,.2f}` NPS\n"
                            f"🎡 **اللفات المجانية:** `{u_info['free_spins']}`\n"
                            f"👥 **عدد الإحالات:** `{u_info['referrals_count']}`\n"
                            f"🎯 **نسبة الحظ الخاص:** `{u_info['custom_boost']}%`\n"
                            f"✨ ─────────────────── ✨\n"
                            f"💳 **الشحن الناجح:** {dep_stats[0]} مرة | الإجمالي: `{dep_stats[1]:,.2f}` NPS\n"
                            f"💸 **السحب الناجح:** {w_stats[0]} مرة | الإجمالي: `{w_stats[1]:,.2f}` NPS\n"
                            f"🎁 **البونص المحصل:** {'نعم' if u_info['welcome_bonus_claimed'] else 'لا'}\n"
                            f"🎟️ **الأكواد المستعملة:** {codes_count} كود\n"
                            f"🎰 **إجمالي الرصيد المصروف باللعب:** `{u_info['total_spent']:,.2f}` NPS\n"
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
    except Exception as e:
        logger.error(f"Error handling text messages: {e}")

# --- معالجة بيانات تطبيقات الويب (Web App Data) ---
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if not update.effective_message or not update.effective_message.web_app_data:
            return
        raw_data = update.effective_message.web_app_data.data
        data = json.loads(raw_data)
        if data.get("action") == "wheel_win" or "amount" in data:
            amt = float(data.get("amount", 0))
            prize = str(data.get("prize", "جائزة عجلة الحظ"))
            await process_wheel_win(context.bot, user.id, amt, prize)
    except Exception as e:
        logger.error(f"Error handling WebApp data: {e}")

# ----------------------------------------------------
# 10. معالجة نقرات الأزرار التفاعلية (Callback Queries)
# ----------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    
    try:
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

        if data == "none":
            conn.close()
            return

        if data == "check_subscription_status":
            is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
            if is_subscribed:
                await query.message.edit_text("✅ **تم التأكد من اشتراكك بنجاح!**")
                await process_welcome_bonus(user.id, context)
                await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
            else:
                await query.message.edit_text("❌ **لم تشترك في القنوات المطلوبة بعد. يرجى الاشتراك أولاً:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
            conn.close()
            return

        if data == "back_to_main":
            conn.close()
            await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
            return

        # ------------------- أزرار العميل -------------------
        if data == "btn_offers":
            offers = conn.execute("SELECT * FROM offers").fetchall()
            conn.close()
            if not offers:
                await query.message.edit_text("🔥 **لا توجد عروض ترويجية متاحة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                msg = "🔥 **العروض الترويجية والخصومات الحالية:**\n\n"
                kb = []
                for off in offers:
                    msg += f"📌 **{off['title']}**\n📝 {off['description']}\n───────────────\n"
                    if off["link"]:
                        kb.append([InlineKeyboardButton(f"🔗 {off['title']}", url=off["link"])])
                kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data == "btn_deposit":
            dep_methods = conn.execute("SELECT * FROM deposit_methods").fetchall()
            min_dep = conn.execute("SELECT value FROM settings WHERE key='min_deposit'").fetchone()["value"]
            dep_bonus_pct = conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"]
            conn.close()

            if not dep_methods:
                await query.message.edit_text("⚠️ لا توجد وسائل شحن مضافة حالياً. يرجى التواصل مع الدعم الفني.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            kb = []
            for dm in dep_methods:
                kb.append([InlineKeyboardButton(f"💳 {dm['method_name']}", callback_data=f"user_select_dep_{dm['id']}")])
            kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])

            msg = (
                f"💳 **قسم شحن الحساب (تعبئة الرصيد):**\n"
                f"✨ ─────────────────── ✨\n"
                f"⚠️ **الحد الأدنى للشحن:** `{min_dep}` NPS\n"
                f"🎁 **بونص الشحن الإضافي الحالي:** `{dep_bonus_pct}%`\n"
                f"✨ ─────────────────── ✨\n\n"
                f"👇 اختر وسيلة الدفع المناسبة لك من الأزرار أدناه:"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("user_select_dep_"):
            dm_id = int(data.replace("user_select_dep_", ""))
            dm = conn.execute("SELECT * FROM deposit_methods WHERE id = ?", (dm_id,)).fetchone()
            if not dm:
                conn.close()
                await query.message.edit_text("❌ وسيلة الشحن غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["dep_method"] = dm["method_name"]
            context.user_data["dep_acc_details"] = dm["account_details"]

            conn.execute("UPDATE users SET step = 'deposit_step_amount' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()

            await query.message.edit_text(
                f"💳 **وسيلة الشحن المحددة:** {dm['method_name']}\n\n"
                f"✍️ **يرجى كتابة مبلغ الشحن المطلوب برقم صحيح (بالـ NPS):**",
                reply_markup=cancel_keyboard("btn_deposit")
            )
            return

        if data == "btn_withdraw":
            dep_methods = conn.execute("SELECT * FROM deposit_methods").fetchall()
            min_w = conn.execute("SELECT value FROM settings WHERE key='min_withdraw'").fetchone()["value"]
            comm_pct = conn.execute("SELECT value FROM settings WHERE key='withdraw_commission_percent'").fetchone()["value"]
            conn.close()

            if not dep_methods:
                await query.message.edit_text("⚠️ لا توجد طرق سحب مضافة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            kb = []
            for dm in dep_methods:
                kb.append([InlineKeyboardButton(f"💸 {dm['method_name']}", callback_data=f"user_select_w_{dm['id']}")])
            kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])

            msg = (
                f"💸 **قسم سحب الأرباح والعمولات:**\n"
                f"✨ ─────────────────── ✨\n"
                f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` NPS\n"
                f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS\n"
                f"➖ **نسبة عمولة السحب:** `{comm_pct}%`\n"
                f"✨ ─────────────────── ✨\n\n"
                f"👇 اختر وسيلة استلام الأرباح المفضلّة:"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("user_select_w_"):
            dm_id = int(data.replace("user_select_w_", ""))
            dm = conn.execute("SELECT * FROM deposit_methods WHERE id = ?", (dm_id,)).fetchone()
            conn.close()

            if not dm:
                await query.message.edit_text("❌ وسيلة غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["withdraw_method"] = dm["method_name"]

            conn = get_db()
            conn.execute("UPDATE users SET step = 'withdraw_step_code' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()

            await query.message.edit_text(
                f"💳 **طريقة السحب:** {dm['method_name']}\n\n"
                f"✍️ **أدخل رقم المحفظة أو الحساب المراد التحويل إليه:**",
                reply_markup=cancel_keyboard("btn_withdraw")
            )
            return

        if data == "confirm_withdraw":
            amt = context.user_data.get("withdraw_amount", 0.0)
            net_amt = context.user_data.get("withdraw_net", 0.0)
            method = context.user_data.get("withdraw_method", "غير محدد")
            acc_code = context.user_data.get("withdraw_code", "غير محدد")

            if amt <= 0 or u["balance"] < amt:
                conn.close()
                await query.message.edit_text("❌ رصيدك غير كافٍ لإتمام السحب.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, user.id))
            cursor = conn.execute("INSERT INTO withdrawals (user_id, method, account_code, amount, net_amount) VALUES (?, ?, ?, ?, ?)",
                                  (user.id, method, acc_code, amt, net_amt))
            conn.commit()
            w_id = cursor.lastrowid
            conn.close()

            await query.message.edit_text("✅ **تم تقديم طلب السحب بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_withdraw_to_channel(context.bot, w_id, user, method, acc_code, amt, net_amt)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_w_{w_id}")]])
            await notify_admins(context, f"💸 **طلب سحب جديد (# {w_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الحساب: `{acc_code}`\n💰 المبلغ المطلوب: `{amt}` NPS\n💵 الصافي: `{net_amt}` NPS", reply_markup=kb)
            return

        if data == "btn_account":
            conn.close()
            msg = (
                f"📌 **ملف الحساب الشخصي:**\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **الاسم:** {user.full_name}\n"
                f"🆔 **المعرف:** `{user.id}`\n"
                f"📱 **رقم الهاتف:** `{u['phone'] or 'غير مرتبط'}`\n"
                f"💰 **رصيدك الحالي:** `{u['balance']:,.2f}` NPS\n"
                f"🎡 **اللفات المجانية:** `{u['free_spins']}` لفة\n"
                f"👥 **عدد إحالاتك الناجحة:** `{u['referrals_count']}`\n"
                f"✨ ─────────────────── ✨"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_referral":
            conn.close()
            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start={user.id}"
            msg = (
                f"🎯 **نظام الإحالة ودعوة الأصدقاء:** 🚀\n"
                f"✨ ─────────────────── ✨\n"
                f"شارك رابط الإحالة الخاص بك واحصل على مكافآت فورية ولفات مجانية لكل صديق ينضم عبر رابطك!\n\n"
                f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
                f"📊 **عدد إحالاتك الناجحة:** `{u['referrals_count']}` صديق\n"
                f"✨ ─────────────────── ✨"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_send_proof":
            conn.execute("UPDATE users SET step = 'user_upload_proof' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("📸 **يرجى إرسال صورة إثبات الإصابة أو الفوز الآن (أو كتابة التفاصيل بنص فوري):**", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_gift":
            conn.execute("UPDATE users SET step = 'input_gift_code' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("🧨 **أدخل كود الهدية الخاص بك الآن لتفعيله:**", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_buy_bot":
            conn.close()
            await query.message.edit_text("🍷 **طلب بوت خاص وشخصي:**\n\nلطلب بوت خاص ألعاب أو برمجيات ممتازة تواصل مع المبرمج المباشر عبر قناة الدعم الرسمية: @lerafree", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_logs":
            logs = conn.execute("SELECT * FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,)).fetchall()
            conn.close()
            if not logs:
                await query.message.edit_text("🛑 **لا توجد عمليات مسجلة في حسابك حتى الآن.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                msg = "🛑 **سجل أحدث العمليات في حسابك:**\n\n"
                for l in logs:
                    msg += f"🔹 {l['action']} | `{l['amount']:,.2f}` NPS | {l['timestamp']}\n"
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_support":
            conn.execute("UPDATE users SET step = 'input_support_msg' WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await query.message.edit_text("🌶️ **اكتب رسالتك للدعم الفني المباشر وسنقوم بالرد عليك في أسرع وقت:**", reply_markup=cancel_keyboard("back_to_main"))
            return

        # ------------------- أزرار لوحة التحكم للإدارة -------------------
        if is_admin:
            if data == "open_admin_panel":
                conn.close()
                await query.message.edit_text("👮‍♂️ **أهلاً بك في لوحة الإدارة العليا والتحكم الكامل بالمنصة:**", reply_markup=admin_panel_keyboard())
                return

            if data == "adm_toggle_maint":
                curr = conn.execute("SELECT value FROM settings WHERE key='maintenance_mode'").fetchone()["value"]
                new_val = "0" if curr == "1" else "1"
                conn.execute("UPDATE settings SET value = ? WHERE key='maintenance_mode'", (new_val,))
                conn.commit()
                conn.close()
                m_txt = "🟢 تم معالجة إيقاف وضع الصيانة وإتاحة البوت للمستخدمين." if new_val == "0" else "🔴 تم تفعيل وضع الصيانة وحظر دخول العوام."
                await query.message.edit_text(m_txt, reply_markup=admin_panel_keyboard())
                return

            if data == "adm_toggle_welcome":
                curr = conn.execute("SELECT value FROM settings WHERE key='welcome_bonus_enabled'").fetchone()["value"]
                new_val = "0" if curr == "1" else "1"
                conn.execute("UPDATE settings SET value = ? WHERE key='welcome_bonus_enabled'", (new_val,))
                conn.commit()
                conn.close()
                await query.message.edit_text(f"✅ تم تغيير حالة البونص الترحيبي بنجاح.", reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_welcome_amt":
                conn.execute("UPDATE users SET step = 'adm_input_welcome_amt' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✏️ **أدخل قيمة البونص الترحيبي الجديدة (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_toggle_ref_bonus":
                curr = conn.execute("SELECT value FROM settings WHERE key='referral_reward_enabled'").fetchone()["value"]
                new_val = "0" if curr == "1" else "1"
                conn.execute("UPDATE settings SET value = ? WHERE key='referral_reward_enabled'", (new_val,))
                conn.commit()
                conn.close()
                await query.message.edit_text(f"✅ تم تغيير حالة مكافأة الإحالة بنجاح.", reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_ref_amt":
                conn.execute("UPDATE users SET step = 'adm_input_ref_amt' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✏️ **أدخل قيمة مكافأة الإحالة الجديدة (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_toggle_ref_spin":
                curr = conn.execute("SELECT value FROM settings WHERE key='referral_spin_enabled'").fetchone()["value"]
                new_val = "0" if curr == "1" else "1"
                conn.execute("UPDATE settings SET value = ? WHERE key='referral_spin_enabled'", (new_val,))
                conn.commit()
                conn.close()
                await query.message.edit_text(f"✅ تم تغيير حالة لفة الإحالة المجانية.", reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_tx_channel":
                conn.execute("UPDATE users SET step = 'adm_input_tx_channel' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("📢 **أدخل معرف قناة طلبات الشحن والسحب الجديدة (مثال: `@my_channel`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_deposits":
                deps = conn.execute("SELECT d.*, u.full_name FROM deposits d JOIN users u ON d.user_id = u.user_id WHERE d.status = 'pending' ORDER BY d.id DESC LIMIT 10").fetchall()
                conn.close()
                if not deps:
                    await query.message.edit_text("📥 **لا توجد طلبات شحن معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    for dep in deps:
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep['id']}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep['id']}")]])
                        await context.bot.send_message(chat_id=user.id, text=f"📥 **طلب شحن معلق (# {dep['id']}):**\n👤 {dep['full_name']} (`{dep['user_id']}`)\n💳 الطريقة: {dep['method']}\n💰 المبلغ: `{dep['amount']}` NPS\n📝 الإشعار: `{dep['tx_id']}`", reply_markup=kb, parse_mode="Markdown")
                    await query.message.edit_text("📋 تم عرض الطلبات المعلقة أعلاه.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data.startswith("app_dep_"):
                dep_id = int(data.replace("app_dep_", ""))
                dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
                if not dep or dep["status"] != "pending":
                    conn.close()
                    await query.message.edit_text("❌ الطلب معالج سابقاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    return

                dep_bonus_pct = float(conn.execute("SELECT value FROM settings WHERE key='deposit_bonus_percent'").fetchone()["value"])
                bonus_val = dep["amount"] * (dep_bonus_pct / 100.0)
                tot_amt = dep["amount"] + bonus_val

                conn.execute("UPDATE deposits SET status = 'approved' WHERE id = ?", (dep_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (tot_amt, dep["user_id"]))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (dep["user_id"], f"تعبئة شحن قبول طلب {dep_id}", tot_amt))
                conn.commit()
                conn.close()

                await query.message.edit_text(f"✅ تم القبول وتعبئة `{tot_amt}` NPS للعميل بنجاح!")
                try: await context.bot.send_message(dep["user_id"], f"🎉 **تم قبول طلب الشحن الخاص بك (# {dep_id}) وإضافة `{tot_amt}` NPS لرصيدك!**")
                except: pass
                await notify_channel_approved_dep(context.bot, dep_id)
                return

            if data.startswith("rej_dep_"):
                dep_id = int(data.replace("rej_dep_", ""))
                conn.execute("UPDATE deposits SET status = 'rejected' WHERE id = ?", (dep_id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("❌ تم رفض طلب الشحن.")
                return

            if data == "adm_withdraws":
                ws = conn.execute("SELECT w.*, u.full_name FROM withdrawals w JOIN users u ON w.user_id = u.user_id WHERE w.status = 'pending' ORDER BY w.id DESC LIMIT 10").fetchall()
                conn.close()
                if not ws:
                    await query.message.edit_text("💸 **لا توجد طلبات سحب معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    for w in ws:
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w['id']}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_w_{w['id']}")]])
                        await context.bot.send_message(chat_id=user.id, text=f"💸 **طلب سحب معلق (# {w['id']}):**\n👤 {w['full_name']} (`{w['user_id']}`)\n💳 الطريقة: {w['method']}\n🔢 الحساب: `{w['account_code']}`\n💰 المبلغ: `{w['amount']}` NPS\n💵 الصافي: `{w['net_amount']}` NPS", reply_markup=kb, parse_mode="Markdown")
                    await query.message.edit_text("📋 تم عرض طلبات السحب المعلقة أعلاه.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data.startswith("app_w_"):
                w_id = int(data.replace("app_w_", ""))
                w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
                if not w or w["status"] != "pending":
                    conn.close()
                    await query.message.edit_text("❌ الطلب معالج سابقاً.")
                    return

                conn.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
                conn.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)", (w["user_id"], f"سحب أرباح مقبول {w_id}", -w["amount"]))
                conn.commit()
                conn.close()

                await query.message.edit_text(f"✅ تم تأكيد دفع ودفع طلب السحب رقم #{w_id} بنجاح!")
                try: await context.bot.send_message(w["user_id"], f"🎉 **تم قبول طلب السحب الخاص بك (# {w_id}) وتحويل المبلغ لـ `{w['account_code']}` بنجاح!**")
                except: pass
                await notify_channel_approved_w(context.bot, w_id)
                return

            if data.startswith("rej_w_"):
                w_id = int(data.replace("rej_w_", ""))
                w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
                if not w or w["status"] != "pending":
                    conn.close()
                    await query.message.edit_text("❌ الطلب معالج سابقاً.")
                    return

                conn.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w["amount"], w["user_id"]))
                conn.commit()
                conn.close()

                await query.message.edit_text(f"❌ تم رفض طلب السحب وإعادة الرصيد `{w['amount']}` للعميل.")
                try: await context.bot.send_message(w["user_id"], f"❌ **تم رفض طلب السحب الخاص بك (# {w_id}) وإعادة المبلغ لرصيدك.**")
                except: pass
                return

            if data == "adm_stats":
                u_cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                tot_bal = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM users").fetchone()[0]
                tot_dep = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status='approved'").fetchone()[0]
                tot_w = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status='approved'").fetchone()[0]
                conn.close()

                msg = (
                    f"📊 **الإحصائيات الشاملة للمنصة:**\n"
                    f"✨ ─────────────────── ✨\n"
                    f"👥 **إجمالي المستخدمين:** `{u_cnt}` مستخدم\n"
                    f"💰 **إجمالي رصيد المستخدمين:** `{tot_bal:,.2f}` NPS\n"
                    f"💳 **إجمالي الشحن المقبول:** `{tot_dep:,.2f}` NPS\n"
                    f"💸 **إجمالي السحب المقبول:** `{tot_w:,.2f}` NPS\n"
                    f"✨ ─────────────────── ✨"
                )
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_dep_methods":
                conn.close()
                kb = [
                    [InlineKeyboardButton("➕ إضافة طريقة شحن 💳", callback_data="adm_add_dep_meth")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text("💳 **إدارة وسائل وحسابات الشحن:**", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_dep_meth":
                conn.execute("UPDATE users SET step = 'adm_input_add_dep_meth' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✍️ **أدخل اسم الوسيلة وتفاصيل الحساب مفصولة بـ `|`**\nمثال: `شام كاش | a612b862fc9960e61333d48b86d0dcd3`", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if data == "adm_offers_menu":
                conn.close()
                kb = [
                    [InlineKeyboardButton("➕ إضافة عرض جديد 🔥", callback_data="adm_add_offer")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text("🔥 **إدارة العروض الترويجية والخصومات:**", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_offer":
                conn.execute("UPDATE users SET step = 'adm_input_add_offer' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✍️ **أدخل بيانات العرض بالصيغة:**\n`العنوان | الوصف | الرابط (اختياري)`", reply_markup=cancel_keyboard("adm_offers_menu"))
                return

            if data == "adm_channels_menu":
                conn.close()
                kb = [
                    [InlineKeyboardButton("➕ إضافة قناة رسمية 🚀", callback_data="adm_add_channel")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text("📢 **إدارة قنوات الاشتراك الصارم المنظمة:**", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_add_channel":
                conn.execute("UPDATE users SET step = 'adm_input_add_channel' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✍️ **أدخل القناة بالصيغة:**\n`@ChannelID | العنوان | https://t.me/Channel`", reply_markup=cancel_keyboard("adm_channels_menu"))
                return

            if data == "adm_grant_spins_menu":
                conn.execute("UPDATE users SET step = 'adm_input_grant_spin_user' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🎁 **منح لفات مجانية للاعب:**\nأدخل ID اللاعب وعدد اللفات مفصولة بمسافة:\nمثال: `7255100997 5`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_dep_bonus":
                conn.execute("UPDATE users SET step = 'adm_input_dep_bonus' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("➕ **أدخل نسبة بونص الشحن الإضافي (%) (مثال: `10`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_w_commission":
                conn.execute("UPDATE users SET step = 'adm_input_w_commission' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("➖ **أدخل نسبة عمولة السحب (%) (مثال: `5`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_dep":
                conn.execute("UPDATE users SET step = 'adm_input_min_dep' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("💰 **أدخل الحد الأدنى المسموح للشحن (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_w":
                conn.execute("UPDATE users SET step = 'adm_input_min_w' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("💸 **أدخل الحد الأدنى المسموح للسحب (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_bal":
                conn.execute("UPDATE users SET step = 'adm_input_add_bal' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("💎 **إضافة رصيد للاعب:**\nأدخل ID والكمية مفصولة بمسافة (مثال: `7255100997 500`)", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_sub_bal":
                conn.execute("UPDATE users SET step = 'adm_input_sub_bal' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🔻 **خصم رصيد من لاعب:**\nأدخل ID والكمية مفصولة بمسافة (مثال: `7255100997 100`)", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_batch_codes":
                conn.execute("UPDATE users SET step = 'adm_input_batch_codes' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🎟️ **توليد دفعة أكواد هدية:**\nالصيغة: `بادئة المبلغ الاستخدامات_لكل_كود العدد`\nمثال: `GOLDEN 50 1 10`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_make_gift":
                conn.execute("UPDATE users SET step = 'adm_input_make_gift' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🎫 **إنشاء كود فردي:**\nالصيغة: `الكود المبلغ الاستخدامات`\nمثال: `VIP100 500 10`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_user_info":
                conn.execute("UPDATE users SET step = 'adm_input_user_info' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🔍 **أدخل ID العميل لعرض تقريره الكامل:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_ban":
                conn.execute("UPDATE users SET step = 'adm_input_ban' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("🚫 **أدخل ID المستخدم لحظره من البوت:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_unban":
                conn.execute("UPDATE users SET step = 'adm_input_unban' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("✅ **أدخل ID المستخدم لفك الحظر عنه:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_admin":
                conn.execute("UPDATE users SET step = 'adm_input_add_admin' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("👮 **أدخل ID الأدمن الجديد لمنحه الصلاحيات:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_del_admin":
                conn.execute("UPDATE users SET step = 'adm_input_del_admin' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("❌ **أدخل ID الأدمن لإزالة صلاحياته:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_txt":
                conn.execute("UPDATE users SET step = 'adm_input_bc_txt' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("📢 **اكتب نص الإذاعة العامة المباشرة لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_img":
                conn.execute("UPDATE users SET step = 'adm_input_bc_img' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("📸 **أرسل صورة الإذاعة مع النص في الوصف لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_pm_txt":
                conn.execute("UPDATE users SET step = 'adm_input_pm_txt' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text("📩 **إرسال رسالة خاصة:**\nأدخل ID والرسالة مفصولة بمسافة (مثال: `7255100997 مرحباً بك`)", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data.startswith("adm_rep_supp_"):
                target_uid = int(data.replace("adm_rep_supp_", ""))
                context.user_data["support_target_id"] = target_uid
                conn.execute("UPDATE users SET step = 'adm_input_support_reply' WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                await query.message.edit_text(f"💬 **اكتب ردك المباشر للعميل (`{target_uid}`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

        conn.close()
    except Exception as e:
        logger.error(f"Error in handle_callback: {e}")

# ----------------------------------------------------
# 11. التشغيل الرئيسي وتحديد المعالجات
# ----------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    logger.info("Bot started successfully in production mode.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
