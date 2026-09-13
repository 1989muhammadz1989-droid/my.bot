import os
import glob
import json
import sqlite3
import random
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB_FILE = "database.db"
GAMES_DIR = "games"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # جدول مستخدمي تلجرام وأرصدتهم
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT DEFAULT 'User',
            balance REAL DEFAULT 100.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # جدول التحكم اللحظي بخوارزميات الألعاب
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_settings (
            game_id TEXT PRIMARY KEY,
            loss_rate REAL,
            normal_rate REAL,
            medium_rate REAL,
            high_rate REAL,
            mega_rate REAL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def load_games():
    """قراءة وفحص مجلد games ديناميكياً لإضافة أي لعبة جديدة فوراً"""
    games = {}
    if not os.path.exists(GAMES_DIR):
        os.makedirs(GAMES_DIR)
        
    json_files = glob.glob(os.path.join(GAMES_DIR, "*.json"))
    conn = get_db()
    cursor = conn.cursor()
    
    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                gdata = json.load(f)
                g_id = gdata.get("id")
                if not g_id:
                    continue
                
                # جلب إعدادات الخوارزمية الحالية من قاعدة البيانات أو استخدام الافتراضية
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
                    """, (g_id, default_algo["loss_rate"], default_algo["normal_rate"], 
                          default_algo["medium_rate"], default_algo["high_rate"], default_algo["mega_rate"]))
                    conn.commit()
                    algo = default_algo

                gdata["algo"] = algo
                games[g_id] = gdata
        except Exception as e:
            print(f"Error loading {filepath}: {e}")
            
    conn.close()
    return games

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/user/get", methods=["GET"])
def get_user():
    telegram_id = request.args.get("telegram_id")
    if not telegram_id:
        return jsonify({"success": False, "message": "Telegram ID مطلوب"}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (telegram_id, username, balance) VALUES (?, ?, ?)",
                       (str(telegram_id), f"tg_{telegram_id}", 100.0))
        conn.commit()
        balance = 100.0
        username = f"tg_{telegram_id}"
    else:
        balance = user["balance"]
        username = user["username"]
        
    conn.close()
    return jsonify({"success": True, "telegram_id": str(telegram_id), "username": username, "balance": balance})

@app.route("/api/games", methods=["GET"])
def api_games():
    games = load_games()
    return jsonify({"success": True, "games": list(games.values())})

@app.route("/api/play", methods=["POST"])
def play_game():
    data = request.json or {}
    telegram_id = str(data.get("telegram_id", ""))
    game_id = data.get("game_id", "")
    bet_amount = float(data.get("bet_amount", 0))

    if not telegram_id or not game_id or bet_amount <= 0:
        return jsonify({"success": False, "message": "بيانات غير صالحة"}), 400

    games = load_games()
    if game_id not in games:
        return jsonify({"success": False, "message": "اللعبة غير موجودة"}), 404

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()

    if not user or user["balance"] < bet_amount:
        conn.close()
        return jsonify({"success": False, "message": "رصيدك غير كافٍ لإجراء الرهان"}), 400

    current_balance = user["balance"]
    game = games[game_id]
    algo = game["algo"]

    # سحب الخوارزمية المحددة مسبقاً من المدير
    tiers = ["loss", "normal", "medium", "high", "mega"]
    weights = [
        algo["loss_rate"],
        algo["normal_rate"],
        algo["medium_rate"],
        algo["high_rate"],
        algo["mega_rate"]
    ]
    chosen_tier = random.choices(tiers, weights=weights, k=1)[0]

    multiplier = 0.0
    tier_label = ""

    # تطبيق حدود المضاعفات حسب الشروط
    if chosen_tier == "loss":
        multiplier = 0.0
        tier_label = "خسارة (ضربة خاسرة)"
    elif chosen_tier == "normal":
        multiplier = round(random.uniform(1.2, 5.0), 2)  # لا يتجاوز 5x
        tier_label = "ربح عادي (حتى 5x)"
    elif chosen_tier == "medium":
        multiplier = round(random.uniform(5.1, 10.0), 2) # لا يتجاوز 10x
        tier_label = "ربح متوسط (حتى 10x)"
    elif chosen_tier == "high":
        multiplier = round(random.uniform(10.1, 50.0), 2) # لا يتجاوز 50x
        tier_label = "ربح عالي (حتى 50x)"
    elif chosen_tier == "mega":
        multiplier = round(random.uniform(50.1, 500.0), 2) # لا يتجاوز 500x
        tier_label = "ربح ضخم (حتى 500x)"

    win_amount = bet_amount * multiplier
    net_change = win_amount - bet_amount
    new_balance = round(current_balance + net_change, 2)

    cursor.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (new_balance, telegram_id))
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

# --- نقاط التحكم الخاصة بالمدير (Admin APIs) ---

@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY updated_at DESC")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "users": users})

@app.route("/api/admin/user/update-balance", methods=["POST"])
def admin_update_balance():
    data = request.json or {}
    telegram_id = data.get("telegram_id")
    new_balance = data.get("balance")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (float(new_balance), str(telegram_id)))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تحديث الرصيد بنجاح"})

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
    return jsonify({"success": True, "message": f"تم تحديث خوارزمية اللعبة {game_id} فوراً!"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
