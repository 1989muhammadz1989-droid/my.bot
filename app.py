import os
import glob
import json
import sqlite3
import random
import threading
import logging
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

DB_FILE = "database.db"
GAMES_DIR = "games"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 1. تهيئة قاعدة البيانات الموحدة مع البوت
# ----------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # جدول مستخدمي تلجرام الموحد (مطابق لملف bot.py)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            balance REAL DEFAULT 0.0,
            free_spins INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # جدول التحكم اللحظي بخوارزميات الألعاب
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_settings (
            game_id TEXT PRIMARY KEY,
            loss_rate REAL DEFAULT 60.0,
            normal_rate REAL DEFAULT 25.0,
            medium_rate REAL DEFAULT 10.0,
            high_rate REAL DEFAULT 4.5,
            mega_rate REAL DEFAULT 0.5
        )
    """)
    
    # جدول إعدادات البوت والعجلة
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 2. قراءة مجلد الألعاب ديناميكياً (Files & Subdirectories)
# ----------------------------------------------------
def load_games():
    """قراءة وفحص مجلد games ديناميكياً لإضافة أي لعبة جديدة فوراً"""
    games = {}
    if not os.path.exists(GAMES_DIR):
        os.makedirs(GAMES_DIR)

    conn = get_db()
    cursor = conn.cursor()

    # البحث عن ملفات .json المباشرة أو داخل مجلدات فرعية في games/
    json_files = glob.glob(os.path.join(GAMES_DIR, "*.json")) + glob.glob(os.path.join(GAMES_DIR, "*", "*.json"))

    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                gdata = json.load(f)
                g_id = gdata.get("id")
                if not g_id:
                    continue

                # جلب الخوارزمية من قاعدة البيانات أو استخدام الافتراضية
                cursor.execute("SELECT * FROM game_settings WHERE game_id = ?", (g_id,))
                row = cursor.fetchone()

                if row:
                    algo = {
                        "loss_rate": row["loss_rate"],
                        "normal_rate": row["normal_rate"],
                        "medium_rate": row["medium_rate"],
                        "high_rate": row["high_rate"],
                        "mega_rate": row["mega_rate"]
                    }
                else:
                    default_algo = gdata.get("default_algo", {
                        "loss_rate": 60.0,
                        "normal_rate": 25.0,
                        "medium_rate": 10.0,
                        "high_rate": 4.5,
                        "mega_rate": 0.5
                    })
                    cursor.execute("""
                        INSERT OR IGNORE INTO game_settings (game_id, loss_rate, normal_rate, medium_rate, high_rate, mega_rate)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (g_id, default_algo["loss_rate"], default_algo["normal_rate"],
                          default_algo["medium_rate"], default_algo["high_rate"], default_algo["mega_rate"]))
                    conn.commit()
                    algo = default_algo

                gdata["algo"] = algo
                games[g_id] = gdata
        except Exception as e:
            logger.error(f"خطأ في تحميل اللعبة {filepath}: {e}")

    conn.close()
    return games

# ----------------------------------------------------
# 3. الصفحات الرئيسية والـ WebApp
# ----------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/games")
def games_page():
    return render_template("games.html")

@app.route("/wheel")
def wheel_page():
    return render_template("wheel.html")

# خدمة الملفات الثابتة للألعاب داخل مجلد games
@app.route("/games/<path:filename>")
def serve_game_files(filename):
    return send_from_directory(GAMES_DIR, filename)

# ----------------------------------------------------
# 4. APIs بيانات المستخدمين والألعاب
# ----------------------------------------------------
@app.route("/api/user/get", methods=["GET"])
def get_user():
    user_id = request.args.get("user_id") or request.args.get("telegram_id")
    if not user_id:
        return jsonify({"success": False, "message": "user_id مطلوب"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, balance, free_spins FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user:
        cursor.execute("INSERT INTO users (user_id, full_name, balance, free_spins) VALUES (?, ?, 0.0, 0)",
                       (user_id, f"User_{user_id}"))
        conn.commit()
        balance = 0.0
        free_spins = 0
        full_name = f"User_{user_id}"
    else:
        balance = user["balance"]
        free_spins = user["free_spins"]
        full_name = user["full_name"] or f"User_{user_id}"

    conn.close()
    return jsonify({
        "success": True,
        "user_id": int(user_id),
        "full_name": full_name,
        "balance": balance,
        "free_spins": free_spins
    })

@app.route("/api/games", methods=["GET"])
def api_games():
    games = load_games()
    return jsonify({"success": True, "games": list(games.values())})

# ----------------------------------------------------
# 5. محرك تشغيل اللعبة والخصم/الإضافة اللحظي
# ----------------------------------------------------
@app.route("/api/play", methods=["POST"])
def play_game():
    data = request.json or {}
    user_id = str(data.get("user_id") or data.get("telegram_id", ""))
    game_id = data.get("game_id", "")
    bet_amount = float(data.get("bet_amount", 0))

    if not user_id or not game_id or bet_amount <= 0:
        return jsonify({"success": False, "message": "بيانات غير صالحة"}), 400

    games = load_games()
    if game_id not in games:
        return jsonify({"success": False, "message": "اللعبة غير موجودة"}), 404

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_spent, games_played FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user or user["balance"] < bet_amount:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك في تلجرام غير كافٍ للرهان"}), 400

    current_balance = user["balance"]
    game = games[game_id]
    algo = game["algo"]

    # سحب الخوارزمية المحددة واستخراج النتيجة بموجب الاحتمالات الحالية
    tiers = ["loss", "normal", "medium", "high", "mega"]
    weights = [
        algo["loss_rate"],
        algo["normal_rate"],
        algo["medium_rate"],
        algo["high_rate"],
        algo["mega_rate"]
    ]
    chosen_tier = random.choices(tiers, weights=weights, k=1)[0]

    if chosen_tier == "loss":
        multiplier = 0.0
        tier_label = "خسارة"
    elif chosen_tier == "normal":
        multiplier = round(random.uniform(1.2, 5.0), 2)
        tier_label = "ربح عادي (حتى 5x)"
    elif chosen_tier == "medium":
        multiplier = round(random.uniform(5.1, 10.0), 2)
        tier_label = "ربح متوسط (حتى 10x)"
    elif chosen_tier == "high":
        multiplier = round(random.uniform(10.1, 50.0), 2)
        tier_label = "ربح عالي (حتى 50x)"
    elif chosen_tier == "mega":
        multiplier = round(random.uniform(50.1, 500.0), 2)
        tier_label = "ربح ضخم (حتى 500x)"

    win_amount = bet_amount * multiplier
    net_change = win_amount - bet_amount
    new_balance = round(current_balance + net_change, 2)
    new_total_spent = (user["total_spent"] or 0.0) + bet_amount
    new_games_played = (user["games_played"] or 0) + 1

    cursor.execute("""
        UPDATE users 
        SET balance = ?, total_spent = ?, games_played = ?, updated_at = CURRENT_TIMESTAMP 
        WHERE user_id = ?
    """, (new_balance, new_total_spent, new_games_played, user_id))
    
    # تسجيل العملية في السجلات
    cursor.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                   (user_id, f"لعب {game_id} (مضاعف: {multiplier}x)", net_change))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "game_id": game_id,
        "tier": chosen_tier,
        "tier_label": tier_label,
        "multiplier": multiplier,
        "bet_amount": bet_amount,
        "win_amount": win_amount,
        "net_change": net_change,
        "new_balance": new_balance
    })

# ----------------------------------------------------
# 6. محرك عجلة الحظ (يستجيب لخوارزميات البوت)
# ----------------------------------------------------
@app.route("/api/wheel/spin", methods=["POST"])
def spin_wheel():
    data = request.json or {}
    user_id = str(data.get("user_id", ""))

    if not user_id:
        return jsonify({"success": False, "message": "user_id مطلوب"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, free_spins FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user or user["free_spins"] <= 0:
        conn.close()
        return jsonify({"success": False, "message": "ليس لديك لفات مجانية متاحة!"}), 400

    # قراءة نسب العجلة المحددة من البوت عبر جدول settings
    keys = [
        'wheel_prob_luck', 'wheel_prob_5', 'wheel_prob_10', 'wheel_prob_15',
        'wheel_prob_try_again', 'wheel_prob_25', 'wheel_prob_50', 'wheel_prob_100',
        'wheel_prob_250', 'wheel_prob_dep_bonus_20', 'wheel_prob_500', 'wheel_prob_1000'
    ]
    defaults = {
        'wheel_prob_luck': 25.0, 'wheel_prob_5': 20.0, 'wheel_prob_10': 15.0, 'wheel_prob_15': 10.0,
        'wheel_prob_try_again': 15.0, 'wheel_prob_25': 7.0, 'wheel_prob_50': 4.0, 'wheel_prob_100': 2.0,
        'wheel_prob_250': 1.0, 'wheel_prob_dep_bonus_20': 0.8, 'wheel_prob_500': 0.15, 'wheel_prob_1000': 0.05
    }

    probs = {}
    for k in keys:
        cursor.execute("SELECT value FROM settings WHERE key = ?", (k,))
        row = cursor.fetchone()
        probs[k] = float(row["value"]) if row else defaults[k]

    outcomes = ["luck", "5", "10", "15", "try_again", "25", "50", "100", "250", "dep_bonus_20", "500", "1000"]
    weights = [probs[f"wheel_prob_{o}"] for o in outcomes]

    result = random.choices(outcomes, weights=weights, k=1)[0]

    # تطبيق مكافأة العجلة
    reward_balance = 0.0
    spins_change = -1  # خصم لفة

    if result.isdigit():
        reward_balance = float(result)
    elif result == "try_again":
        spins_change = 0  # إلغاء الخصم (لفة إضافية)

    new_balance = round(user["balance"] + reward_balance, 2)
    new_spins = max(0, user["free_spins"] + spins_change)

    cursor.execute("UPDATE users SET balance = ?, free_spins = ? WHERE user_id = ?", (new_balance, new_spins, user_id))
    cursor.execute("INSERT INTO logs (user_id, action, amount) VALUES (?, ?, ?)",
                   (user_id, f"دوران عجلة الحظ (النتيجة: {result})", reward_balance))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "result": result,
        "reward_balance": reward_balance,
        "new_balance": new_balance,
        "new_free_spins": new_spins
    })

# ----------------------------------------------------
# 7. التحكم التلقائي بالخوارزميات (Admin APIs)
# ----------------------------------------------------
@app.route("/api/admin/algo/update", methods=["POST"])
def admin_update_algo():
    data = request.json or {}
    game_id = data.get("game_id")
    loss_rate = float(data.get("loss_rate", 60.0))
    normal_rate = float(data.get("normal_rate", 25.0))
    medium_rate = float(data.get("medium_rate", 10.0))
    high_rate = float(data.get("high_rate", 4.5))
    mega_rate = float(data.get("mega_rate", 0.5))

    total = loss_rate + normal_rate + medium_rate + high_rate + mega_rate
    if round(total, 1) != 100.0:
        return jsonify({"success": False, "message": f"مجموع النسب يجب أن يساوي 100% (المجموع الحالي: {total}%)"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO game_settings (game_id, loss_rate, normal_rate, medium_rate, high_rate, mega_rate)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            loss_rate=excluded.loss_rate,
            normal_rate=excluded.normal_rate,
            medium_rate=excluded.medium_rate,
            high_rate=excluded.high_rate,
            mega_rate=excluded.mega_rate
    """, (game_id, loss_rate, normal_rate, medium_rate, high_rate, mega_rate))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": f"تم تحديث خوارزمية {game_id} وتطبيقها فوراً!"})

@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, phone, balance, free_spins, total_spent FROM users ORDER BY updated_at DESC")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "users": users})

@app.route("/api/admin/user/update-balance", methods=["POST"])
def admin_update_balance():
    data = request.json or {}
    user_id = data.get("user_id")
    new_balance = data.get("balance")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (float(new_balance), str(user_id)))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تعديل الرصيد بنجاح!"})

# ----------------------------------------------------
# 8. تشغيل البوت بالتوازي مع السيرفر
# ----------------------------------------------------
def start_bot_thread():
    """تشغيل بوت التليجرام في الخلفية لمنع تعارضه مع سيرفر Flask"""
    bot_file = "bot-2.py" if os.path.exists("bot-2.py") else "bot.py"
    if os.path.exists(bot_file):
        logger.info(f"بدء تشغيل ملف البوت ({bot_file}) في مسار خلفي...")
        os.system(f"python {bot_file}")

# تشغيل خيط البوت مرة واحدة عند بدء التطبيق
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    threading.Thread(target=start_bot_thread, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
