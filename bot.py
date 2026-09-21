import os
import sys
import random
import string
import logging
import asyncio
import re
import json
import time

from threading import Thread
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string

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
DEFAULT_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

RAW_SERVER_URL = os.getenv("SERVER_URL", "https://my-bot-j658.onrender.com")
extracted_urls = re.findall(r'https?://[^\s\)\]]+', RAW_SERVER_URL)
SERVER_URL = extracted_urls[0].rstrip('/') if extracted_urls else "https://my-bot-j658.onrender.com"

# ----------------------------------------------------
# 2. حماية ضد ضغط الأزرار المتكرر والسريع (Anti-Spam Rate Limiter)
# ----------------------------------------------------
USER_LAST_CLICK = {}
RATE_LIMIT_INTERVAL = 0.6  # النوافذ الزمنية بالثواني بين الضغطات

def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last_time = USER_LAST_CLICK.get(user_id, 0)
    if now - last_time < RATE_LIMIT_INTERVAL:
        return True
    USER_LAST_CLICK[user_id] = now
    return False

# ----------------------------------------------------
# 3. نظام حفظ البيانات في الذاكرة مع JSON (بديل SQLite)
# ----------------------------------------------------
DB_FILE = "database.json"

class JSONDB:
    def __init__(self, filename=DB_FILE):
        self.filename = filename
        self.data = {
            "users": {},
            "admins": [],
            "settings": {},
            "gift_codes": {},
            "channels": {},
            "deposit_methods": {},
            "deposits": [],
            "withdrawals": [],
            "logs": [],
            "offers": [],
            "games": [],
            "code_restrictions": {}
        }
        self.load()
        self.init_defaults()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    for k in self.data:
                        if k in loaded:
                            self.data[k] = loaded[k]
            except Exception as e:
                logger.error(f"Error loading JSON DB: {e}")

    def save(self):
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving JSON DB: {e}")

    def init_defaults(self):
        if DEFAULT_ADMIN_ID and DEFAULT_ADMIN_ID not in self.data["admins"]:
            self.data["admins"].append(DEFAULT_ADMIN_ID)

        defaults = [
            ('maintenance_mode', '1'),
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
            if key not in self.data["settings"]:
                self.data["settings"][key] = val

        if "1" not in self.data["deposit_methods"]:
            self.data["deposit_methods"]["1"] = {"id": 1, "method_name": "شام كاش", "account_details": "a612b862fc9960e61333d48b86d0dcd3"}
        else:
            self.data["deposit_methods"]["1"]["account_details"] = "a612b862fc9960e61333d48b86d0dcd3"

        if "2" not in self.data["deposit_methods"]:
            self.data["deposit_methods"]["2"] = {"id": 2, "method_name": "سيريتل كاش", "account_details": "00973427"}
        else:
            self.data["deposit_methods"]["2"]["account_details"] = "00973427"

        req_channels = [
            ('@lerafree', 'قناة المبرمج', 'https://t.me/lerafree'),
            ('@golden_game_b', 'قناة البوت', 'https://t.me/golden_game_b'),
            ('@goldennlera', 'كروب المسابقات', 'https://t.me/goldennlera')
        ]
        valid_ids = [ch[0] for ch in req_channels]
        self.data["channels"] = {k: v for k, v in self.data["channels"].items() if k in valid_ids}
        for ch_id, ch_title, ch_link in req_channels:
            if ch_id not in self.data["channels"]:
                self.data["channels"][ch_id] = {"channel_id": ch_id, "channel_title": ch_title, "channel_link": ch_link}

        self.save()

db = JSONDB()

# المرجع الرئيسي لـ Asyncio Event Loop للتواصل بين Flask و Bot
bot_loop = None
tg_app = None

def run_coro_in_bot_loop(coro):
    if bot_loop and bot_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return None

def is_maintenance_active() -> bool:
    return db.data["settings"].get("maintenance_mode", "1") == "1"

def is_admin_user(user_id: int) -> bool:
    if DEFAULT_ADMIN_ID and user_id == DEFAULT_ADMIN_ID:
        return True
    return user_id in db.data["admins"]

def get_user(user_id: int):
    return db.data["users"].get(str(user_id))

def save_user(user_data: dict):
    db.data["users"][str(user_data["user_id"])] = user_data
    db.save()

def create_user(user_id: int, full_name: str, referred_by=None, step="start"):
    u = {
        "user_id": user_id,
        "full_name": full_name or "لاعب",
        "phone": "",
        "balance": 0.0,
        "referred_by": referred_by,
        "referrals_count": 0,
        "active_referrals_count": 0,
        "referral_mode": None,
        "free_spins": 0,
        "games_played": 0,
        "total_spent": 0.0,
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
    log_entry = {
        "id": len(db.data["logs"]) + 1,
        "user_id": user_id,
        "action": action,
        "amount": float(amount),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    db.data["logs"].append(log_entry)
    db.save()

# ----------------------------------------------------
# 4. خوارزمية عجلة الحظ المتقدمة وتحديث الرصيد
# ----------------------------------------------------
WHEEL_SEGMETNS = [
    {"index": 0, "title": "5 NPS", "amount": 5, "key": "wheel_prob_5"},
    {"index": 1, "title": "10 NPS", "amount": 10, "key": "wheel_prob_10"},
    {"index": 2, "title": "15 NPS", "amount": 15, "key": "wheel_prob_15"},
    {"index": 3, "title": "حاول مرة أخرى ❌", "amount": 0, "key": "wheel_prob_try_again"},
    {"index": 4, "title": "25 NPS", "amount": 25, "key": "wheel_prob_25"},
    {"index": 5, "title": "50 NPS", "amount": 50, "key": "wheel_prob_50"},
    {"index": 6, "title": "100 NPS", "amount": 100, "key": "wheel_prob_100"},
    {"index": 7, "title": "250 NPS", "amount": 250, "key": "wheel_prob_250"},
    {"index": 8, "title": "بونص 20%", "amount": 0, "key": "wheel_prob_dep_bonus_20"},
    {"index": 9, "title": "500 NPS", "amount": 500, "key": "wheel_prob_500"},
    {"index": 10, "title": "1000 NPS", "amount": 1000, "key": "wheel_prob_1000"}
]

def calculate_wheel_spin(user_id: int):
    u = get_user(user_id)
    if not u:
        return None, "مستخدم غير موجود"

    if u.get("is_banned"):
        return None, "حسابك محظور"

    if u.get("free_spins", 0) <= 0:
        return None, "ليس لديك لفات مجانية متبقية!"

    weights = []
    custom_boost = float(u.get("custom_boost", 0.0))

    for seg in WHEEL_SEGMETNS:
        base_prob = float(db.data["settings"].get(seg["key"], "0"))
        if seg["amount"] > 0:
            base_prob += custom_boost
        weights.append(max(0.1, base_prob))

    selected_seg = random.choices(WHEEL_SEGMETNS, weights=weights, k=1)[0]

    u["free_spins"] -= 1
    prize_amt = selected_seg["amount"]
    u["balance"] = u.get("balance", 0.0) + prize_amt
    u["games_played"] = u.get("games_played", 0) + 1
    save_user(u)

    add_log(user_id, f"دورة عجلة حظ بالموقع ({selected_seg['title']})", prize_amt)

    if tg_app:
        run_coro_in_bot_loop(process_wheel_win(tg_app.bot, user_id, prize_amt, selected_seg['title']))

    return {
        "segment_index": selected_seg["index"],
        "title": selected_seg["title"],
        "amount": prize_amt,
        "new_balance": u["balance"],
        "remaining_spins": u["free_spins"]
    }, None

# ----------------------------------------------------
# 5. إشعارات القناة والتحقق مع أزرار التحكم الفورية
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
        ch_id = db.data["settings"].get("tx_channel_id", "").strip()
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

        for dep in db.data["deposits"]:
            if dep["id"] == dep_id:
                dep["channel_msg_id"] = msg.message_id
                db.save()
                break
        return msg.message_id
    except Exception as e:
        logger.error(f"Error sending deposit notice to channel: {e}")
    return 0

async def notify_channel_deposit_status(bot, dep: dict, status: str):
    try:
        ch_id = db.data["settings"].get("tx_channel_id", "").strip()
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
        ch_id = db.data["settings"].get("tx_channel_id", "").strip()
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

        for w in db.data["withdrawals"]:
            if w["id"] == w_id:
                w["channel_msg_id"] = msg.message_id
                db.save()
                break
        return msg.message_id
    except Exception as e:
        logger.error(f"Error sending withdraw notice to channel: {e}")
    return 0

async def notify_channel_withdrawal_status(bot, w: dict, status: str):
    try:
        ch_id = db.data["settings"].get("tx_channel_id", "").strip()
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
# 6. التفاعل والإشعارات والاشتراك الإجباري
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
        channels = list(db.data["channels"].values())
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
        for adm_id in db.data["admins"]:
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
        ref_reward_enabled = db.data["settings"].get("referral_reward_enabled", "0") == "1"
        ref_reward_amt = float(db.data["settings"].get("referral_reward", "0")) if ref_reward_enabled else 0.0
        ref_spin_enabled = db.data["settings"].get("referral_spin_enabled", "1") == "1"

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
            welcome_enabled = db.data["settings"].get("welcome_bonus_enabled", "1") == "1"
            welcome_bonus = float(db.data["settings"].get("welcome_bonus", "50")) if welcome_enabled else 0.0

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

async def process_wheel_win(bot, user_id: int, prize_amount: float, prize_title: str):
    try:
        u = get_user(user_id)
        if not u:
            return

        bal_after = u.get("balance", 0.0)
        bal_before = bal_after - prize_amount

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
# 7. القوائم ولوحات التحكم
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
        maint_status = "🔴 مفعل (الصيانة مغلق)" if is_maintenance_active() else "🟢 معطل (البوت يعمل)"
        
        welcome_enabled = db.data["settings"].get("welcome_bonus_enabled", "1") == "1"
        welcome_status = "🟢 مفعل" if welcome_enabled else "🔴 معطل"
        welcome_amt = db.data["settings"].get("welcome_bonus", "50")
        
        ref_enabled = db.data["settings"].get("referral_reward_enabled", "0") == "1"
        ref_status = "🟢 مفعل" if ref_enabled else "🔴 معطل"
        ref_amt = db.data["settings"].get("referral_reward", "50")

        ref_spin_enabled = db.data["settings"].get("referral_spin_enabled", "1") == "1"
        ref_spin_status = "🟢 مفعل" if ref_spin_enabled else "🔴 معطل"

        tx_ch_val = db.data["settings"].get("tx_channel_id", "") or "غير محددة"
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
# 8. المعالجات والأوامر الرئيسية
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
# 9. معالجة الصور والإثباتات
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

            dep_id = len(db.data["deposits"]) + 1
            dep_rec = {
                "id": dep_id,
                "user_id": user.id,
                "method": method,
                "amount": float(amt),
                "tx_id": f"إيصال مصور: {caption}",
                "photo_file_id": photo_file_id,
                "status": "pending",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel_msg_id": 0
            }
            db.data["deposits"].append(dep_rec)
            db.save()

            u["step"] = "main"
            save_user(u)

            await update.message.reply_text("✅ **تم تقديم طلب الشحن مع صورة الإيصال بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, f"إيصال مصور: {caption}", photo_file_id)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            dep_text = f"📥 **طلب شحن جديد بإيصال مصور (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n💰 المبلغ: `{amt}` NPS\n📝 الوصف: {caption}"
            await notify_admins(context, dep_text, reply_markup=kb)
            return

        if is_admin_user(user.id) and step == "adm_input_bc_img":
            users_list = [usr for usr in db.data["users"].values() if not usr.get("is_banned")]
            u["step"] = "main"
            save_user(u)

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

    except Exception as e:
        logger.error(f"Error handling photo messages: {e}")

# ----------------------------------------------------
# 10. معالجة التواصل والتوثيق
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

        for u_item in db.data["users"].values():
            if u_item.get("phone") == contact.phone_number and u_item.get("phone_verified") == 1 and u_item.get("user_id") != user.id:
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
            await update.message.reply_text("⚠️ **الخطوة الأخيرة: يرجى الاشتراك القنوات لاستلام البونص ودخول البوت:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
        else:
            await process_welcome_bonus(user.id, context)
            await send_main_dashboard(update.effective_chat.id, user.id, user.full_name, is_admin_user(user.id), context)
    except Exception as e:
        logger.error(f"Error handling contact: {e}")

# ----------------------------------------------------
# 11. معالجة الرسائل النصية
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

            min_dep = float(db.data["settings"].get("min_deposit", "50"))
            if amt < min_dep:
                await update.message.reply_text(f"❌ الحد الأدنى للشحن هو `{min_dep}` NPS.", reply_markup=cancel_keyboard())
                return

            dep_bonus_pct = float(db.data["settings"].get("deposit_bonus_percent", "0"))
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

            dep_id = len(db.data["deposits"]) + 1
            dep_rec = {
                "id": dep_id,
                "user_id": user.id,
                "method": method,
                "amount": float(amt),
                "tx_id": text,
                "photo_file_id": None,
                "status": "pending",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel_msg_id": 0
            }
            db.data["deposits"].append(dep_rec)
            db.save()

            u["step"] = "main"
            save_user(u)

            await update.message.reply_text("✅ تم تقديم طلب الشحن بنجاح وهو قيد المراجعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_deposit_to_channel(context.bot, dep_id, user, method, amt, text, None)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة وتعبئة ⚡", callback_data=f"app_dep_{dep_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_dep_{dep_id}")]])
            await notify_admins(context, f"📥 **طلب شحن جديد (# {dep_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الإشعار: `{text}`\n💰 المبلغ: `{amt}` NPS", reply_markup=kb)
            return

        if step == "withdraw_step_code":
            context.user_data["withdraw_code"] = text
            min_w = float(db.data["settings"].get("min_withdraw", "1500"))
            comm_pct = float(db.data["settings"].get("withdraw_commission_percent", "0"))

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

            min_w = float(db.data["settings"].get("min_withdraw", "1500"))
            if amt < min_w or amt > u.get("balance", 0.0):
                await update.message.reply_text(f"❌ المبلغ غير متاح في رصيدك الحالي (`{u.get('balance', 0.0):,.2f}` NPS) أو أقل من حد السحب الأدنى (`{min_w}` NPS).", reply_markup=cancel_keyboard("btn_withdraw"))
                return

            comm_pct = float(db.data["settings"].get("withdraw_commission_percent", "0"))
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
            last_used = db.data["code_restrictions"].get(str(user.id), 0)
            if last_used and (now_ts - last_used) < 21600:
                rem_sec = 21600 - (now_ts - last_used)
                hrs = rem_sec // 3600
                mins = (rem_sec % 3600) // 60
                u["step"] = "main"
                save_user(u)
                await update.message.reply_text(f"⚠️ **تقييد الأكواد:**\nيمكنك استخدام كود هدية واحد كل 6 ساعات فقط.\n⏱️ **المتبقي:** `{hrs}` ساعة و `{mins}` دقيقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            g = db.data["gift_codes"].get(text)
            if not g or g.get("uses_left", 0) <= 0:
                u["step"] = "main"
                save_user(u)
                await update.message.reply_text("❌ الكود غير صحيح أو منتهي الاستخدام.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            bal_before = u.get("balance", 0.0)
            amt = float(g["amount"])
            bal_after = bal_before + amt

            u["balance"] = bal_after
            u["step"] = "main"
            save_user(u)

            g["uses_left"] -= 1
            if g["uses_left"] <= 0:
                del db.data["gift_codes"][text]
            db.data["code_restrictions"][str(user.id)] = now_ts
            db.save()

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
        # خطوات لوحة الإدارة الشاملة
        # ----------------------------------------------------
        if is_admin_user(user.id):
            if step == "adm_input_welcome_amt":
                try:
                    amt = float(text)
                    db.data["settings"]["welcome_bonus"] = str(amt)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ **تم تعديل قيمة البونص الترحيبي بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_ref_amt":
                try:
                    amt = float(text)
                    db.data["settings"]["referral_reward"] = str(amt)
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ **تم تعديل قيمة مكافأة الإحالة بنجاح إلى:** `{amt}` NPS", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح بالأرقام.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_tx_channel":
                ch_val = text.strip()
                db.data["settings"]["tx_channel_id"] = ch_val
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
                if m_id in db.data["deposit_methods"]:
                    db.data["deposit_methods"][m_id]["account_details"] = text
                    db.save()
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
                if c_code not in db.data["gift_codes"]:
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text("❌ الكود غير موجود أو ملغي سابقاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    return

                del db.data["gift_codes"][c_code]
                db.save()

                u["step"] = "main"
                save_user(u)
                await update.message.reply_text(f"✅ تم إلغاء الكود `{c_code}` وحذفه بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if step == "adm_input_add_dep_meth":
                try:
                    parts = text.split("|")
                    m_name, m_det = parts[0].strip(), parts[1].strip()
                    new_id = str(len(db.data["deposit_methods"]) + 1)
                    db.data["deposit_methods"][new_id] = {"id": int(new_id), "method_name": m_name, "account_details": m_det}
                    db.save()

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
                    off_id = len(db.data["offers"]) + 1
                    db.data["offers"].append({"id": off_id, "title": off_t, "description": off_d, "link": off_l})
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة العرض: **{off_t}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `عنوان العرض | وصف العرض | https://t.me/example`", reply_markup=cancel_keyboard("adm_offers_menu"))
                return

            if step == "adm_input_add_channel":
                try:
                    parts = text.split("|")
                    ch_id, ch_title, ch_link = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    db.data["channels"][ch_id] = {"channel_id": ch_id, "channel_title": ch_title, "channel_link": ch_link}
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة قناة الاشتراك الصارم: **{ch_title}** بنجاح!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ صيغة خاطئة. الفصل بـ `|`. مثال: `@MyChan | قناة الأخبار | https://t.me/MyChan`", reply_markup=cancel_keyboard("adm_channels_menu"))
                return

            if step == "adm_input_unrestrict_user":
                try:
                    tid = str(int(text))
                    if tid in db.data["code_restrictions"]:
                        del db.data["code_restrictions"][tid]
                        db.save()
                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إلغاء تقييد استخدام الكود عن اللاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_clear_boost":
                try:
                    tid = int(text)
                    t_user = get_user(tid)
                    if t_user:
                        t_user["custom_boost"] = 0.0
                        save_user(t_user)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إلغاء الحظ الخاص عن اللاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_user_boost":
                try:
                    parts = text.split()
                    tid, boost_val = int(parts[0]), float(parts[1])
                    t_user = get_user(tid)
                    if t_user:
                        t_user["custom_boost"] = boost_val
                        save_user(t_user)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تخصيص نسبة حظ `{boost_val}%` للاعب `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ مثال: `7255100997 50`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_wheel_prob_val":
                target_key = context.user_data.get("edit_wheel_key")
                if target_key:
                    try:
                        prob_val = float(text)
                        db.data["settings"][target_key] = str(prob_val)
                        db.save()

                        u["step"] = "main"
                        save_user(u)
                        await update.message.reply_text(f"✅ تم تعديل احتمال `{target_key}` إلى `{prob_val}%` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 خوارزمية العجلة 🎡", callback_data="adm_wheel_algo")]]))
                        return
                    except Exception:
                        pass
                await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("adm_wheel_algo"))
                return

            if step == "adm_input_grant_spin_user":
                try:
                    parts = text.split()
                    tid, num_spins = int(parts[0]), int(parts[1])
                    t_user = get_user(tid)
                    if t_user:
                        t_user["free_spins"] = t_user.get("free_spins", 0) + num_spins
                        save_user(t_user)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم منح `{num_spins}` لفة مجانية للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try: await context.bot.send_message(tid, f"🎁 تم منحك `{num_spins}` لفة مجانية في عجلة الحظ من الإدارة!")
                    except: pass
                except Exception:
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
                        db.data["gift_codes"][full_code] = {"code": full_code, "amount": amt, "uses_left": uses_per_code}
                        generated.append(full_code)

                    db.save()
                    u["step"] = "main"
                    save_user(u)

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
                    await update.message.reply_text(f"❌ خطأ بالبيانات. مثال: `GOLDEN 50 1 10`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_make_gift":
                try:
                    parts = text.split()
                    code_str, amt, uses = parts[0], float(parts[1]), int(parts[2])
                    db.data["gift_codes"][code_str] = {"code": code_str, "amount": amt, "uses_left": uses}
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🎁 **تم إنشاء الكود الفردي بنجاح!**\n\n🎫 **الكود:** `{code_str}`\n💰 **المبلغ:** `{amt}` NPS\n👥 **الاستخدامات:** `{uses}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ مثال: `VIP100 500 10`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_dep_bonus":
                try:
                    val = float(text)
                    db.data["settings"]["deposit_bonus_percent"] = str(val)
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم ضبط بونص الشحن إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_w_commission":
                try:
                    val = float(text)
                    db.data["settings"]["withdraw_commission_percent"] = str(val)
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم ضبط عمولة السحب إلى `{val}%`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_min_dep":
                try:
                    val = float(text)
                    db.data["settings"]["min_deposit"] = str(val)
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل حد الشحن الأدنى إلى `{val}` NPS.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_min_w":
                try:
                    val = float(text)
                    db.data["settings"]["min_withdraw"] = str(val)
                    db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم تعديل حد السحب الأدنى إلى `{val}` NPS.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل رقم صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_add_bal":
                try:
                    parts = text.split()
                    tid, amt = int(parts[0]), float(parts[1])
                    t_user = get_user(tid)
                    if t_user:
                        t_user["balance"] = t_user.get("balance", 0.0) + amt
                        save_user(t_user)
                        add_log(tid, "إضافة رصيد من الإدارة", amt)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إضافة `{amt}` NPS للاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try: await context.bot.send_message(tid, f"🎁 تم إضافة `{amt}` NPS لرصيدك من الإدارة!")
                    except: pass
                except Exception:
                    await update.message.reply_text("❌ مثال: `7255100997 500`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_sub_bal":
                try:
                    parts = text.split()
                    tid, amt = int(parts[0]), float(parts[1])
                    t_user = get_user(tid)
                    if t_user:
                        t_user["balance"] = max(0.0, t_user.get("balance", 0.0) - amt)
                        save_user(t_user)
                        add_log(tid, "خصم رصيد من الإدارة", -amt)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم خصم `{amt}` NPS من اللاعب `{tid}`.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ مثال: `7255100997 100`", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_ban":
                try:
                    tid = int(text)
                    t_user = get_user(tid)
                    if t_user:
                        t_user["is_banned"] = 1
                        save_user(t_user)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"🚫 تم حظر المستخدم `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_unban":
                try:
                    tid = int(text)
                    t_user = get_user(tid)
                    if t_user:
                        t_user["is_banned"] = 0
                        save_user(t_user)

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم فك حظر المستخدم `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_add_admin":
                try:
                    tid = int(text)
                    if tid not in db.data["admins"]:
                        db.data["admins"].append(tid)
                        db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"👮 تم إضافة الأدمن الجديد `{tid}` بنجاح ومنحه كامل الصلاحيات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                    try: await context.bot.send_message(tid, "👑 **تهانينا! تم منحك صلاحيات أدمن كاملة في البوت.**")
                    except: pass
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_del_admin":
                try:
                    tid = int(text)
                    if DEFAULT_ADMIN_ID and tid == DEFAULT_ADMIN_ID:
                        await update.message.reply_text("❌ لا يمكن إزالة الأدمن الرئيسي المنشئ.", reply_markup=cancel_keyboard("open_admin_panel"))
                        return
                    if tid in db.data["admins"]:
                        db.data["admins"].remove(tid)
                        db.save()

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إزالة الأدمن `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception:
                    await update.message.reply_text("❌ أدخل ID صحيح.", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if step == "adm_input_pm_txt":
                try:
                    parts = text.split(" ", 1)
                    tid, msg_content = int(parts[0]), parts[1]
                    await context.bot.send_message(chat_id=tid, text=f"📩 **رسالة خاصة من إدارة منصة Golden Games:**\n\n{msg_content}", parse_mode="Markdown")

                    u["step"] = "main"
                    save_user(u)
                    await update.message.reply_text(f"✅ تم إرسال الرسالة الخاصة إلى `{tid}` بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception as e:
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

                u["step"] = "main"
                save_user(u)
                return

            if step == "adm_input_user_info":
                try:
                    tid = int(text)
                    u_info = get_user(tid)
                    if not u_info:
                        await update.message.reply_text("❌ اللاعب غير موجود.", reply_markup=cancel_keyboard("open_admin_panel"))
                    else:
                        dep_stats = [d for d in db.data["deposits"] if d["user_id"] == tid and d["status"] == "approved"]
                        w_stats = [w for w in db.data["withdrawals"] if w["user_id"] == tid and w["status"] == "approved"]
                        codes_count = len([l for l in db.data["logs"] if l["user_id"] == tid and "تفعيل كود هدية" in l["action"]])

                        dep_sum = sum(d["amount"] for d in dep_stats)
                        w_sum = sum(w["amount"] for w in w_stats)

                        msg = (
                            f"🔍 **تفاصيل العميل الشاملة:**\n"
                            f"✨ ─────────────────── ✨\n"
                            f"🆔 **ID:** `{u_info['user_id']}`\n"
                            f"👤 **الاسم:** {u_info['full_name']}\n"
                            f"📱 **الهاتف:** `{u_info.get('phone') or 'غير مرتبط'}`\n"
                            f"💰 **الرصيد الحالي:** `{u_info.get('balance', 0.0):,.2f}` NPS\n"
                            f"🎡 **اللفات المجانية:** `{u_info.get('free_spins', 0)}`\n"
                            f"👥 **عدد الإحالات:** `{u_info.get('referrals_count', 0)}`\n"
                            f"🎯 **نسبة الحظ الخاص:** `{u_info.get('custom_boost', 0.0)}%`\n"
                            f"✨ ─────────────────── ✨\n"
                            f"💳 **الشحن الناجح:** {len(dep_stats)} مرة | الإجمالي: `{dep_sum:,.2f}` NPS\n"
                            f"💸 **السحب الناجح:** {len(w_stats)} مرة | الإجمالي: `{w_sum:,.2f}` NPS\n"
                            f"🎁 **البونص المحصل:** {'نعم' if u_info.get('welcome_bonus_claimed') else 'لا'}\n"
                            f"🎟️ **الأكواد المستعملة:** {codes_count} كود\n"
                            f"🎰 **إجمالي الرصيد المصروف باللعب:** `{u_info.get('total_spent', 0.0):,.2f}` NPS\n"
                            f"🚫 **الحالة:** {'محظور' if u_info.get('is_banned') else 'نشط'}"
                        )
                        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                except Exception as e:
                    await update.message.reply_text(f"❌ أدخل ID صحيح. الخطأ: {e}", reply_markup=cancel_keyboard("open_admin_panel"))

                u["step"] = "main"
                save_user(u)
                return

            if step == "adm_input_bc_txt":
                users_list = [usr for usr in db.data["users"].values() if not usr.get("is_banned")]
                u["step"] = "main"
                save_user(u)

                async def send_msg_fast(uid):
                    try: await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                    except: pass

                tasks = [send_msg_fast(u_item["user_id"]) for u_item in users_list]
                await asyncio.gather(*tasks)

                await update.message.reply_text(f"📢 تم إرسال الإذاعة السريعة لـ `{len(users_list)}` مستخدم بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

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
# 12. معالجة نقرات الأزرار التفاعلية (Callback Queries)
# ----------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    # نظام حماية الزر السريع (Rate Limiter)
    if is_rate_limited(user.id):
        try:
            await query.answer("⚡ يرجى الضغط بهدوء وبدون تكرار سريع لحماية الحساب!", show_alert=False)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass

    try:
        data = query.data
        u = get_user(user.id)
        is_admin = is_admin_user(user.id)

        if u:
            u["step"] = "main"
            save_user(u)

        if not u or u.get("is_banned"):
            return

        if data == "none":
            return

        if data == "check_subscription_status":
            is_subscribed, unsubscribed = await check_user_channels_subscription(context.bot, user.id)
            if is_subscribed:
                await query.message.edit_text("✅ **تم التأكد من اشتراكك بنجاح!**")
                await process_welcome_bonus(user.id, context)
                await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
            else:
                await query.message.edit_text("❌ **لم تشترك في القنوات المطلوبة بعد. يرجى الاشتراك أولاً:**", reply_markup=build_sub_keyboard(unsubscribed), parse_mode="Markdown")
            return

        if data == "back_to_main":
            await send_main_dashboard(query.message.chat_id, user.id, user.full_name, is_admin, context)
            return

        # ------------------- أزرار العميل -------------------
        if data == "btn_offers":
            offers = db.data["offers"]
            if not offers:
                await query.message.edit_text("🔥 **لا توجد عروض ترويجية متاحة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                msg = "🔥 **العروض الترويجية والخصومات الحالية:**\n\n"
                kb = []
                for off in offers:
                    msg += f"📌 **{off['title']}**\n📝 {off['description']}\n───────────────\n"
                    if off.get("link"):
                        kb.append([InlineKeyboardButton(f"🔗 {off['title']}", url=off["link"])])
                kb.append([InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")])
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data == "btn_deposit":
            dep_methods = list(db.data["deposit_methods"].values())
            min_dep = db.data["settings"].get("min_deposit", "50")
            dep_bonus_pct = db.data["settings"].get("deposit_bonus_percent", "0")

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
            dm_id = data.replace("user_select_dep_", "")
            dm = db.data["deposit_methods"].get(dm_id)
            if not dm:
                await query.message.edit_text("❌ وسيلة الشحن غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["dep_method"] = dm["method_name"]
            context.user_data["dep_acc_details"] = dm["account_details"]

            u["step"] = "deposit_step_amount"
            save_user(u)

            await query.message.edit_text(
                f"💳 **وسيلة الشحن المحددة:** {dm['method_name']}\n\n"
                f"✍️ **يرجى كتابة مبلغ الشحن المطلوب برقم صحيح (بالـ NPS):**",
                reply_markup=cancel_keyboard("btn_deposit")
            )
            return

        if data == "btn_withdraw":
            dep_methods = list(db.data["deposit_methods"].values())
            min_w = db.data["settings"].get("min_withdraw", "1500")
            comm_pct = db.data["settings"].get("withdraw_commission_percent", "0")

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
                f"💰 **رصيدك الحالي:** `{u.get('balance', 0.0):,.2f}` NPS\n"
                f"⚠️ **الحد الأدنى للسحب:** `{min_w}` NPS\n"
                f"➖ **نسبة عمولة السحب:** `{comm_pct}%`\n"
                f"✨ ─────────────────── ✨\n\n"
                f"👇 اختر وسيلة استلام الأرباح المفضلّة:"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("user_select_w_"):
            dm_id = data.replace("user_select_w_", "")
            dm = db.data["deposit_methods"].get(dm_id)

            if not dm:
                await query.message.edit_text("❌ وسيلة غير متوفرة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            context.user_data["withdraw_method"] = dm["method_name"]

            u["step"] = "withdraw_step_code"
            save_user(u)

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

            if amt <= 0 or u.get("balance", 0.0) < amt:
                await query.message.edit_text("❌ رصيدك غير كافٍ لإتمام السحب.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
                return

            u["balance"] -= amt
            save_user(u)

            w_id = len(db.data["withdrawals"]) + 1
            w_rec = {
                "id": w_id,
                "user_id": user.id,
                "method": method,
                "account_code": acc_code,
                "amount": float(amt),
                "net_amount": float(net_amt),
                "status": "pending",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel_msg_id": 0
            }
            db.data["withdrawals"].append(w_rec)
            db.save()

            await query.message.edit_text("✅ **تم تقديم طلب السحب بنجاح وهو قيد المراجعة.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))

            await send_withdraw_to_channel(context.bot, w_id, user, method, acc_code, amt, net_amt)

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة ودفع 💸", callback_data=f"app_w_{w_id}"), InlineKeyboardButton("❌ رفض 🗑️", callback_data=f"rej_w_{w_id}")]])
            await notify_admins(context, f"💸 **طلب سحب جديد (# {w_id}):**\n👤 {user.full_name} (`{user.id}`)\n💳 الطريقة: {method}\n🔢 الحساب: `{acc_code}`\n💰 المبلغ المطلوب: `{amt}` NPS\n💵 الصافي: `{net_amt}` NPS", reply_markup=kb)
            return

        if data == "btn_account":
            msg = (
                f"📌 **ملف الحساب الشخصي:**\n"
                f"✨ ─────────────────── ✨\n"
                f"👤 **الاسم:** {user.full_name}\n"
                f"🆔 **المعرف:** `{user.id}`\n"
                f"📱 **رقم الهاتف:** `{u.get('phone') or 'غير مرتبط'}`\n"
                f"💰 **رصيدك الحالي:** `{u.get('balance', 0.0):,.2f}` NPS\n"
                f"🎡 **اللفات المجانية:** `{u.get('free_spins', 0)}` لفة\n"
                f"👥 **عدد إحالاتك الناجحة:** `{u.get('referrals_count', 0)}`\n"
                f"✨ ─────────────────── ✨"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_referral":
            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start={user.id}"
            msg = (
                f"🎯 **نظام الإحالة ودعوة الأصدقاء:** 🚀\n"
                f"✨ ─────────────────── ✨\n"
                f"شارك رابط الإحالة الخاص بك واحصل على مكافآت فورية ولفات مجانية لكل صديق ينضم عبر رابطك!\n\n"
                f"🔗 **رابط الإحالة الخاص بك:**\n`{ref_link}`\n\n"
                f"📊 **عدد إحالاتك الناجحة:** `{u.get('referrals_count', 0)}` صديق\n"
                f"✨ ─────────────────── ✨"
            )
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_send_proof":
            u["step"] = "user_upload_proof"
            save_user(u)
            await query.message.edit_text("📸 **يرجى إرسال صورة إثبات الإصابة أو الفوز الآن (أو كتابة التفاصيل بنص فوري):**", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_gift":
            u["step"] = "input_gift_code"
            save_user(u)
            await query.message.edit_text("🧨 **أدخل كود الهدية الخاص بك الآن لتفعيله:**", reply_markup=cancel_keyboard("back_to_main"))
            return

        if data == "btn_buy_bot":
            await query.message.edit_text("🍷 **طلب بوت خاص وشخصي:**\n\nلطلب بوت خاص ألعاب أو برمجيات ممتازة تواصل مع المبرمج المباشر عبر قناة الدعم الرسمية: @lerafree", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_logs":
            user_logs = [l for l in db.data["logs"] if l["user_id"] == user.id][-10:]
            user_logs.reverse()
            if not user_logs:
                await query.message.edit_text("🛑 **لا توجد عمليات مسجلة في حسابك حتى الآن.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            else:
                msg = "🛑 **سجل أحدث العمليات في حسابك:**\n\n"
                for l in user_logs:
                    msg += f"🔹 {l['action']} | `{l['amount']:,.2f}` NPS | {l['timestamp']}\n"
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية 🏠", callback_data="back_to_main")]]))
            return

        if data == "btn_support":
            u["step"] = "input_support_msg"
            save_user(u)
            await query.message.edit_text("🌶️ **اكتب رسالتك للدعم الفني المباشر وسنقوم بالرد عليك في أسرع وقت:**", reply_markup=cancel_keyboard("back_to_main"))
            return

        # ------------------- أزرار لوحة الإدارة -------------------
        if is_admin:
            if data == "open_admin_panel":
                await query.message.edit_text("🛑 **لوحة التحكم الإدارية العليا:**", reply_markup=admin_panel_keyboard())
                return

            if data == "adm_toggle_maint":
                curr = db.data["settings"].get("maintenance_mode", "1")
                db.data["settings"]["maintenance_mode"] = "0" if curr == "1" else "1"
                db.save()
                await query.message.edit_reply_markup(reply_markup=admin_panel_keyboard())
                return

            if data == "adm_toggle_welcome":
                curr = db.data["settings"].get("welcome_bonus_enabled", "1")
                db.data["settings"]["welcome_bonus_enabled"] = "0" if curr == "1" else "1"
                db.save()
                await query.message.edit_reply_markup(reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_welcome_amt":
                u["step"] = "adm_input_welcome_amt"
                save_user(u)
                await query.message.edit_text("✏️ **أدخل القيمة الجديدة للبونص الترحيبي (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_toggle_ref_bonus":
                curr = db.data["settings"].get("referral_reward_enabled", "0")
                db.data["settings"]["referral_reward_enabled"] = "0" if curr == "1" else "1"
                db.save()
                await query.message.edit_reply_markup(reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_ref_amt":
                u["step"] = "adm_input_ref_amt"
                save_user(u)
                await query.message.edit_text("✏️ **أدخل قيمة مكافأة الإحالة الجديدة (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_toggle_ref_spin":
                curr = db.data["settings"].get("referral_spin_enabled", "1")
                db.data["settings"]["referral_spin_enabled"] = "0" if curr == "1" else "1"
                db.save()
                await query.message.edit_reply_markup(reply_markup=admin_panel_keyboard())
                return

            if data == "adm_set_tx_channel":
                u["step"] = "adm_input_tx_channel"
                save_user(u)
                await query.message.edit_text("📢 **أدخل يوزر القناة الخاصة بالإشعارات (مثال: `@my_channel`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_wheel_algo":
                kb = []
                for seg in WHEEL_SEGMETNS:
                    val = db.data["settings"].get(seg["key"], "0")
                    kb.append([InlineKeyboardButton(f"⚙️ {seg['title']}: {val}%", callback_data=f"adm_edit_prob_{seg['key']}")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])

                msg = "🎡 **إعدادات خوارزمية عجلة الحظ والاحتمالات:**\n\nاضغط على أي خيار لتعديل نسبته مئوية:"
                await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(kb))
                return

            if data.startswith("adm_edit_prob_"):
                key = data.replace("adm_edit_prob_", "")
                context.user_data["edit_wheel_key"] = key
                u["step"] = "adm_input_wheel_prob_val"
                save_user(u)
                await query.message.edit_text(f"✏️ **أدخل الاحتمال المئوي الجديد للـ (`{key}`):**", reply_markup=cancel_keyboard("adm_wheel_algo"))
                return

            if data == "adm_user_boost":
                kb = [
                    [InlineKeyboardButton("🎯 تخصيص حظر/حظ لاعب", callback_data="adm_set_user_boost_input")],
                    [InlineKeyboardButton("❌ إلغاء الحظ الخاص لاعب", callback_data="adm_clear_user_boost_input")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text("🎯 **إدارة نسبة الحظ الخاص للاعبين:**", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_set_user_boost_input":
                u["step"] = "adm_input_user_boost"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب متبوعاً بنسبة الحظ (مثال: `7255100997 50`):**", reply_markup=cancel_keyboard("adm_user_boost"))
                return

            if data == "adm_clear_user_boost_input":
                u["step"] = "adm_input_clear_boost"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب لإلغاء الحظ الخاص عنه:**", reply_markup=cancel_keyboard("adm_user_boost"))
                return

            if data == "adm_active_referrals":
                active_refs = [usr for usr in db.data["users"].values() if usr.get("referrals_count", 0) > 0]
                msg = f"👥 **قائمة اللاعبين أصحاب الإحالات النشطة ({len(active_refs)} لاعب):**\n\n"
                for r in active_refs[:15]:
                    msg += f"👤 {r['full_name']} (`{r['user_id']}`) | إحالاته: `{r.get('referrals_count',0)}`\n"

                kb = [
                    [InlineKeyboardButton("💰 دفع مستحقات لاعب", callback_data="adm_payout_ref")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_payout_ref":
                u["step"] = "adm_input_ref_payout"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب والمبلغ المراد إضافته لمستحقاته (مثال: `7255100997 500`):**", reply_markup=cancel_keyboard("adm_active_referrals"))
                return

            if data == "adm_grant_spins_menu":
                kb = [
                    [InlineKeyboardButton("🎰 منح لاعب معين لفات", callback_data="adm_grant_spin_user_prompt")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text("🎰 **منح لفات مجانية في عجلة الحظ:**", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_grant_spin_user_prompt":
                u["step"] = "adm_input_grant_spin_user"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب وعدد اللفات (مثال: `7255100997 5`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_channels_menu":
                ch_list = list(db.data["channels"].values())
                msg = "📢 **قنوات الاشتراك الإجباري والصارم:**\n\n"
                kb = []
                for ch in ch_list:
                    msg += f"📌 **{ch.get('channel_title')}** (`{ch.get('channel_id')}`)\n"
                    kb.append([InlineKeyboardButton(f"❌ حذف {ch.get('channel_title')}", callback_data=f"adm_del_chan_{ch.get('channel_id')}")])
                kb.append([InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="adm_add_chan_prompt")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data.startswith("adm_del_chan_"):
                ch_id = data.replace("adm_del_chan_", "")
                if ch_id in db.data["channels"]:
                    del db.data["channels"][ch_id]
                    db.save()
                await query.message.edit_text("✅ تم حذف القناة بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة القنوات 📢", callback_data="adm_channels_menu")]]))
                return

            if data == "adm_add_chan_prompt":
                u["step"] = "adm_input_add_channel"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل تفاصيل القناة مفصولة بـ `|` (مثال: `@MyChan | اسم القناة | https://t.me/MyChan`):**", reply_markup=cancel_keyboard("adm_channels_menu"))
                return

            if data == "adm_dep_methods":
                m_list = list(db.data["deposit_methods"].values())
                msg = "💳 **وسائل الدفع والشحن المتاحة:**\n\n"
                kb = []
                for m in m_list:
                    msg += f"🔹 **{m['method_name']}:** `{m['account_details']}`\n"
                    kb.append([InlineKeyboardButton(f"✏️ تعديل حساب {m['method_name']}", callback_data=f"adm_edit_dep_m_{m['id']}")])
                kb.append([InlineKeyboardButton("➕ إضافة وسيلة شحن جديدة", callback_data="adm_add_dep_m_prompt")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data.startswith("adm_edit_dep_m_"):
                m_id = data.replace("adm_edit_dep_m_", "")
                u["step"] = f"adm_edit_dep_acc_{m_id}"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل تفاصيل الحساب الجديدة لهذه الوسيلة:**", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if data == "adm_add_dep_m_prompt":
                u["step"] = "adm_input_add_dep_meth"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل اسم وسيلة الشحن وحسابها مفصولة بـ `|` (مثال: `شام كاش | 0912345678`):**", reply_markup=cancel_keyboard("adm_dep_methods"))
                return

            if data == "adm_deposits":
                pending = [d for d in db.data["deposits"] if d.get("status") == "pending"]
                if not pending:
                    await query.message.edit_text("📥 **لا توجد طلبات شحن معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    d = pending[0]
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ موافقة وتعبئة", callback_data=f"app_dep_{d['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_dep_{d['id']}")],
                        [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                    ])
                    msg = f"📥 **طلب شحن معلق (# {d['id']}):**\n\n👤 المستخدم: `{d['user_id']}`\n💳 الطريقة: {d['method']}\n💰 المبلغ: `{d['amount']}` NPS\n📝 الإشعار: `{d['tx_id']}`"
                    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
                return

            if data == "adm_withdraws":
                pending = [w for w in db.data["withdrawals"] if w.get("status") == "pending"]
                if not pending:
                    await query.message.edit_text("💸 **لا توجد طلبات سحب معلقة حالياً.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                else:
                    w = pending[0]
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ موافقة ودفع", callback_data=f"app_w_{w['id']}"), InlineKeyboardButton("❌ رفض", callback_data=f"rej_w_{w['id']}")],
                        [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                    ])
                    msg = f"💸 **طلب سحب معلق (# {w['id']}):**\n\n👤 المستخدم: `{w['user_id']}`\n💳 الطريقة: {w['method']}\n🔢 الحساب: `{w['account_code']}`\n💰 المبلغ: `{w['amount']}` NPS\n💵 الصافي: `{w['net_amount']}` NPS"
                    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
                return

            if data == "adm_offers_menu":
                offers = db.data["offers"]
                msg = "🎁 **إدارة العروض الحالية:**\n\n"
                kb = []
                for off in offers:
                    msg += f"📌 **{off['title']}:** {off['description']}\n"
                    kb.append([InlineKeyboardButton(f"❌ حذف {off['title']}", callback_data=f"adm_del_off_{off['id']}")])
                kb.append([InlineKeyboardButton("➕ إضافة عرض جديد", callback_data="adm_add_offer_prompt")])
                kb.append([InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")])
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data.startswith("adm_del_off_"):
                off_id = int(data.replace("adm_del_off_", ""))
                db.data["offers"] = [o for o in db.data["offers"] if o["id"] != off_id]
                db.save()
                await query.message.edit_text("✅ تم حذف العرض بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة العروض 🎁", callback_data="adm_offers_menu")]]))
                return

            if data == "adm_add_offer_prompt":
                u["step"] = "adm_input_add_offer"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل عنوان العرض ووصفه ورابطه مفصولة بـ `|` (مثال: `عنوان | وصف العرض | https://t.me/example`):**", reply_markup=cancel_keyboard("adm_offers_menu"))
                return

            if data == "adm_code_restrictions":
                u["step"] = "adm_input_unrestrict_user"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب لإلغاء تقييد استخدام الكود عنه:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_active_codes":
                codes = list(db.data["gift_codes"].values())
                msg = f"🎟️ **قائمة الأكواد النشطة ({len(codes)} كود):**\n\n"
                for c in codes[:15]:
                    msg += f"🎫 `{c['code']}` | المبلغ: `{c['amount']}` NPS | المتبقي: `{c['uses_left']}`\n"

                kb = [
                    [InlineKeyboardButton("❌ إلغاء وحذف كود", callback_data="adm_del_code_prompt")],
                    [InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]
                ]
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return

            if data == "adm_del_code_prompt":
                u["step"] = "adm_input_del_code_manual"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل رمز الكود المراد حذفه وإلغائه:**", reply_markup=cancel_keyboard("adm_active_codes"))
                return

            if data == "adm_set_dep_bonus":
                u["step"] = "adm_input_dep_bonus"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل نسبة بونص الشحن الإضافية المئوية (%):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_w_commission":
                u["step"] = "adm_input_w_commission"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل نسبة عمولة السحب المئوية (%):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_dep":
                u["step"] = "adm_input_min_dep"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل الحد الأدنى المسموح به للشحن (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_set_min_w":
                u["step"] = "adm_input_min_w"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل الحد الأدنى المسموح به للسحب (NPS):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_bal":
                u["step"] = "adm_input_add_bal"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب والمبلغ المراد إضافته (مثال: `7255100997 500`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_sub_bal":
                u["step"] = "adm_input_sub_bal"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID اللاعب والمبلغ المراد خصمه (مثال: `7255100997 100`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_batch_codes":
                u["step"] = "adm_input_batch_codes"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل بيانات توليد دفعة الأكواد (بادئة مبلغ استخدامات عدد):\nمثال: `GOLDEN 50 1 10`**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_make_gift":
                u["step"] = "adm_input_make_gift"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل بيانات الكود الفردي (كود مبلغ استخدامات):\nمثال: `VIP100 500 10`**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_user_info":
                u["step"] = "adm_input_user_info"
                save_user(u)
                await query.message.edit_text("✍️ **أدخل ID العميل لعرض تفاصيله الكاملة:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_players_log":
                logs = db.data["logs"][-15:]
                logs.reverse()
                msg = "📊 **سجل أحدث أفعال ونقاط اللاعبين:**\n\n"
                for l in logs:
                    msg += f"👤 `{l['user_id']}` | {l['action']} | `{l['amount']}` NPS\n"
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_list_admins":
                msg = "👮 **قائمة الأدمنية المنضمين:**\n\n"
                for adm in db.data["admins"]:
                    msg += f"👑 ID: `{adm}`\n"
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_stats":
                total_users = len(db.data["users"])
                total_bal = sum(usr.get("balance", 0.0) for usr in db.data["users"].values())
                app_deps = [d for d in db.data["deposits"] if d.get("status") == "approved"]
                app_ws = [w for w in db.data["withdrawals"] if w.get("status") == "approved"]

                dep_sum = sum(d["amount"] for d in app_deps)
                w_sum = sum(w["amount"] for w in app_ws)

                msg = (
                    f"📊 **الإحصائيات الشاملة والكاملة للبوت:**\n"
                    f"✨ ─────────────────── ✨\n"
                    f"👥 **إجمالي المستخدمين:** `{total_users}` لاعب\n"
                    f"💰 **إجمالي رصيد الحسابات الحالي:** `{total_bal:,.2f}` NPS\n"
                    f"💳 **الشحنات الناجحة المقبولة:** {len(app_deps)} طلب | `{dep_sum:,.2f}` NPS\n"
                    f"💸 **السحوبات الناجحة المقبولة:** {len(app_ws)} طلب | `{w_sum:,.2f}` NPS\n"
                    f"✨ ─────────────────── ✨"
                )
                await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة ⚙️", callback_data="open_admin_panel")]]))
                return

            if data == "adm_ban":
                u["step"] = "adm_input_ban"
                save_user(u)
                await query.message.edit_text("🚫 **أدخل ID المستخدم المراد حظره:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_unban":
                u["step"] = "adm_input_unban"
                save_user(u)
                await query.message.edit_text("✅ **أدخل ID المستخدم المراد فك الحظر عنه:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_txt":
                u["step"] = "adm_input_bc_txt"
                save_user(u)
                await query.message.edit_text("📢 **اكتب نص الرسالة المراد إذاعتها لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_bc_img":
                u["step"] = "adm_input_bc_img"
                save_user(u)
                await query.message.edit_text("📸 **أرسل الصورة المراد إذاعتها لجميع المستخدمين:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_pm_txt":
                u["step"] = "adm_input_pm_txt"
                save_user(u)
                await query.message.edit_text("📩 **أدخل ID المستخدم والرسالة (مثال: `7255100997 مرحباً بك`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_add_admin":
                u["step"] = "adm_input_add_admin"
                save_user(u)
                await query.message.edit_text("👮 **أدخل ID المستخدم لرفعه أدمن:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data == "adm_del_admin":
                u["step"] = "adm_input_del_admin"
                save_user(u)
                await query.message.edit_text("❌ **أدخل ID الأدمن المراد إزالته:**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

            if data.startswith("adm_rep_supp_"):
                tid = data.replace("adm_rep_supp_", "")
                context.user_data["support_target_id"] = tid
                u["step"] = "adm_input_support_reply"
                save_user(u)
                await query.message.edit_text(f"💬 **اكتب ردك المباشر للعميل (`{tid}`):**", reply_markup=cancel_keyboard("open_admin_panel"))
                return

        # ------------------- أزرار موافقة ورفض طلبات الشحن والسحب -------------------
        if data.startswith("app_dep_"):
            dep_id = int(data.replace("app_dep_", ""))
            dep = next((d for d in db.data["deposits"] if d["id"] == dep_id), None)
            if dep and dep["status"] == "pending":
                dep["status"] = "approved"
                t_user = get_user(dep["user_id"])
                if t_user:
                    amt = dep["amount"]
                    dep_bonus_pct = float(db.data["settings"].get("deposit_bonus_percent", "0"))
                    total_credit = amt + (amt * (dep_bonus_pct / 100.0))

                    t_user["balance"] = t_user.get("balance", 0.0) + total_credit
                    save_user(t_user)

                    add_log(dep["user_id"], f"شحن مقبول #{dep_id}", total_credit)

                    try:
                        await context.bot.send_message(
                            dep["user_id"],
                            f"✅ **تمت الموافقة على طلب الشحن الخاص بك (# {dep_id})!**\n💰 **المبلغ المضاف لرصيدك:** `{total_credit:,.2f}` NPS",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

                db.save()
                await notify_channel_deposit_status(context.bot, dep, "approved")
                await query.message.edit_text(f"✅ **تمت الموافقة على طلب الشحن #{dep_id} وتعبئة الرصيد بنجاح.**")
            return

        if data.startswith("rej_dep_"):
            dep_id = int(data.replace("rej_dep_", ""))
            dep = next((d for d in db.data["deposits"] if d["id"] == dep_id), None)
            if dep and dep["status"] == "pending":
                dep["status"] = "rejected"
                db.save()
                try:
                    await context.bot.send_message(
                        dep["user_id"],
                        f"❌ **عذراً، تم رفض طلب الشحن الخاص بك (# {dep_id}).**\nيرجى التواصل مع الدعم للتحقق من الإيصال.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                await notify_channel_deposit_status(context.bot, dep, "rejected")
                await query.message.edit_text(f"❌ **تم رفض طلب الشحن #{dep_id}.**")
            return

        if data.startswith("app_w_"):
            w_id = int(data.replace("app_w_", ""))
            w = next((x for x in db.data["withdrawals"] if x["id"] == w_id), None)
            if w and w["status"] == "pending":
                w["status"] = "approved"
                db.save()
                add_log(w["user_id"], f"سحب مقبول #{w_id}", -w["amount"])

                try:
                    await context.bot.send_message(
                        w["user_id"],
                        f"✅ **تمت الموافقة على طلب السحب الخاص بك (# {w_id}) وتحويل المبلغ بنجاح!**\n💵 **الصافي المحول:** `{w['net_amount']:,.2f}` NPS",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await notify_channel_withdrawal_status(context.bot, w, "approved")
                await query.message.edit_text(f"✅ **تمت الموافقة على طلب السحب #{w_id} وإكمال التحويل.**")
            return

        if data.startswith("rej_w_"):
            w_id = int(data.replace("rej_w_", ""))
            w = next((x for x in db.data["withdrawals"] if x["id"] == w_id), None)
            if w and w["status"] == "pending":
                w["status"] = "rejected"
                t_user = get_user(w["user_id"])
                if t_user:
                    t_user["balance"] = t_user.get("balance", 0.0) + w["amount"]
                    save_user(t_user)

                db.save()
                try:
                    await context.bot.send_message(
                        w["user_id"],
                        f"❌ **تم رفض طلب السحب الخاص بك (# {w_id}) وإعادة المبلغ (`{w['amount']}` NPS) لحسابك.**",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                await notify_channel_withdrawal_status(context.bot, w, "rejected")
                await query.message.edit_text(f"❌ **تم رفض طلب السحب #{w_id} وإعادة الرصيد للمستخدم.**")
            return

    except Exception as e:
        logger.error(f"Error in handle_callback: {e}")

# ----------------------------------------------------
# 13. خادم Flask لتطبيقات الويب وقراءة الرصيد وعجلة الحظ
# ----------------------------------------------------
flask_app = Flask(__name__)

# --- واجهات الـ HTML لموقع الألعاب وموقع عجلة الحظ ---
WHEEL_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Golden Games - عجلة الحظ الكبرى</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body {
            background: linear-gradient(135deg, #0f0c20, #1a103c);
            color: #ffffff;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            text-align: center;
            margin: 0;
            padding: 15px;
        }
        .card {
            background: rgba(255, 255, 255, 0.07);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            border: 1px solid rgba(255, 255, 255, 0.18);
            margin-bottom: 20px;
        }
        .balance-badge {
            background: linear-gradient(45deg, #ff9900, #ff5500);
            padding: 10px 20px;
            border-radius: 50px;
            font-size: 1.2rem;
            font-weight: bold;
            display: inline-block;
            margin: 10px 0;
        }
        .wheel-container {
            position: relative;
            width: 300px;
            height: 300px;
            margin: 20px auto;
        }
        #wheel {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            border: 8px solid #ffaa00;
            transition: transform 4s cubic-bezier(0.17, 0.67, 0.12, 0.99);
            box-shadow: 0 0 20px #ffaa00;
        }
        .pointer {
            position: absolute;
            top: -15px;
            left: 50%;
            transform: translateX(-50%);
            width: 0;
            height: 0;
            border-left: 15px solid transparent;
            border-right: 15px solid transparent;
            border-top: 30px solid #ff0055;
            z-index: 10;
        }
        .btn-spin {
            background: linear-gradient(45deg, #00e676, #00b0ff);
            color: white;
            border: none;
            padding: 15px 40px;
            font-size: 1.3rem;
            font-weight: bold;
            border-radius: 50px;
            cursor: pointer;
            box-shadow: 0 4px 15px rgba(0,230,118,0.4);
            transition: 0.2s;
        }
        .btn-spin:active {
            transform: scale(0.95);
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>🎰 منصة Golden Games 👑</h2>
        <p id="user-name">مرحباً بك عزيزي اللاعب</p>
        <div class="balance-badge">💰 الرصيد: <span id="user-balance">0.00</span> NPS</div>
        <p>🎡 اللفات المجانية: <strong id="user-spins">0</strong></p>
    </div>

    <div class="wheel-container">
        <div class="pointer"></div>
        <img id="wheel" src="https://cdn-icons-png.flaticon.com/512/812/812322.png" alt="Wheel">
    </div>

    <button class="btn-spin" onclick="spinWheel()">در العجلة الآن 🎯</button>
    <div id="result-msg" style="margin-top: 20px; font-weight: bold;"></div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        let userId = tg.initDataUnsafe?.user?.id || 0;
        let isSpinning = false;

        async function loadUserData() {
            if(!userId) return;
            try {
                let res = await fetch(`/api/get_user_info?user_id=${userId}`);
                let data = await res.json();
                if(data.success) {
                    document.getElementById('user-name').innerText = "👤 اللاعب: " + data.full_name;
                    document.getElementById('user-balance').innerText = data.balance.toLocaleString();
                    document.getElementById('user-spins').innerText = data.free_spins;
                }
            } catch(e) {
                console.error(e);
            }
        }

        async function spinWheel() {
            if(isSpinning) return;
            if(!userId) {
                alert("يرجى فتح اللعبة من داخل بوت التلغرام حصراً!");
                return;
            }

            isSpinning = true;
            document.getElementById('result-msg').innerText = "⏳ جاري التدوير والتحقق...";

            try {
                let res = await fetch('/api/wheel/spin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId })
                });
                let data = await res.json();

                if(!data.success) {
                    document.getElementById('result-msg').innerText = "❌ " + data.error;
                    isSpinning = false;
                    return;
                }

                let rotation = 360 * 5 + (data.segment_index * (360 / 11));
                let wheelEl = document.getElementById('wheel');
                wheelEl.style.transform = `rotate(${rotation}deg)`;

                setTimeout(() => {
                    document.getElementById('result-msg').innerText = `🎉 مبروك! فزت بـ: ${data.prize_title}`;
                    document.getElementById('user-balance').innerText = data.new_balance.toLocaleString();
                    document.getElementById('user-spins').innerText = data.remaining_spins;
                    isSpinning = false;
                }, 4000);

            } catch(e) {
                document.getElementById('result-msg').innerText = "❌ حدث خطأ بالاتصال بالسيرفر.";
                isSpinning = false;
            }
        }

        loadUserData();
    </script>
</body>
</html>
"""

GAMES_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Golden Games - موقع الألعاب</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body {
            background: #0d1117;
            color: #c9d1d9;
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 20px;
        }
        .box {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .bal {
            color: #2ea44f;
            font-size: 1.5rem;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="box">
        <h2>🎰 موقع الألعاب Golden Games 💎</h2>
        <p id="p-name">جاري التحميل...</p>
        <p>رصيدك الحالي بالموقع: <span class="bal" id="p-bal">0.00</span> NPS</p>
    </div>
    <div class="box">
        <h3>🥊 الألعاب المتوفرة حالياً 🎲</h3>
        <p>اختر اللعبة المفضلة وابدأ باللعب والربح المباشر!</p>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        let userId = tg.initDataUnsafe?.user?.id || 0;

        async function init() {
            if(!userId) return;
            let res = await fetch(`/api/get_user_info?user_id=${userId}`);
            let data = await res.json();
            if(data.success) {
                document.getElementById('p-name').innerText = "أهلاً بك: " + data.full_name;
                document.getElementById('p-bal').innerText = data.balance.toLocaleString();
            }
        }
        init();
    </script>
</body>
</html>
"""

@flask_app.route('/')
def home_route():
    return "🟢 Golden Games Bot & Web App Server 2026 Active!"

@flask_app.route('/wheel')
def wheel_route():
    return render_template_string(WHEEL_HTML)

@flask_app.route('/games')
def games_route():
    return render_template_string(GAMES_HTML)

# API لقراءة رصيد العميل المباشر
@flask_app.route('/api/get_user_info', methods=['GET'])
def api_get_user_info():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "user_id missing"}), 400

    u = get_user(int(user_id))
    if not u:
        return jsonify({"success": False, "error": "User not found"}), 444

    return jsonify({
        "success": True,
        "user_id": u["user_id"],
        "full_name": u["full_name"],
        "balance": u.get("balance", 0.0),
        "free_spins": u.get("free_spins", 0),
        "is_verified": u.get("is_verified", 0),
        "phone_verified": u.get("phone_verified", 0)
    })

# API لتشغيل عجلة الحظ الكبرى
@flask_app.route('/api/wheel/spin', methods=['POST'])
def api_wheel_spin():
    data = request.json or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "المعرف غير متوفر"}), 400

    res, err = calculate_wheel_spin(int(user_id))
    if err:
        return jsonify({"success": False, "error": err}), 400

    return jsonify({
        "success": True,
        "segment_index": res["segment_index"],
        "prize_title": res["title"],
        "prize_amount": res["amount"],
        "new_balance": res["new_balance"],
        "remaining_spins": res["remaining_spins"]
    })

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False)

# ----------------------------------------------------
# 14. تشغيل تطبيق التلغرام والسيرفر معاً
# ----------------------------------------------------
def main():
    global bot_loop, tg_app

    # تشغيل خادم WebApp في Thread خلفي
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    tg_app = application

    # إضافة المعالجات للأوامر والأزرار
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("⚡ Golden Games Bot & WebApp Started Successfully!")

    bot_loop = asyncio.get_event_loop()
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
