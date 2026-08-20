import os
import sqlite3
import random
from flask import Flask, render_template, request, jsonify

GAME_FOLDER = 'templates'
app = Flask(__name__, template_folder=GAME_FOLDER, static_folder=GAME_FOLDER)
DB_NAME = 'database.db'

# --- الاتصال بقاعدة البيانات مع تفعيل وضع WAL لمنع التضارب ---
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

# --- تهيئة الجداول والإعدادات الافتراضية ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 100.0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    default_settings = {
        "bonus_win_rate": "40",
        "bonus_cap_1": "200",
        "bonus_cap_2": "500",
        "bonus_cap_3": "1000",
        
        "chance_loss": "70",
        "chance_win1": "15",
        "chance_win2": "10",
        "chance_win5": "5",
        "chance_win10": "0",
        "chance_win20": "0",
        "chance_win50": "0",
        
        "maintenance_mode": "off",
        "global_win_mode": "auto",
        
        # إعدادات خوارزمية الجرة القابلة للتعديل من البوت
        "jar_mult_pool": "2,3,5",     # قيم المضاعفات المتاحة للجرة
        "jar_chance_boost": "off"     # وضع رفع احتمالية ظهور الجرات (on / off)
    }
    
    for k, v in default_settings.items():
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (k, str(v)))
        
    conn.commit()
    conn.close()

init_db()

# --- جلب وتحديث الإعدادات ---
def get_setting(key, default_val=""):
    try:
        conn = get_db_connection()
        res = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        return res['value'] if res else str(default_val)
    except Exception:
        return str(default_val)

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

# --- إدارة رصيد المستخدم ---
def get_user_balance(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT balance FROM users WHERE user_id = ?', (str(user_id),)).fetchone()
    if user is None:
        conn.execute('INSERT INTO users (user_id, balance) VALUES (?, ?)', (str(user_id), 100.0))
        conn.commit()
        conn.close()
        return 100.0
    conn.close()
    return float(user['balance'])

def update_user_balance(user_id, new_balance):
    conn = get_db_connection()
    conn.execute('UPDATE users SET balance = ? WHERE user_id = ?', (float(new_balance), str(user_id)))
    conn.commit()
    conn.close()

# --- دالة تقييم شبكة اللعبة ومنطق الجرة (Wild) ---
def evaluate_grid(grid, bet):
    # خطوط الدفع الـ 9 الرسمية (تبدأ من العمود الأول يساراً)
    paylines = [
        [(0,0), (1,0), (2,0), (3,0), (4,0)], # line 1: أفقي علوي
        [(0,1), (1,1), (2,1), (3,1), (4,1)], # line 2: أفقي أوسط
        [(0,2), (1,2), (2,2), (3,2), (4,2)], # line 3: أفقي سفلي
        [(0,0), (1,1), (2,2), (3,1), (4,0)], # line 4: V هابط
        [(0,2), (1,1), (2,0), (3,1), (4,2)], # line 5: V صاعد
        [(0,1), (1,0), (2,0), (3,0), (4,1)], # line 6: قوس علوي
        [(0,1), (1,2), (2,2), (3,2), (4,1)], # line 7: قوس سفلي
        [(0,0), (1,0), (2,1), (3,2), (4,2)], # line 8: درجات للأسفل
        [(0,2), (1,2), (2,1), (3,0), (4,0)]  # line 9: درجات للأعلى
    ]

    jars_count = 0
    jar_positions = {}

    for r_idx, column in enumerate(grid):
        for c_idx, cell in enumerate(column):
            if cell.get("sym") == "🏺":
                jars_count += 1
                jar_positions[(r_idx, c_idx)] = cell.get("mult", 1)

    win_amount = 0.0
    winning_coords = []
    winning_jar_reels = set()
    winning_lines = []

    # 1. تقييم خطوط الدفع وتطبيق منطق الجرة (تكمل النقص وتزيد العدد)
    for line_idx, line in enumerate(paylines):
        # تحديد الرمز الأساسي للخط (أول رمز ليس جرة ولا Scatter)
        target_sym = None
        for coord in line:
            sym = grid[coord[0]][coord[1]]["sym"]
            if sym != "🏺" and sym not in ["⭐", "$"]:
                target_sym = sym
                break

        # إذا كان الخط يشتمل على جرات فقط دون رموز أخرى -> يحتسب على أعلى رمز قياسي '7'
        if not target_sym:
            target_sym = "7"

        count = 0
        current_coords = []
        line_jar_mult = 1
        line_jars = []

        # الاحتساب التتابعي من اليسار إلى اليمين (الجرة تكمل النقص وتزيد التوالي)
        for coord in line:
            c_sym = grid[coord[0]][coord[1]]["sym"]
            if c_sym == target_sym or c_sym == "🏺":
                count += 1
                current_coords.append(list(coord))
                if c_sym == "🏺":
                    line_jars.append(coord[0])
                    # ضرب مضاعفات الجرات المشاركة في هذا الخط تحديداً
                    line_jar_mult *= jar_positions.get((coord[0], coord[1]), 1)
            else:
                break # انقطاع تسلسل الخط

        line_base_mult = 0.0
        
        # جدول المضاعفات الأساسية حسب عدد الرموز المتتالية
        if target_sym == '7':
            if count == 2: line_base_mult = 1.0
            elif count == 3: line_base_mult = 2.0
            elif count == 4: line_base_mult = 6.0
            elif count >= 5: line_base_mult = 50.0

        elif target_sym in ['🍉', '🍇']:
            if count == 3: line_base_mult = 2.0
            elif count == 4: line_base_mult = 4.0
            elif count >= 5: line_base_mult = 6.0

        elif target_sym == '🔔':
            if count == 3: line_base_mult = 1.5
            elif count == 4: line_base_mult = 3.0
            elif count >= 5: line_base_mult = 4.0

        elif target_sym in ['🍋', '🍊', '🍍', '🍒']:
            if count == 3: line_base_mult = 1.0
            elif count == 4: line_base_mult = 2.0
            elif count >= 5: line_base_mult = 5.0

        if line_base_mult > 0:
            # احتساب الربح = (المضاعف الأساسي * مضاعف الجرات) * الرهان
            line_win = (line_base_mult * line_jar_mult) * bet
            win_amount += line_win
            winning_coords.extend(current_coords)
            winning_lines.append(line_idx)
            
            for r in line_jars:
                winning_jar_reels.add(r)

    # 2. تقييم رموز Scatter (⭐, $)
    star_coords = []
    dollar_coords = []
    for r_idx, column in enumerate(grid):
        for c_idx, cell in enumerate(column):
            if cell["sym"] == "⭐":
                star_coords.append([r_idx, c_idx])
            elif cell["sym"] == "$":
                dollar_coords.append([r_idx, c_idx])

    if len(star_coords) >= 3:
        win_amount += bet * (2.0 if len(star_coords) == 3 else 10.0)
        winning_coords.extend(star_coords)

    if len(dollar_coords) >= 3:
        win_amount += bet * (3.0 if len(dollar_coords) == 3 else 15.0)
        winning_coords.extend(dollar_coords)

    has_jar = len(winning_jar_reels) > 0 or jars_count > 0
    primary_jar_reel = list(winning_jar_reels)[0] if winning_jar_reels else (-1 if jars_count == 0 else list(jar_positions.keys())[0][0])
    max_jar_mult = max(jar_positions.values()) if jar_positions else 1

    return win_amount, winning_coords, has_jar, primary_jar_reel, max_jar_mult, jars_count, winning_lines

# --- دالة اختيار الفئة بناءً على الإعدادات ---
def choose_tier(is_bonus_buy=False):
    global_mode = get_setting("global_win_mode", "auto")
    if global_mode in ["loss", "win1", "win2", "win5", "win10", "win20", "win50"]:
        return global_mode

    if is_bonus_buy:
        bonus_win_rate = int(get_setting("bonus_win_rate", 40))
        if random.randint(1, 100) > bonus_win_rate:
            return "loss"
        return random.choices(["win1", "win2", "win5", "win10", "win20", "win50"], weights=[30, 25, 20, 15, 7, 3])[0]
    else:
        c_loss = float(get_setting("chance_loss", 70))
        c_win1 = float(get_setting("chance_win1", 15))
        c_win2 = float(get_setting("chance_win2", 10))
        c_win5 = float(get_setting("chance_win5", 5))
        c_win10 = float(get_setting("chance_win10", 0))
        c_win20 = float(get_setting("chance_win20", 0))
        c_win50 = float(get_setting("chance_win50", 0))
        
        tiers = ["loss", "win1", "win2", "win5", "win10", "win20", "win50"]
        weights = [c_loss, c_win1, c_win2, c_win5, c_win10, c_win20, c_win50]
        total = sum(weights)
        if total <= 0: 
            return "loss"
        return random.choices(tiers, weights=weights)[0]

# --- توليد الشبكة مع ضبط درجات ندرة الجرات وإعدادات البوت ---
def generate_controlled_grid(tier, bet, forced_jars=0, max_win_cap=None):
    # جلب قيم المضاعفات المتاحة من الإعدادات (تُحدد من قبل البوت)
    raw_mults = get_setting("jar_mult_pool", "2,3,5")
    try:
        jar_mults = [int(m.strip()) for m in raw_mults.split(",") if m.strip().isdigit()]
        if not jar_mults:
            jar_mults = [2, 3, 5]
    except Exception:
        jar_mults = [2, 3, 5]

    for _ in range(300):
        grid = []
        
        # السبعات محصورة بالأرباح الضخمة فقط
        if tier in ["win20", "win50"]:
            symbols_pool = ['🍋', '🍍', '🍊', '🍒', '🍉', '🔔', '🍇', '⭐', '$', '7']
            weights = [15, 15, 15, 15, 12, 12, 12, 3, 3, 1]
        else:
            symbols_pool = ['🍋', '🍍', '🍊', '🍒', '🍉', '🔔', '🍇', '⭐', '$']
            weights = [15, 15, 15, 15, 12, 12, 12, 4, 4]

        # 🎯 ضبط نسبة ظهور الجرات وفق الشروط وإمكانية تفعيل الرفع من البوت:
        if forced_jars > 0:
            target_jars = min(forced_jars, 3) # حصر ظهور 3 جرات بشراء المكافأة فقط
        else:
            rand = random.random()
            boost = get_setting("jar_chance_boost", "off") == "on"
            if boost:
                target_jars = 2 if rand < 0.30 else (1 if rand < 0.70 else 0)
            elif tier in ["win20", "win50"]:
                target_jars = 2 if rand < 0.20 else (1 if rand < 0.60 else 0)
            elif tier in ["win5", "win10"]:
                target_jars = 2 if rand < 0.08 else (1 if rand < 0.35 else 0)
            elif tier in ["win1", "win2"]:
                target_jars = 1 if rand < 0.12 else 0
            else:
                target_jars = 1 if rand < 0.02 else 0

        jar_reels = random.sample(range(5), target_jars) if target_jars > 0 else []

        for reel_idx in range(5):
            column = []
            has_jar = reel_idx in jar_reels
            jar_row = random.randint(0, 2) if has_jar else -1

            for row_idx in range(3):
                if row_idx == jar_row:
                    mult = random.choice(jar_mults)
                    column.append({"sym": "🏺", "mult": mult})
                else:
                    chosen_sym = random.choices(symbols_pool, weights=weights)[0]
                    column.append({"sym": chosen_sym, "mult": 1})
            grid.append(column)

        win_amount, winning_coords, h_jar, j_idx, j_mult, j_count, win_lines = evaluate_grid(grid, bet)

        if max_win_cap is not None and win_amount > max_win_cap:
            continue

        win_ratio = win_amount / bet if bet > 0 else 0

        if tier == "loss" and win_amount == 0:
            return grid, 0.0, [], h_jar, j_idx, j_mult, win_lines
        elif tier == "win1" and 0.5 <= win_ratio <= 1.8:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines
        elif tier == "win2" and 1.8 < win_ratio <= 3.8:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines
        elif tier == "win5" and 3.8 < win_ratio <= 7.5:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines
        elif tier == "win10" and 7.5 < win_ratio <= 15.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines
        elif tier == "win20" and 15.0 < win_ratio <= 35.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines
        elif tier == "win50" and win_ratio > 35.0:
            return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines

    # شبكة الأمان
    safe_symbols = ['🍋', '🍍', '🍊', '🍒', '🍉']
    grid = []
    for reel_idx in range(5):
        column = []
        for row in range(3):
            sym = safe_symbols[(reel_idx * 2 + row) % len(safe_symbols)]
            column.append({"sym": sym, "mult": 1})
        grid.append(column)

    if forced_jars > 0:
        jar_reels = random.sample(range(5), min(forced_jars, 3))
        for r_idx in jar_reels:
            grid[r_idx][1] = {"sym": "🏺", "mult": random.choice(jar_mults)}

    win_amount, winning_coords, h_jar, j_idx, j_mult, j_count, win_lines = evaluate_grid(grid, bet)
    if max_win_cap is not None and win_amount > max_win_cap:
        win_amount = 0.0
        winning_coords = []
        win_lines = []

    return grid, win_amount, winning_coords, h_jar, j_idx, j_mult, win_lines

# --- المسارات والواجهات (APIs) ---

@app.route('/api/get_user', methods=['POST'])
def get_user():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    balance = get_user_balance(user_id)
    maintenance = get_setting("maintenance_mode", "off") == "on"
    return jsonify({"success": True, "balance": balance, "maintenance": maintenance})

@app.route('/api/get_settings', methods=['GET', 'POST'])
def get_settings_api():
    conn = get_db_connection()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    settings_dict = {row['key']: row['value'] for row in rows}
    return jsonify({"success": True, "settings": settings_dict})

@app.route('/api/set_settings', methods=['POST'])
def set_settings_api():
    data = request.get_json() or {}
    for key, value in data.items():
        if key != 'user_id':
            set_setting(key, str(value))
    return jsonify({"success": True, "message": "تم تحديث الإعدادات بنجاح"})

@app.route('/api/update_balance', methods=['POST'])
def update_balance_api():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    amount = data.get('amount')
    action = data.get('action', 'add')
    
    if not user_id or amount is None:
        return jsonify({"success": False, "message": "بيانات غير مكتملة"})
    
    current = get_user_balance(user_id)
    new_bal = (current + float(amount)) if action == 'add' else float(amount)
    if new_bal < 0: 
        new_bal = 0.0
        
    update_user_balance(user_id, new_bal)
    return jsonify({"success": True, "new_balance": new_bal})

@app.route('/api/play_spin', methods=['POST'])
def play_spin():
    if get_setting("maintenance_mode", "off") == "on":
        return jsonify({
            "success": False, 
            "maintenance": True, 
            "message": "⚠️ اللعبة حالياً في وضع الصيانة. يرجى المحاولة لاحقاً."
        })

    data = request.get_json() or {}
    user_id = data.get('user_id', 'demo_user')
    buy_bonus_jars = int(data.get('buy_bonus_jars', 0))
    
    try:
        bet = float(data.get('bet', 3.0))
    except (ValueError, TypeError):
        bet = 3.0

    current_balance = get_user_balance(user_id)

    if buy_bonus_jars > 0:
        bonus_cost = data.get('bonus_cost') or data.get('cost')
        if bonus_cost is not None:
            try:
                spin_cost = float(bonus_cost)
            except (ValueError, TypeError):
                spin_cost = bet * 10 * buy_bonus_jars
        else:
            spin_cost = bet * 10 * buy_bonus_jars

        if current_balance < spin_cost:
            return jsonify({"success": False, "message": "رصيدك غير كافٍ لشراء المكافأة!"})

        current_balance -= spin_cost

        bet_ratio = bet / 3.0
        cap_key = f"bonus_cap_{buy_bonus_jars}"
        base_cap = float(get_setting(cap_key, 200 * buy_bonus_jars))
        max_win_cap = base_cap * bet_ratio
        tier = choose_tier(is_bonus_buy=True)
    else:
        spin_cost = bet
        if current_balance < spin_cost:
            return jsonify({"success": False, "message": "رصيدك غير كافٍ للعب!"})

        current_balance -= spin_cost
        max_win_cap = None
        tier = choose_tier(is_bonus_buy=False)

    grid, win_amount, winning_coords, has_jar, jar_reel_index, jar_multiplier, winning_lines = generate_controlled_grid(
        tier=tier,
        bet=bet, 
        forced_jars=buy_bonus_jars,
        max_win_cap=max_win_cap
    )

    new_balance = current_balance + win_amount
    update_user_balance(user_id, new_balance)

    meter_count = len(winning_coords)
    meter_fill_percent = min(100, meter_count * 10)

    return jsonify({
        "success": True,
        "grid": grid,
        "win_amount": win_amount,
        "new_balance": new_balance,
        "has_jar": has_jar,
        "jar_reel_index": jar_reel_index,
        "jar_multiplier": jar_multiplier,
        "winning_coords": winning_coords,
        "winning_lines": winning_lines,
        "meter_count": meter_count,
        "meter_fill": meter_fill_percent
    })

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
