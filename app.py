import os
import glob
import json
import sqlite3
import random
import threading
import logging
import sys
import re
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

DB_FILE = "database.db"
GAMES_DIR = "games"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 0. تهيئة الاتصال بقاعدة البيانات (PostgreSQL / Supabase أو SQLite)
# ----------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_POSTGRES = False
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    if DATABASE_URL:
        IS_POSTGRES = True
        logger.info("تم اكتشاف DATABASE_URL: سيتم الاتصال بقاعدة بيانات PostgreSQL / Supabase")
except ImportError:
    if DATABASE_URL:
        logger.warning("DATABASE_URL موجود ولكن مكتبة psycopg2 غير مثبتة! سيتم التراجع إلى SQLite. تأكد من إضافة psycopg2-binary إلى requirements.txt")

class PGWrapperCursor:
    def __init__(self, pg_cursor):
        self._cursor = pg_cursor

    def execute(self, sql, params=()):
        adapted_sql = sql.replace("?", "%s")
        adapted_sql = re.sub(r'\bmax\s*\(\s*0\s*,', 'GREATEST(0,', adapted_sql, flags=re.IGNORECASE)
        adapted_sql = adapted_sql.replace("round(balance + %s, 2)", "ROUND(CAST(balance + %s AS NUMERIC), 2)")
        self._cursor.execute(adapted_sql, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

class PGWrapperConn:
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def cursor(self):
        return PGWrapperCursor(self._conn.cursor(cursor_factory=RealDictCursor))

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

def get_db():
    if IS_POSTGRES and DATABASE_URL:
        try:
            pg_conn = psycopg2.connect(DATABASE_URL)
            return PGWrapperConn(pg_conn)
        except Exception as e:
            logger.error(f"خطأ في الاتصال بـ PostgreSQL ({e})، جاري التراجع إلى SQLite...")
    
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

# ----------------------------------------------------
# 1. تهيئة قاعدة البيانات الموحدة مع البوت
# ----------------------------------------------------
def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # جدول مستخدمي تلجرام الموحد
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
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
    
    # جدول سجلات العمليات والألعاب
    logs_id_type = "SERIAL PRIMARY KEY" if (IS_POSTGRES and DATABASE_URL) else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS logs (
            id {logs_id_type},
            user_id TEXT,
            action TEXT,
            amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# 2. قراءة مجلد الألعاب ديناميكياً (Files & Subdirectories)
# ----------------------------------------------------
def load_games():
    """قراءة وفحص مجلد games ديناميكياً لإضافة أي لعبة حية جديدة فوراً"""
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
                        INSERT INTO game_settings (game_id, loss_rate, normal_rate, medium_rate, high_rate, mega_rate)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (game_id) DO NOTHING
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
# 3. الصفحات الرئيسية والتشغيل الديناميكي للالعاب الحية
# ----------------------------------------------------
@app.route("/", methods=["GET", "HEAD"])
def index():
    index_file = os.path.join(app.template_folder, "index.html")
    if os.path.exists(index_file):
        try:
            return render_template("index.html")
        except Exception as e:
            logger.error(f"خطأ في عرض index.html: {e}")
            return "Bot & Web Server are running!", 200
    return "Bot & Web Server are running!", 200

@app.route("/health", methods=["GET", "HEAD"])
@app.route("/ping", methods=["GET", "HEAD"])
def health_check():
    return jsonify({"status": "ok", "message": "Bot & Web Server are running!"}), 200

@app.route("/games", methods=["GET", "HEAD"])
def games_page():
    games_file = os.path.join(app.template_folder, "games.html")
    if os.path.exists(games_file):
        try:
            return render_template("games.html")
        except Exception as e:
            logger.error(f"خطأ في عرض games.html: {e}")
            return "Games Page", 200
    return "Games Page", 200

@app.route("/wheel", methods=["GET", "HEAD"])
def wheel_page():
    wheel_file = os.path.join(app.template_folder, "wheel.html")
    if os.path.exists(wheel_file):
        try:
            return render_template("wheel.html")
        except Exception as e:
            logger.error(f"خطأ في عرض wheel.html: {e}")
            return "Wheel Page", 200
    return "Wheel Page", 200

# مسار تشغيل الألعاب الحية التفاعلية تلقائياً عبر ID اللعبة
@app.route("/play/<game_id>")
def play_live_game(game_id):
    # 1. البحث عن ملف index.html داخل مجلد اللعبة (مثل games/rocket/index.html)
    subfolder_html = os.path.join(GAMES_DIR, game_id, "index.html")
    if os.path.exists(subfolder_html):
        return send_from_directory(os.path.join(GAMES_DIR, game_id), "index.html")
        
    # 2. البحث عن ملف HTML منفصل (مثل games/rocket.html)
    standalone_html = os.path.join(GAMES_DIR, f"{game_id}.html")
    if os.path.exists(standalone_html):
        return send_from_directory(GAMES_DIR, f"{game_id}.html")
        
    # 3. العودة للصفحة الرئيسية بحال عدم وجود ملف خاص باللعبة
    index_file = os.path.join(app.template_folder, "index.html")
    if os.path.exists(index_file):
        try:
            return render_template("index.html", game_id=game_id)
        except Exception as e:
            logger.error(f"خطأ في عرض index.html: {e}")
            return f"Game {game_id}", 200
    return f"Game {game_id}", 200

# خدمة الملفات الثابتة للألعاب (الخلفيات الحية، الأصوات، الصور، والـ JS)
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
        balance = user["balance"] or 0.0
        free_spins = user["free_spins"] or 0
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

    try:
        bet_amount = round(float(data.get("bet_amount", 0)), 2)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "مبلغ الرهان غير صالح"}), 400

    if not user_id or not game_id or bet_amount <= 0:
        return jsonify({"success": False, "message": "بيانات غير صالحة"}), 400

    games = load_games()
    if game_id not in games:
        return jsonify({"success": False, "message": "اللعبة غير موجودة"}), 404

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return jsonify({"success": False, "message": "المستخدم غير موجود"}), 404

    current_balance = user["balance"] if user["balance"] is not None else 0.0

    # منع التشغيل نهائياً إن لم يكن هناك رصيد كافٍ
    if current_balance < bet_amount:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب"}), 400

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

    win_amount = round(bet_amount * multiplier, 2)
    net_change = round(win_amount - bet_amount, 2)

    # تحديث الرصيد ذرياً بحسب الربح/الخسارة فقط، مع التأكد من وجود الرصيد
    cursor.execute("""
        UPDATE users 
        SET balance = round(balance + ?, 2), 
            total_spent = total_spent + ?, 
            games_played = games_played + 1, 
            updated_at = CURRENT_TIMESTAMP 
        WHERE user_id = ? AND balance >= ?
    """, (net_change, bet_amount, user_id, bet_amount))

    if cursor.rowcount == 0:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب"}), 400

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_balance = cursor.fetchone()["balance"]

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

    if not user or (user["free_spins"] or 0) <= 0:
        conn.close()
        return jsonify({"success": False, "message": "ليس لديك لفات مجانية متاحة!"}), 400

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

    reward_balance = 0.0
    spins_change = -1

    if result.isdigit():
        reward_balance = float(result)
    elif result == "try_again":
        spins_change = 0

    cursor.execute("""
        UPDATE users 
        SET balance = round(balance + ?, 2), 
            free_spins = max(0, free_spins + ?) 
        WHERE user_id = ? AND free_spins > 0
    """, (reward_balance, spins_change, user_id))

    cursor.execute("SELECT balance, free_spins FROM users WHERE user_id = ?", (user_id,))
    updated_user = cursor.fetchone()
    new_balance = updated_user["balance"]
    new_spins = updated_user["free_spins"]

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
        python_cmd = sys.executable or "python"
        os.system(f"{python_cmd} {bot_file}")

if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    threading.Thread(target=start_bot_thread, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
