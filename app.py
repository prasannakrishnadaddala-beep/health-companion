import os, json, datetime, hashlib, secrets, traceback
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "healthmate-default-change-this-key")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours

# ── Uploads folder ────────────────────────────────────────────────────────────
UPLOAD_FOLDER = "/tmp/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg'}

# ── Database setup ────────────────────────────────────────────────────────────
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
USE_POSTGRES = bool(_DATABASE_URL)

# SQLite path in /tmp so it's always writable
DB_PATH = "/tmp/health_data.db"

try:
    import psycopg2, psycopg2.extras
    PSYCOPG2_OK = True
except ImportError:
    PSYCOPG2_OK = False
    USE_POSTGRES = False

import sqlite3

def get_db():
    if USE_POSTGRES and PSYCOPG2_OK:
        return psycopg2.connect(_DATABASE_URL)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql):
    return sql.replace("?", "%s") if (USE_POSTGRES and PSYCOPG2_OK) else sql

def init_db():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        stmts = [
            "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT, google_fit_token TEXT, google_fit_connected INTEGER DEFAULT 0, strava_token TEXT)",
            "CREATE TABLE IF NOT EXISTS vitals (id SERIAL PRIMARY KEY, timestamp TEXT NOT NULL, oxygen REAL, heart_rate INTEGER, bp_sys INTEGER, bp_dia INTEGER, temperature REAL, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS medications (id SERIAL PRIMARY KEY, name TEXT NOT NULL, dose TEXT, frequency TEXT, times TEXT, color TEXT, active INTEGER DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS med_logs (id SERIAL PRIMARY KEY, med_id INTEGER, date TEXT, dose_index INTEGER, taken INTEGER DEFAULT 0, taken_at TEXT)",
            "CREATE TABLE IF NOT EXISTS health_records (id SERIAL PRIMARY KEY, filename TEXT, original_name TEXT, uploaded_at TEXT, analysis TEXT, file_type TEXT)",
            "CREATE TABLE IF NOT EXISTS cycle_log (id SERIAL PRIMARY KEY, start_date TEXT, end_date TEXT, cycle_length INTEGER, flow_intensity TEXT, symptoms TEXT, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS diet_log (id SERIAL PRIMARY KEY, date TEXT, meal_type TEXT, food_items TEXT, calories INTEGER, water_ml INTEGER, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS appointments (id SERIAL PRIMARY KEY, doctor_name TEXT, specialty TEXT, date TEXT, time TEXT, location TEXT, reason TEXT, notes TEXT, reminder_sent INTEGER DEFAULT 0, completed INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS chat_history (id SERIAL PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT)",
        ]
        for s in stmts:
            try:
                cur.execute(s)
                conn.commit()
            except Exception:
                conn.rollback()
        cur.close()
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT, google_fit_token TEXT, google_fit_connected INTEGER DEFAULT 0, strava_token TEXT);
            CREATE TABLE IF NOT EXISTS vitals (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, oxygen REAL, heart_rate INTEGER, bp_sys INTEGER, bp_dia INTEGER, temperature REAL, notes TEXT);
            CREATE TABLE IF NOT EXISTS medications (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, dose TEXT, frequency TEXT, times TEXT, color TEXT, active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS med_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER, date TEXT, dose_index INTEGER, taken INTEGER DEFAULT 0, taken_at TEXT);
            CREATE TABLE IF NOT EXISTS health_records (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, original_name TEXT, uploaded_at TEXT, analysis TEXT, file_type TEXT);
            CREATE TABLE IF NOT EXISTS cycle_log (id INTEGER PRIMARY KEY AUTOINCREMENT, start_date TEXT, end_date TEXT, cycle_length INTEGER, flow_intensity TEXT, symptoms TEXT, notes TEXT);
            CREATE TABLE IF NOT EXISTS diet_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, meal_type TEXT, food_items TEXT, calories INTEGER, water_ml INTEGER, notes TEXT);
            CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_name TEXT, specialty TEXT, date TEXT, time TEXT, location TEXT, reason TEXT, notes TEXT, reminder_sent INTEGER DEFAULT 0, completed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, timestamp TEXT);
        """)
        conn.commit()
    # ── Migrations: add columns if they don't exist ──────────────────
    migrate_cols = [
        ("users", "google_fit_token", "TEXT"),
        ("users", "google_fit_connected", "INTEGER DEFAULT 0"),
        ("users", "strava_token", "TEXT"),
        ("vitals", "username", "TEXT DEFAULT 'admin'"),
        ("medications", "username", "TEXT DEFAULT 'admin'"),
        ("health_records", "username", "TEXT DEFAULT 'admin'"),
        ("cycle_log", "username", "TEXT DEFAULT 'admin'"),
        ("diet_log", "username", "TEXT DEFAULT 'admin'"),
        ("appointments", "username", "TEXT DEFAULT 'admin'"),
        ("chat_history", "username", "TEXT DEFAULT 'admin'"),
        ("user_profile", "email", "TEXT"),
    ]
    # Also create profile table if not exists
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        try:
            cur.execute("""CREATE TABLE IF NOT EXISTS user_profile (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                full_name TEXT, age INTEGER, gender TEXT, weight_kg REAL,
                height_cm REAL, activity_level TEXT, health_goals TEXT,
                medical_conditions TEXT, allergies TEXT, dietary_pref TEXT,
                calorie_target INTEGER DEFAULT 2000,
                protein_target INTEGER DEFAULT 50, carb_target INTEGER DEFAULT 250,
                fat_target INTEGER DEFAULT 65, water_target INTEGER DEFAULT 2000,
                updated_at TEXT
            )""")
            conn.commit()
        except Exception:
            conn.rollback()
        cur.close()
    else:
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                full_name TEXT, age INTEGER, gender TEXT, weight_kg REAL,
                height_cm REAL, activity_level TEXT, health_goals TEXT,
                medical_conditions TEXT, allergies TEXT, dietary_pref TEXT,
                calorie_target INTEGER DEFAULT 2000,
                protein_target INTEGER DEFAULT 50, carb_target INTEGER DEFAULT 250,
                fat_target INTEGER DEFAULT 65, water_target INTEGER DEFAULT 2000,
                updated_at TEXT
            )""")
            conn.commit()
        except Exception:
            pass
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        for table, col, coltype in migrate_cols:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                conn.commit()
            except Exception:
                conn.rollback()
        cur.close()
    else:
        for table, col, coltype in migrate_cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                conn.commit()
            except Exception:
                pass
    conn.close()

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

def seed_admin():
    uname = os.environ.get("ADMIN_USERNAME", "admin")
    pwd   = os.environ.get("ADMIN_PASSWORD", "healthmate123")
    try:
        conn = get_db()
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO users (username,password_hash,created_at) VALUES (%s,%s,%s)",
                            (uname, hash_pw(pwd), datetime.datetime.now().isoformat()))
            conn.commit(); cur.close()
        else:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                conn.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,?)",
                             (uname, hash_pw(pwd), datetime.datetime.now().isoformat()))
                conn.commit()
        conn.close()
    except Exception as e:
        print(f"[seed_admin error] {e}")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def dec(*a, **k):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*a, **k)
    return dec

def get_ai_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    try:
        import anthropic, httpx
        transport = httpx.HTTPTransport(retries=2)
        http_client = httpx.Client(transport=transport, timeout=60.0)
        return anthropic.Anthropic(api_key=key, http_client=http_client)
    except Exception:
        return None

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        try:
            u = request.form.get("username", "").strip()
            p = request.form.get("password", "")
            conn = get_db()
            if USE_POSTGRES and PSYCOPG2_OK:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM users WHERE username=%s AND password_hash=%s", (u, hash_pw(p)))
                user = cur.fetchone()
                cur.close()
            else:
                row = conn.execute("SELECT * FROM users WHERE username=? AND password_hash=?",
                                   (u, hash_pw(p))).fetchone()
                user = dict(row) if row else None
            conn.close()
            if user:
                session.permanent = True
                session["logged_in"] = True
                session["username"] = u
                # Handle pending Google Fit OAuth code
                pending_code = session.pop("pending_gfit_code", None)
                if pending_code:
                    try:
                        redirect_uri = request.host_url.rstrip("/") + "/googlefit/callback"
                        token_dict = gfit.exchange_code(pending_code, redirect_uri)
                        if token_dict:
                            save_user_token(u, token_dict)
                            return redirect("/googlefit-setup?success=1")
                    except Exception:
                        pass
                return redirect(url_for("index"))
            error = "Invalid username or password."
        except Exception as e:
            traceback.print_exc()
            error = f"Login error: {str(e)}"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    """Public registration — anyone can create an account and get auto-logged in."""
    # If already logged in, go to app
    if session.get("logged_in") and request.method == "GET":
        # Show family management page for logged-in admin
        pass
    error = None; success = None
    if request.method == "POST":
        u = request.form.get("username","").strip().lower().replace(" ","_")
        p = request.form.get("password","")
        full_name = request.form.get("full_name","").strip()
        email = request.form.get("email","").strip()
        if not u or not p:
            error = "Username and password are required."
        elif len(u) < 3:
            error = "Username must be at least 3 characters."
        elif len(p) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                conn = get_db()
                if USE_POSTGRES and PSYCOPG2_OK:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO users (username,password_hash,created_at) VALUES (%s,%s,%s)",
                                (u, hash_pw(p), datetime.datetime.now().isoformat()))
                    conn.commit(); cur.close()
                else:
                    conn.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,?)",
                                 (u, hash_pw(p), datetime.datetime.now().isoformat()))
                    conn.commit()
                conn.close()
                # Create profile
                conn = get_db()
                if USE_POSTGRES and PSYCOPG2_OK:
                    cur = conn.cursor()
                    cur.execute("""INSERT INTO user_profile (username,full_name,email,updated_at)
                                   VALUES (%s,%s,%s,%s)
                                   ON CONFLICT (username) DO UPDATE SET
                                   full_name=EXCLUDED.full_name, email=EXCLUDED.email""",
                                (u, full_name, email, datetime.datetime.now().isoformat()))
                    conn.commit(); cur.close()
                else:
                    conn.execute("INSERT OR REPLACE INTO user_profile (username,full_name,email,updated_at) VALUES (?,?,?,?)",
                                 (u, full_name, email, datetime.datetime.now().isoformat()))
                    conn.commit()
                conn.close()
                # Auto-login after registration
                session.permanent = True
                session["logged_in"] = True
                session["username"] = u
                return redirect(url_for("index"))
            except Exception as e:
                if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                    error = f"Username '{u}' is already taken. Please choose a different one."
                else:
                    error = f"Registration failed: {str(e)}"
    return render_template("register.html", error=error, success=success,
                           is_logged_in=session.get("logged_in", False))

@app.route("/api/users", methods=["GET"])
@login_required
def get_users():
    """List all family member accounts."""
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT u.username, u.created_at,
                       p.full_name, p.age, p.gender, p.weight_kg, p.height_cm,
                       p.health_goals, p.medical_conditions
                       FROM users u LEFT JOIN user_profile p ON u.username=p.username
                       ORDER BY u.created_at""")
        users = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        users = [dict(r) for r in conn.execute("""
            SELECT u.username, u.created_at,
            p.full_name, p.age, p.gender, p.weight_kg, p.height_cm,
            p.health_goals, p.medical_conditions
            FROM users u LEFT JOIN user_profile p ON u.username=p.username
            ORDER BY u.created_at""").fetchall()]
    conn.close()
    return jsonify(users)

@app.route("/api/users/<username>/delete", methods=["POST"])
@login_required
def delete_user(username):
    """Delete a family member account (can't delete yourself)."""
    if username == session.get("username"):
        return jsonify({"error": "Cannot delete your own account"}), 400
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE username=%s",(username,))
        cur.execute("DELETE FROM user_profile WHERE username=%s",(username,))
        conn.commit(); cur.close()
    else:
        conn.execute("DELETE FROM users WHERE username=?",(username,))
        conn.execute("DELETE FROM user_profile WHERE username=?",(username,))
        conn.commit()
    conn.close()
    return jsonify({"status":"ok"})

@app.route("/api/users/<username>/reset-password", methods=["POST"])
@login_required
def reset_password(username):
    """Reset a family member's password."""
    new_pw = request.json.get("password","")
    if len(new_pw) < 6:
        return jsonify({"error":"Password too short"}), 400
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash=%s WHERE username=%s",(hash_pw(new_pw),username))
        conn.commit(); cur.close()
    else:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?",(hash_pw(new_pw),username))
        conn.commit()
    conn.close()
    return jsonify({"status":"ok"})

@app.route("/api/change-password", methods=["POST"])
@login_required
def change_password():
    """User changes their own password."""
    d = request.json
    old_pw = d.get("old_password","")
    new_pw = d.get("new_password","")
    if len(new_pw) < 6:
        return jsonify({"error":"New password must be at least 6 characters"}), 400
    username = session.get("username")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT password_hash FROM users WHERE username=%s",(username,))
        row = cur.fetchone(); cur.close()
    else:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?",(username,)).fetchone()
        row = dict(row) if row else None
    conn.close()
    if not row or row["password_hash"] != hash_pw(old_pw):
        return jsonify({"error":"Current password is incorrect"}), 400
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash=%s WHERE username=%s",(hash_pw(new_pw),username))
        conn.commit(); cur.close()
    else:
        conn.execute("UPDATE users SET password_hash=? WHERE username=?",(hash_pw(new_pw),username))
        conn.commit()
    conn.close()
    return jsonify({"status":"ok"})

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/googlefit-setup")
@login_required
def googlefit_setup_page():
    return render_template("googlefit_setup.html")

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "redirect_uri": get_redirect_uri(),
        "app_base_url": os.environ.get("APP_BASE_URL","not set"),
        "db": "postgresql" if (USE_POSTGRES and PSYCOPG2_OK) else "sqlite"
    })

# ── Vitals ────────────────────────────────────────────────────────────────────
@app.route("/api/vitals", methods=["GET"])
@login_required
def get_vitals():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM vitals WHERE username=%s ORDER BY timestamp DESC LIMIT 50",(u,))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM vitals WHERE username=? ORDER BY timestamp DESC LIMIT 50",(session.get("username","admin"),)).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/vitals", methods=["POST"])
@login_required
def add_vital():
    d = request.json; ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes,username) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(ts,d.get("oxygen"),d.get("heart_rate"),d.get("bp_sys"),d.get("bp_dia"),d.get("temperature"),d.get("notes",""),u))
        cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes,username) VALUES (?,?,?,?,?,?,?,?)",(ts,d.get("oxygen"),d.get("heart_rate"),d.get("bp_sys"),d.get("bp_dia"),d.get("temperature"),d.get("notes",""),u))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Medications ───────────────────────────────────────────────────────────────
@app.route("/api/medications", methods=["GET"])
@login_required
def get_medications():
    today = datetime.date.today().isoformat()
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM medications WHERE active=1 AND (username=%s OR username IS NULL)",(u,))
        meds = [dict(r) for r in cur.fetchall()]
        result = []
        for m in meds:
            times = json.loads(m["times"])
            cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur2.execute("SELECT * FROM med_logs WHERE med_id=%s AND date=%s", (m["id"], today))
            logs = [dict(r) for r in cur2.fetchall()]; cur2.close()
            tm = {l["dose_index"]: bool(l["taken"]) for l in logs}
            result.append({**m, "times": times, "taken": [tm.get(i, False) for i in range(len(times))]})
        cur.close()
    else:
        meds = [dict(r) for r in conn.execute("SELECT * FROM medications WHERE active=1 AND (username=? OR username IS NULL)",(session.get("username","admin"),)).fetchall()]
        result = []
        for m in meds:
            times = json.loads(m["times"])
            logs = [dict(r) for r in conn.execute("SELECT * FROM med_logs WHERE med_id=? AND date=?", (m["id"], today)).fetchall()]
            tm = {l["dose_index"]: bool(l["taken"]) for l in logs}
            result.append({**m, "times": times, "taken": [tm.get(i, False) for i in range(len(times))]})
    conn.close(); return jsonify(result)

@app.route("/api/medications", methods=["POST"])
@login_required
def add_medication():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO medications (name,dose,frequency,times,color,username) VALUES (%s,%s,%s,%s,%s,%s)",(d["name"],d.get("dose",""),d.get("frequency",""),json.dumps(d.get("times",["09:00"])),d.get("color","#378ADD"),u))
        cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO medications (name,dose,frequency,times,color,username) VALUES (?,?,?,?,?,?)",(d["name"],d.get("dose",""),d.get("frequency",""),json.dumps(d.get("times",["09:00"])),d.get("color","#378ADD"),u))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/medications/<int:mid>", methods=["DELETE"])
@login_required
def delete_medication(mid):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("UPDATE medications SET active=0 WHERE id=%s",(mid,)); cur.close()
    else:
        conn.execute("UPDATE medications SET active=0 WHERE id=?",(mid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/medications/log", methods=["POST"])
@login_required
def log_medication():
    d = request.json; today = datetime.date.today().isoformat()
    taken_at = datetime.datetime.now().isoformat() if d["taken"] else None
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id FROM med_logs WHERE med_id=%s AND date=%s AND dose_index=%s",(d["med_id"],today,d["dose_index"]))
        ex = cur.fetchone()
        if ex:
            cur.execute("UPDATE med_logs SET taken=%s,taken_at=%s WHERE id=%s",(d["taken"],taken_at,ex["id"]))
        else:
            cur.execute("INSERT INTO med_logs (med_id,date,dose_index,taken,taken_at) VALUES (%s,%s,%s,%s,%s)",(d["med_id"],today,d["dose_index"],d["taken"],taken_at))
        cur.close()
    else:
        ex = conn.execute("SELECT id FROM med_logs WHERE med_id=? AND date=? AND dose_index=?",(d["med_id"],today,d["dose_index"])).fetchone()
        if ex:
            conn.execute("UPDATE med_logs SET taken=?,taken_at=? WHERE id=?",(d["taken"],taken_at,ex["id"]))
        else:
            conn.execute("INSERT INTO med_logs (med_id,date,dose_index,taken,taken_at) VALUES (?,?,?,?,?)",(d["med_id"],today,d["dose_index"],d["taken"],taken_at))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Records ───────────────────────────────────────────────────────────────────
@app.route("/api/records", methods=["GET"])
@login_required
def get_records():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT id,original_name,uploaded_at,analysis,file_type FROM health_records WHERE username=%s ORDER BY uploaded_at DESC",(u,))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT id,original_name,uploaded_at,analysis,file_type FROM health_records WHERE username=? ORDER BY uploaded_at DESC",(session.get("username","admin"),)).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/records/upload", methods=["POST"])
@login_required
def upload_record():
    if "file" not in request.files: return jsonify({"error":"No file"}),400
    file = request.files["file"]; ext = file.filename.rsplit(".",1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS: return jsonify({"error":"File type not allowed"}),400
    filename = secure_filename(f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
    filepath = os.path.join(UPLOAD_FOLDER, filename); file.save(filepath)
    analysis = "File uploaded successfully."
    client = get_ai_client()
    if client:
        try:
            if ext == "txt":
                text = open(filepath,"r",errors="ignore").read()[:3000]
                msg = client.messages.create(model="claude-opus-4-5",max_tokens=600,messages=[{"role":"user","content":f"Analyze this health record in 4-5 sentences:\n\n{text}"}])
                analysis = msg.content[0].text
            elif ext in ("png","jpg","jpeg"):
                import base64
                img = base64.b64encode(open(filepath,"rb").read()).decode()
                mt = "image/jpeg" if ext in ("jpg","jpeg") else "image/png"
                msg = client.messages.create(model="claude-opus-4-5",max_tokens=600,messages=[{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":mt,"data":img}},{"type":"text","text":"Analyze this health record in 4-5 sentences."}]}])
                analysis = msg.content[0].text
        except Exception as e:
            analysis = f"Uploaded. Analysis unavailable: {str(e)}"
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO health_records (filename,original_name,uploaded_at,analysis,file_type,username) VALUES (%s,%s,%s,%s,%s,%s)",(filename,file.filename,datetime.datetime.now().isoformat(),analysis,ext,u)); cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO health_records (filename,original_name,uploaded_at,analysis,file_type,username) VALUES (?,?,?,?,?,?)",(filename,file.filename,datetime.datetime.now().isoformat(),analysis,ext,u))
    conn.commit(); conn.close()
    return jsonify({"status":"ok","analysis":analysis})

@app.route("/api/records/<int:rid>", methods=["DELETE"])
@login_required
def delete_record(rid):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("DELETE FROM health_records WHERE id=%s",(rid,)); cur.close()
    else:
        conn.execute("DELETE FROM health_records WHERE id=?",(rid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Cycle ─────────────────────────────────────────────────────────────────────
@app.route("/api/cycle", methods=["GET"])
@login_required
def get_cycle():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM cycle_log WHERE username=%s ORDER BY start_date DESC LIMIT 12",(u,))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM cycle_log WHERE username=? ORDER BY start_date DESC LIMIT 12",(session.get("username","admin"),)).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/cycle", methods=["POST"])
@login_required
def add_cycle():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO cycle_log (start_date,end_date,cycle_length,flow_intensity,symptoms,notes,username) VALUES (%s,%s,%s,%s,%s,%s,%s)",(d.get("start_date"),d.get("end_date"),d.get("cycle_length"),d.get("flow_intensity"),json.dumps(d.get("symptoms",[])),d.get("notes",""),u)); cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO cycle_log (start_date,end_date,cycle_length,flow_intensity,symptoms,notes,username) VALUES (?,?,?,?,?,?,?)",(d.get("start_date"),d.get("end_date"),d.get("cycle_length"),d.get("flow_intensity"),json.dumps(d.get("symptoms",[])),d.get("notes",""),u))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Diet ──────────────────────────────────────────────────────────────────────
@app.route("/api/diet", methods=["GET"])
@login_required
def get_diet():
    date = request.args.get("date", datetime.date.today().isoformat()); conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM diet_log WHERE date=%s AND username=%s ORDER BY id",(date,u))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM diet_log WHERE date=? AND username=? ORDER BY id",(date,session.get("username","admin"))).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/diet", methods=["POST"])
@login_required
def add_diet():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes,username) VALUES (%s,%s,%s,%s,%s,%s,%s)",(d.get("date",datetime.date.today().isoformat()),d.get("meal_type"),d.get("food_items"),d.get("calories",0),d.get("water_ml",0),d.get("notes",""),u)); cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes,username) VALUES (?,?,?,?,?,?,?)",(d.get("date",datetime.date.today().isoformat()),d.get("meal_type"),d.get("food_items"),d.get("calories",0),d.get("water_ml",0),d.get("notes",""),u))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/diet/<int:eid>", methods=["DELETE"])
@login_required
def delete_diet(eid):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("DELETE FROM diet_log WHERE id=%s",(eid,)); cur.close()
    else:
        conn.execute("DELETE FROM diet_log WHERE id=?",(eid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/analyze-calories", methods=["POST"])
@login_required
def analyze_calories():
    d = request.json
    food_items = d.get("food_items","").strip()
    if not food_items:
        return jsonify({"error": "No food items provided"}), 400
    client = get_ai_client()
    if not client:
        return jsonify({"error": "AI not configured"}), 400
    try:
        prompt = f"""You are a nutrition expert. Analyze these Indian food items and estimate total calories.

Food: {food_items}

Respond ONLY with a JSON object like this (no markdown, no extra text):
{{"calories": 450, "breakdown": "Rice 200kcal + Dal 150kcal + Bitter gourd 100kcal", "note": "Approximate values for typical Indian serving sizes"}}

Rules:
- Use typical Indian home-cooked portion sizes
- Be accurate for Indian foods like idly, dosa, rice, dal, sabzi, roti etc.
- Return only the JSON, nothing else"""

        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        import re
        text = msg.content[0].text.strip()
        # Extract JSON from response
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            return jsonify(result)
        return jsonify({"error": "Could not parse AI response"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ── User Profile ──────────────────────────────────────────────────────────────
@app.route("/api/profile", methods=["GET"])
@login_required
def get_profile():
    username = session.get("username")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM user_profile WHERE username=%s", (username,))
        row = cur.fetchone(); cur.close()
    else:
        r = conn.execute("SELECT * FROM user_profile WHERE username=?", (username,)).fetchone()
        row = dict(r) if r else None
    conn.close()
    return jsonify(dict(row) if row else {})

@app.route("/api/profile", methods=["POST"])
@login_required
def save_profile():
    d = request.json
    username = session.get("username")
    ts = datetime.datetime.now().isoformat()
    conn = get_db()
    # Calculate targets based on profile using Mifflin-St Jeor
    age = d.get("age", 30)
    weight = d.get("weight_kg", 60)
    height = d.get("height_cm", 165)
    gender = d.get("gender", "female")
    activity = d.get("activity_level", "moderate")
    activity_mult = {"sedentary":1.2,"light":1.375,"moderate":1.55,"active":1.725,"very_active":1.9}.get(activity,1.55)
    if gender == "female":
        bmr = 10*weight + 6.25*height - 5*age - 161
    else:
        bmr = 10*weight + 6.25*height - 5*age + 5
    tdee = round(bmr * activity_mult)
    goal = d.get("health_goals","")
    if "lose" in goal.lower() or "weight loss" in goal.lower():
        cal_target = tdee - 400
    elif "gain" in goal.lower():
        cal_target = tdee + 300
    else:
        cal_target = tdee
    protein_target = round(weight * 1.2)
    fat_target = round(cal_target * 0.25 / 9)
    carb_target = round((cal_target - protein_target*4 - fat_target*9) / 4)
    water_target = round(weight * 35)

    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("""INSERT INTO user_profile
            (username,full_name,age,gender,weight_kg,height_cm,activity_level,health_goals,
             medical_conditions,allergies,dietary_pref,calorie_target,protein_target,
             carb_target,fat_target,water_target,email,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (username) DO UPDATE SET
            full_name=EXCLUDED.full_name, age=EXCLUDED.age, gender=EXCLUDED.gender,
            weight_kg=EXCLUDED.weight_kg, height_cm=EXCLUDED.height_cm,
            activity_level=EXCLUDED.activity_level, health_goals=EXCLUDED.health_goals,
            medical_conditions=EXCLUDED.medical_conditions, allergies=EXCLUDED.allergies,
            dietary_pref=EXCLUDED.dietary_pref, calorie_target=EXCLUDED.calorie_target,
            protein_target=EXCLUDED.protein_target, carb_target=EXCLUDED.carb_target,
            fat_target=EXCLUDED.fat_target, water_target=EXCLUDED.water_target,
            email=EXCLUDED.email, updated_at=EXCLUDED.updated_at""",
            (username, d.get("full_name",""), age, gender, weight, height,
             activity, d.get("health_goals",""), d.get("medical_conditions",""),
             d.get("allergies",""), d.get("dietary_pref",""),
             cal_target, protein_target, carb_target, fat_target, water_target,
             d.get("email",""), ts))
        cur.close()
    else:
        conn.execute("DELETE FROM user_profile WHERE username=?", (username,))
        conn.execute("""INSERT INTO user_profile
            (username,full_name,age,gender,weight_kg,height_cm,activity_level,health_goals,
             medical_conditions,allergies,dietary_pref,calorie_target,protein_target,
             carb_target,fat_target,water_target,email,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (username, d.get("full_name",""), age, gender, weight, height,
             activity, d.get("health_goals",""), d.get("medical_conditions",""),
             d.get("allergies",""), d.get("dietary_pref",""),
             cal_target, protein_target, carb_target, fat_target, water_target,
             d.get("email",""), ts))
    conn.commit(); conn.close()
    return jsonify({"status":"ok","calorie_target":cal_target,"protein_target":protein_target,
                    "carb_target":carb_target,"fat_target":fat_target,"water_target":water_target})

@app.route("/api/analyze-nutrition", methods=["POST"])
@login_required
def analyze_nutrition():
    d = request.json
    food_items = d.get("food_items","").strip()
    if not food_items:
        return jsonify({"error":"No food items"}), 400
    client = get_ai_client()
    if not client:
        return jsonify({"error":"AI not configured"}), 400
    # Get user profile for personalized analysis
    username = session.get("username")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM user_profile WHERE username=%s",(username,))
        profile = cur.fetchone(); cur.close()
    else:
        r = conn.execute("SELECT * FROM user_profile WHERE username=?",(username,)).fetchone()
        profile = dict(r) if r else None
    conn.close()
    profile_ctx = ""
    if profile:
        profile_ctx = f"Person: {profile.get('age','?')} yr old {profile.get('gender','female')}, {profile.get('weight_kg','?')}kg, {profile.get('height_cm','?')}cm, {profile.get('activity_level','moderate')} activity. Goals: {profile.get('health_goals','')}. Conditions: {profile.get('medical_conditions','')}. Diet: {profile.get('dietary_pref','')}."
    try:
        prompt = f"""You are an expert nutritionist specializing in Indian cuisine. Analyze this meal with detailed macro and micronutrients.

Food: {food_items}
{profile_ctx}

Respond ONLY with a valid JSON object (no markdown, no extra text):
{{
  "calories": 590,
  "protein_g": 18,
  "carbs_g": 95,
  "fat_g": 12,
  "fiber_g": 8,
  "sugar_g": 4,
  "sodium_mg": 420,
  "calcium_mg": 85,
  "iron_mg": 3.2,
  "vitamin_c_mg": 15,
  "potassium_mg": 380,
  "breakdown": "Rice 300kcal + Dal 190kcal + Bitter gourd 100kcal",
  "health_note": "Good fiber content. Dal provides plant protein. Bitter gourd helps blood sugar.",
  "missing_nutrients": "Low in Vitamin B12, Omega-3",
  "suggestions": "Add a glass of buttermilk for calcium and probiotics"
}}

Use typical Indian home-cooked portions. Be accurate for Indian foods."""
        msg = client.messages.create(model="claude-opus-4-5", max_tokens=500,
            messages=[{"role":"user","content":prompt}])
        import re
        text = msg.content[0].text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return jsonify(json.loads(match.group()))
        return jsonify({"error":"Parse failed"})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/api/diet-analysis", methods=["POST"])
@login_required
def diet_analysis():
    """Full day diet analysis by AI dietician."""
    d = request.json
    date = d.get("date", datetime.date.today().isoformat())
    client = get_ai_client()
    if not client: return jsonify({"error":"AI not configured"}),400
    username = session.get("username")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM diet_log WHERE date=%s AND username=%s ORDER BY id",(date,u))
        entries = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM user_profile WHERE username=%s",(username,))
        profile = cur.fetchone(); cur.close()
    else:
        entries = [dict(r) for r in conn.execute("SELECT * FROM diet_log WHERE date=? AND username=? ORDER BY id",(date,session.get("username","admin"))).fetchall()]
        r = conn.execute("SELECT * FROM user_profile WHERE username=?",(username,)).fetchone()
        profile = dict(r) if r else None
    conn.close()
    if not entries:
        return jsonify({"analysis":"No food logged for this day yet. Please log your meals first."})
    food_summary = "\n".join([f"- {e['meal_type']}: {e['food_items']} ({e['calories']} kcal)" for e in entries])
    total_cal = sum(e['calories'] or 0 for e in entries)
    total_water = sum(e['water_ml'] or 0 for e in entries)
    nutrients_json = json.dumps([json.loads(e['notes']) if e.get('notes','').startswith('{') else {} for e in entries])
    profile_ctx = ""
    if profile:
        profile_ctx = f"\nPatient profile: {profile.get('full_name','')}, {profile.get('age','?')} years, {profile.get('gender','female')}, {profile.get('weight_kg','?')}kg, {profile.get('height_cm','?')}cm height, {profile.get('activity_level','moderate')} activity level.\nHealth goals: {profile.get('health_goals','')}\nMedical conditions: {profile.get('medical_conditions','')}\nDietary preferences: {profile.get('dietary_pref','')}\nDaily targets: {profile.get('calorie_target',2000)} kcal, {profile.get('protein_target',50)}g protein, {profile.get('carb_target',250)}g carbs, {profile.get('fat_target',65)}g fat, {profile.get('water_target',2000)}ml water"
    prompt = f"""You are a personalized AI dietician. Analyze this patient's diet for {date}.
{profile_ctx}

Food consumed:
{food_summary}
Total: {total_cal} kcal, {total_water}ml water

Give a detailed, personalized dietary analysis. Be specific, warm, and actionable. Use this structure:

## Overall Assessment
[2-3 sentences about today's diet overall]

## Macronutrient Balance
[Protein/carbs/fat analysis with specific numbers]

## Key Micronutrients
[What's good, what's missing with specific vitamins/minerals]

## Hydration
[Water intake assessment vs target]

## Tomorrow's Recommendations
- [Specific actionable tip 1]
- [Specific actionable tip 2]
- [Specific actionable tip 3]

## Health Alert (if any)
[Any concerns based on medical conditions - skip if none]

## Recipe Suggestion
[One specific Indian recipe to fill nutritional gaps with ingredients]

Be warm, personal, specific to Indian diet. Address them by their health goals."""
    try:
        msg = client.messages.create(model="claude-opus-4-5", max_tokens=800,
            messages=[{"role":"user","content":prompt}])
        return jsonify({"analysis": msg.content[0].text})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

# ── Appointments ──────────────────────────────────────────────────────────────
@login_required
def get_appointments():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT * FROM appointments WHERE username=%s ORDER BY date,time",(u,))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM appointments WHERE username=? ORDER BY date,time",(session.get("username","admin"),)).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/appointments", methods=["POST"])
@login_required
def add_appointment():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        u=session.get("username","admin"); cur.execute("INSERT INTO appointments (doctor_name,specialty,date,time,location,reason,notes,username) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(d["doctor_name"],d.get("specialty",""),d["date"],d.get("time",""),d.get("location",""),d.get("reason",""),d.get("notes",""),u)); cur.close()
    else:
        u=session.get("username","admin"); conn.execute("INSERT INTO appointments (doctor_name,specialty,date,time,location,reason,notes,username) VALUES (?,?,?,?,?,?,?,?)",(d["doctor_name"],d.get("specialty",""),d["date"],d.get("time",""),d.get("location",""),d.get("reason",""),d.get("notes",""),u))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/appointments/<int:aid>", methods=["PATCH"])
@login_required
def update_appointment(aid):
    d = request.json; conn = get_db()
    if "completed" in d:
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor(); cur.execute("UPDATE appointments SET completed=%s WHERE id=%s",(d["completed"],aid)); cur.close()
        else:
            conn.execute("UPDATE appointments SET completed=? WHERE id=?",(d["completed"],aid))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/appointments/<int:aid>", methods=["DELETE"])
@login_required
def delete_appointment(aid):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("DELETE FROM appointments WHERE id=%s",(aid,)); cur.close()
    else:
        conn.execute("DELETE FROM appointments WHERE id=?",(aid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Chat ──────────────────────────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    d = request.json; user_msg = d.get("message","")
    if not user_msg: return jsonify({"error":"Empty"}),400
    client = get_ai_client()
    if not client:
        return jsonify({"reply":"⚠️ ANTHROPIC_API_KEY not set. Add it in Railway → Variables tab."})
    today = datetime.date.today().isoformat(); conn = get_db()
    u = session.get("username","admin")
    try:
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM vitals WHERE username=%s ORDER BY timestamp DESC LIMIT 1",(u,)); lv = cur.fetchone()
            cur.execute("SELECT name,dose FROM medications WHERE active=1 AND (username=%s OR username IS NULL)",(u,)); meds = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=%s AND (m.username=%s OR m.username IS NULL)",(today,u)); mlogs = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM appointments WHERE date>=%s AND completed=0 AND username=%s ORDER BY date,time LIMIT 1",(today,u)); appt = cur.fetchone()
            cur.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=%s AND username=%s",(today,u)); diet = cur.fetchone()
            cur.execute("SELECT role,content FROM chat_history WHERE username=%s ORDER BY id DESC LIMIT 10",(u,)); hist = list(reversed([dict(r) for r in cur.fetchall()]))
            cur.close()
        else:
            lv = conn.execute("SELECT * FROM vitals WHERE username=? ORDER BY timestamp DESC LIMIT 1",(u,)).fetchone()
            lv = dict(lv) if lv else None
            meds = [dict(r) for r in conn.execute("SELECT name,dose FROM medications WHERE active=1 AND (username=? OR username IS NULL)",(u,)).fetchall()]
            mlogs = [dict(r) for r in conn.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=? AND (m.username=? OR m.username IS NULL)",(today,u)).fetchall()]
            appt = conn.execute("SELECT * FROM appointments WHERE date>=? AND completed=0 AND username=? ORDER BY date,time LIMIT 1",(today,u)).fetchone()
            appt = dict(appt) if appt else None
            diet = conn.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=? AND username=?",(today,u)).fetchone()
            diet = dict(diet) if diet else None
            hist = list(reversed([dict(r) for r in conn.execute("SELECT role,content FROM chat_history WHERE username=? ORDER BY id DESC LIMIT 10",(u,)).fetchall()]))
    except Exception as e:
        conn.close()
        return jsonify({"reply": f"Database error: {str(e)}"})

    taken = [r["name"] for r in mlogs if r["taken"]]
    due   = [m["name"] for m in meds if m["name"] not in taken]
    vs = f"O2:{lv['oxygen']}% HR:{lv['heart_rate']}bpm BP:{lv['bp_sys']}/{lv['bp_dia']} Temp:{lv['temperature']}°F" if lv else "No readings yet"
    sys_p = f"""You are a compassionate AI health companion. Be warm, concise (3-5 sentences). Always recommend a doctor for medical decisions.
Today: {today} | Vitals: {vs}
Meds due: {', '.join(due) or 'All taken'} | Appointment: {appt['doctor_name']+' on '+appt['date'] if appt else 'None'}
Diet: {str(diet['cal'] or 0)+' kcal' if diet else 'Not logged'}"""

    messages = [{"role": h["role"] if h["role"] in ("user","assistant") else "user", "content": h["content"]} for h in hist]
    messages.append({"role":"user","content":user_msg})
    try:
        resp = client.messages.create(model="claude-opus-4-5", max_tokens=800, system=sys_p, messages=messages)
        reply = resp.content[0].text
    except Exception as e:
        reply = f"AI error: {str(e)}"

    ts = datetime.datetime.now().isoformat()
    try:
        u = session.get("username","admin")
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor()
            cur.execute("INSERT INTO chat_history (role,content,timestamp,username) VALUES (%s,%s,%s,%s)",("user",user_msg,ts,u))
            cur.execute("INSERT INTO chat_history (role,content,timestamp,username) VALUES (%s,%s,%s,%s)",("assistant",reply,ts,u))
            cur.close()
        else:
            conn.execute("INSERT INTO chat_history (role,content,timestamp,username) VALUES (?,?,?,?)",("user",user_msg,ts,u))
            conn.execute("INSERT INTO chat_history (role,content,timestamp,username) VALUES (?,?,?,?)",("assistant",reply,ts,u))
        conn.commit()
    except Exception: pass
    conn.close()
    return jsonify({"reply": reply})

@app.route("/api/chat/history")
@login_required
def get_chat_history():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        u=session.get("username","admin"); cur.execute("SELECT role,content,timestamp FROM chat_history WHERE username=%s ORDER BY id DESC LIMIT 40",(u,))
        data = list(reversed([dict(r) for r in cur.fetchall()])); cur.close()
    else:
        data = list(reversed([dict(r) for r in conn.execute("SELECT role,content,timestamp FROM chat_history WHERE username=? ORDER BY id DESC LIMIT 40",(session.get("username","admin"),)).fetchall()]))
    conn.close(); return jsonify(data)

@app.route("/api/chat/clear", methods=["POST"])
@login_required
def clear_chat():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); u=session.get("username","admin"); cur.execute("DELETE FROM chat_history WHERE username=%s",(u,)); cur.close()
    else:
        conn.execute("DELETE FROM chat_history WHERE username=?",(session.get("username","admin"),))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})


# ── Google Fit (Production Web OAuth — multi-user) ────────────────────────────
try:
    import google_fit as gfit
    GFIT_OK = True
except Exception:
    gfit = None
    GFIT_OK = False

def get_redirect_uri():
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if not base:
        # fallback: build from request, force https
        base = "https://" + request.host
    # Always force https
    base = base.replace("http://", "https://")
    return f"{base}/googlefit/callback"

def get_user_token(username):
    """Load Google Fit token for a user from DB."""
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT google_fit_token FROM users WHERE username=%s", (username,))
        row = cur.fetchone(); cur.close()
    else:
        row = conn.execute("SELECT google_fit_token FROM users WHERE username=?", (username,)).fetchone()
        row = dict(row) if row else None
    conn.close()
    if row and row.get("google_fit_token"):
        try:
            return json.loads(row["google_fit_token"])
        except Exception:
            return None
    return None

def save_user_token(username, token_dict):
    """Save updated Google Fit token back to DB."""
    conn = get_db()
    token_json = json.dumps(token_dict)
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("UPDATE users SET google_fit_token=%s, google_fit_connected=1 WHERE username=%s", (token_json, username))
        cur.close()
    else:
        conn.execute("UPDATE users SET google_fit_token=?, google_fit_connected=1 WHERE username=?", (token_json, username))
    conn.commit(); conn.close()

def clear_user_token(username):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("UPDATE users SET google_fit_token=NULL, google_fit_connected=0 WHERE username=%s", (username,))
        cur.close()
    else:
        conn.execute("UPDATE users SET google_fit_token=NULL, google_fit_connected=0 WHERE username=?", (username,))
    conn.commit(); conn.close()

@app.route("/api/googlefit/status")
@login_required
def gf_status():
    username = session.get("username")
    token = get_user_token(username)
    return jsonify({
        "libs_available": GFIT_OK and gfit.GOOGLE_LIBS_AVAILABLE,
        "configured": GFIT_OK and gfit.is_configured(),
        "authenticated": bool(token)
    })

@app.route("/googlefit/connect")
@login_required
def gf_connect():
    """Redirect user to Google OAuth page — pass username in state."""
    if not GFIT_OK or not gfit.is_configured():
        return redirect("/googlefit-setup?error=not_configured")
    # Encode username in state so callback works even if session cookie is lost
    import base64
    state = base64.urlsafe_b64encode(session["username"].encode()).decode()
    auth_url = gfit.get_auth_url(get_redirect_uri(), state=state)
    if not auth_url:
        return redirect("/googlefit-setup?error=no_auth_url")
    return redirect(auth_url)

@app.route("/googlefit/callback")
def gf_callback():
    """Google redirects here — username recovered from state parameter."""
    code = request.args.get("code")
    error = request.args.get("error")
    state = request.args.get("state", "")

    if error or not code:
        return redirect("/googlefit-setup?error=" + (error or "no_code"))

    # Recover username from state (doesn't depend on session cookie)
    username = None
    if state:
        try:
            import base64
            username = base64.urlsafe_b64decode(state.encode()).decode()
        except Exception:
            username = None

    # Fallback to session if state decode failed
    if not username:
        username = session.get("username")

    if not username:
        return redirect("/login")

    try:
        redirect_uri = get_redirect_uri()
        print(f"[gfit callback] username={username} redirect_uri={redirect_uri} code_len={len(code)}")
        token_dict = gfit.exchange_code(code, redirect_uri)
        if token_dict:
            save_user_token(username, token_dict)
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            return redirect("/googlefit-setup?success=1")
        return redirect("/googlefit-setup?error=exchange_failed")
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = str(e).replace("\n","").replace("\r","").replace(" ","_")[:100]
        return redirect(f"/googlefit-setup?error={err}")

@app.route("/api/googlefit/disconnect", methods=["POST"])
@login_required
def gf_disconnect():
    clear_user_token(session.get("username",""))
    return jsonify({"success": True})

@app.route("/api/googlefit/sync", methods=["POST"])
@login_required
def gf_sync():
    if not GFIT_OK:
        return jsonify({"success": False, "error": "Google Fit libraries not installed"}), 400
    username = session.get("username")
    token = get_user_token(username)
    if not token:
        return jsonify({"success": False, "error": "Google Fit not connected. Please connect first."}), 400

    data, updated_token = gfit.fetch_vitals(token)
    if "error" in data:
        return jsonify({"success": False, "error": data["error"]}), 400

    # Save refreshed token back
    if updated_token:
        save_user_token(username, updated_token)

    # Store vitals in DB
    conn = get_db(); ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (ts,data.get("oxygen"),data.get("heart_rate"),data.get("bp_sys"),data.get("bp_dia"),data.get("temperature"),"📱 Google Fit"))
        cur.close()
    else:
        conn.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (?,?,?,?,?,?,?)",
                     (ts,data.get("oxygen"),data.get("heart_rate"),data.get("bp_sys"),data.get("bp_dia"),data.get("temperature"),"📱 Google Fit"))
    conn.commit(); conn.close()
    return jsonify({"success": True, "data": data})

# ── Food Photo Analysis ────────────────────────────────────────────────────────
@app.route("/api/analyze-food-photo", methods=["POST"])
@login_required
def analyze_food_photo():
    if "photo" not in request.files:
        return jsonify({"error": "No photo uploaded"}), 400
    photo = request.files["photo"]
    ext = photo.filename.rsplit(".", 1)[-1].lower() if photo.filename else "jpg"
    if ext not in ("jpg","jpeg","png","webp"):
        return jsonify({"error": "Please upload a JPG or PNG image"}), 400
    client = get_ai_client()
    if not client:
        return jsonify({"error": "AI not configured. Check ANTHROPIC_API_KEY in Railway Variables."}), 400
    import base64, io
    raw = photo.read()
    # Resize image server-side to max 800px to reduce payload size and AI processing time
    try:
        from PIL import Image as PILImage
        img = PILImage.open(io.BytesIO(raw))
        img = img.convert("RGB")
        max_px = 800
        if max(img.width, img.height) > max_px:
            scale = max_px / max(img.width, img.height)
            img = img.resize((int(img.width*scale), int(img.height*scale)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        raw = buf.getvalue()
        ext = "jpeg"
    except Exception:
        pass  # PIL not available or error — use original
    img_data = base64.b64encode(raw).decode()
    media_type = "image/jpeg" if ext in ("jpg","jpeg") else f"image/{ext}"
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=400,
            messages=[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":media_type,"data":img_data}},
                {"type":"text","text":"""You are a nutrition expert. Analyze this food photo and identify all food items visible.
Respond ONLY with a valid JSON object (no markdown):
{"food_items": "2 idlis + sambar + coconut chutney", "calories": 320, "protein_g": 8, "carbs_g": 55, "fat_g": 6, "fiber_g": 4, "breakdown": "Idli 200kcal + Sambar 80kcal + Chutney 40kcal", "health_note": "Good source of fermented carbs, low fat breakfast", "meal_type": "Breakfast"}
Be accurate for Indian foods. Estimate typical restaurant/home serving sizes."""}
            ]}]
        )
        import re
        text = msg.content[0].text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return jsonify(json.loads(match.group()))
        return jsonify({"error": "Could not analyze photo"})
    except Exception as e:
        err = str(e)
        if "Connection" in err or "Network" in err or "timeout" in err.lower():
            return jsonify({"error": "AI service temporarily unreachable. Please try again in a moment."}), 400
        return jsonify({"error": err}), 400

# ── Email Diet Report ──────────────────────────────────────────────────────────
@app.route("/api/send-diet-email", methods=["POST"])
@login_required
def send_diet_email():
    import httpx as http_requests

    d = request.json or {}
    username = session.get("username","admin")

    # Get user's email from their profile
    conn2 = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur2 = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur2.execute("SELECT email, full_name FROM user_profile WHERE username=%s",(username,))
        prow = cur2.fetchone(); cur2.close()
    else:
        r2 = conn2.execute("SELECT email, full_name FROM user_profile WHERE username=?",(username,)).fetchone()
        prow = dict(r2) if r2 else None
    conn2.close()

    # Use email from profile, fallback to request body
    to_email = (prow.get("email","") if prow else "") or d.get("email","").strip()
    if not to_email or "@" not in to_email:
        return jsonify({"error": "No email address found. Please add your email in My Profile → Save Profile first."}), 400

    resend_api_key = os.environ.get("RESEND_API_KEY","")
    smtp_user = os.environ.get("SMTP_USER","")  # used as "from" address

    if not resend_api_key:
        return jsonify({"error": "Email not configured. Add RESEND_API_KEY to Railway Variables."}), 400
    if not smtp_user:
        return jsonify({"error": "Sender email not configured. Add SMTP_USER to Railway Variables."}), 400

    # Get today's diet data
    username = session.get("username","admin")
    today = datetime.date.today().isoformat()
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM diet_log WHERE date=%s AND username=%s ORDER BY id",(today,username))
        entries = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM user_profile WHERE username=%s",(username,))
        profile = cur.fetchone(); cur.close()
    else:
        entries = [dict(r) for r in conn.execute("SELECT * FROM diet_log WHERE date=? AND username=? ORDER BY id",(today,username)).fetchall()]
        r = conn.execute("SELECT * FROM user_profile WHERE username=?",(username,)).fetchone()
        profile = dict(r) if r else None
    conn.close()

    total_cal = sum(e['calories'] or 0 for e in entries)
    total_water = sum(e['water_ml'] or 0 for e in entries)

    # Generate AI report
    client = get_ai_client()
    ai_report = ""
    if client and entries:
        food_summary = "\n".join([f"- {e['meal_type']}: {e['food_items']} ({e['calories']} kcal)" for e in entries])
        profile_ctx = f"Patient: {profile.get('full_name','') if profile else ''}, {profile.get('age','?') if profile else '?'}y, Goals: {profile.get('health_goals','') if profile else ''}" if profile else ""
        try:
            msg = client.messages.create(model="claude-opus-4-5", max_tokens=600,
                messages=[{"role":"user","content":f"""Write a friendly, encouraging daily diet summary email for a health app user.

{profile_ctx}
Date: {today}
Total calories: {total_cal} kcal
Water: {total_water} ml
Meals:
{food_summary}

Include:
1. What they did well today (2 sentences)
2. One thing to improve tomorrow (1 sentence)  
3. Tomorrow's suggested breakfast, lunch, dinner (Indian foods)
4. One motivational closing line

Keep it warm, personal, concise. No markdown headers."""}])
            ai_report = msg.content[0].text
        except Exception:
            ai_report = "Keep up the great work with your health journey!"

    name = profile.get("full_name", username) if profile else username
    html = f"""
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#f8f8f8;padding:20px">
<div style="background:#060b14;border-radius:12px;padding:24px;color:#e8edf5">
  <h2 style="color:#00d4c8;margin:0 0 4px;font-size:22px">HealthMate Daily Report</h2>
  <p style="color:#8a9ab8;margin:0 0 20px;font-size:13px">{today} · {name}</p>

  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">
    <div style="background:#141f35;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#00d4c8">{total_cal}</div>
      <div style="font-size:12px;color:#8a9ab8">calories</div>
    </div>
    <div style="background:#141f35;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#0099ff">{total_water}</div>
      <div style="font-size:12px;color:#8a9ab8">ml water</div>
    </div>
    <div style="background:#141f35;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#00e5a0">{len(entries)}</div>
      <div style="font-size:12px;color:#8a9ab8">meals logged</div>
    </div>
  </div>

  <div style="background:#141f35;border-radius:8px;padding:16px;margin-bottom:16px">
    <h3 style="color:#00d4c8;font-size:14px;margin:0 0 10px">Today's Meals</h3>
    {''.join([f'<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px"><span style="color:#8a9ab8">{e["meal_type"]}:</span> {e["food_items"]} <span style="color:#4a5a78">({e["calories"]} kcal)</span></div>' for e in entries]) or '<p style="color:#4a5a78;font-size:13px">No meals logged</p>'}
  </div>

  <div style="background:#141f35;border-radius:8px;padding:16px;font-size:14px;line-height:1.7;color:#8a9ab8">
    {ai_report.replace(chr(10), '<br>')}
  </div>

  <p style="color:#4a5a78;font-size:11px;margin-top:20px;text-align:center">HealthMate · Your personal AI health companion</p>
</div>
</body></html>"""

    try:
        transport = http_requests.HTTPTransport(retries=2)
        with http_requests.Client(transport=transport, timeout=30.0) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": smtp_user,
                    "to": [to_email],
                    "subject": f"HealthMate Daily Report — {today}",
                    "html": html
                }
            )
        data = response.json()
        if response.status_code not in (200, 201):
            return jsonify({"error": f"Email failed: {data.get('message', data)}"}), 400
        return jsonify({"status":"ok","message":f"Diet report sent to {to_email}"})
    except http_requests.ConnectError:
        return jsonify({"error": "Cannot reach Resend email service. Check Railway outbound network or upgrade plan."}), 400
    except Exception as e:
        return jsonify({"error": f"Email failed: {str(e)}"}), 400

# ── Init on startup ───────────────────────────────────────────────────────────
with app.app_context():
    try:
        init_db()
        seed_admin()
        print(f"[OK] DB initialized ({'PostgreSQL' if USE_POSTGRES and PSYCOPG2_OK else 'SQLite /tmp'})")
    except Exception as e:
        print(f"[ERROR] DB init failed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

# ── Strava Integration ────────────────────────────────────────────────────────
def get_strava_redirect_uri():
    base = os.environ.get("APP_BASE_URL","").strip().rstrip("/")
    if not base:
        base = "https://" + request.host
    return f"{base}/strava/callback"

def get_strava_token(username):
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT strava_token FROM users WHERE username=%s",(username,))
        row = cur.fetchone(); cur.close()
    else:
        row = conn.execute("SELECT strava_token FROM users WHERE username=?",(username,)).fetchone()
        row = dict(row) if row else None
    conn.close()
    if row and row.get("strava_token"):
        try: return json.loads(row["strava_token"])
        except: return None
    return None

def save_strava_token(username, token_dict):
    conn = get_db()
    tj = json.dumps(token_dict)
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("UPDATE users SET strava_token=%s WHERE username=%s",(tj,username)); cur.close()
    else:
        conn.execute("UPDATE users SET strava_token=? WHERE username=?",(tj,username))
    conn.commit(); conn.close()

@app.route("/strava/connect")
@login_required
def strava_connect():
    import base64, urllib.parse
    client_id = os.environ.get("STRAVA_CLIENT_ID","")
    if not client_id:
        return redirect("/googlefit-setup?error=strava_not_configured")
    state = base64.urlsafe_b64encode(session["username"].encode()).decode()
    params = {
        "client_id": client_id,
        "redirect_uri": get_strava_redirect_uri(),
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read,profile:read_all",
        "state": state
    }
    return redirect("https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode(params))

@app.route("/strava/callback")
def strava_callback():
    import base64, urllib.request, urllib.parse, urllib.error
    code  = request.args.get("code","")
    error = request.args.get("error","")
    state = request.args.get("state","")
    if error or not code:
        return redirect("/googlefit-setup?strava_error=access_denied")
    username = None
    if state:
        try: username = base64.urlsafe_b64decode(state.encode()).decode()
        except: pass
    if not username: username = session.get("username")
    if not username: return redirect("/login")
    try:
        data = urllib.parse.urlencode({
            "client_id": os.environ.get("STRAVA_CLIENT_ID",""),
            "client_secret": os.environ.get("STRAVA_CLIENT_SECRET",""),
            "code": code,
            "grant_type": "authorization_code"
        }).encode()
        req = urllib.request.Request("https://www.strava.com/oauth/token", data=data,
            headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read().decode())
        save_strava_token(username, {
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": token_data["expires_at"],
            "athlete": token_data.get("athlete",{})
        })
        session.permanent = True
        session["logged_in"] = True
        session["username"] = username
        return redirect("/googlefit-setup?strava_success=1")
    except Exception as e:
        return redirect(f"/googlefit-setup?strava_error={str(e)[:60]}")

@app.route("/api/strava/status")
@login_required
def strava_status():
    token = get_strava_token(session.get("username"))
    configured = bool(os.environ.get("STRAVA_CLIENT_ID"))
    athlete = token.get("athlete",{}) if token else {}
    return jsonify({
        "configured": configured,
        "connected": bool(token),
        "athlete_name": athlete.get("firstname","") + " " + athlete.get("lastname",""),
    })

@app.route("/api/strava/sync", methods=["POST"])
@login_required
def strava_sync():
    import urllib.request, urllib.parse, time
    username = session.get("username")
    token = get_strava_token(username)
    if not token: return jsonify({"success":False,"error":"Strava not connected"}),400

    # Refresh token if expired
    if time.time() > token.get("expires_at",0) - 300:
        try:
            data = urllib.parse.urlencode({
                "client_id": os.environ.get("STRAVA_CLIENT_ID",""),
                "client_secret": os.environ.get("STRAVA_CLIENT_SECRET",""),
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token"
            }).encode()
            req = urllib.request.Request("https://www.strava.com/oauth/token", data=data,
                headers={"Content-Type":"application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req) as resp:
                new_token = json.loads(resp.read().decode())
            token["access_token"] = new_token["access_token"]
            token["refresh_token"] = new_token["refresh_token"]
            token["expires_at"] = new_token["expires_at"]
            save_strava_token(username, token)
        except Exception as e:
            return jsonify({"success":False,"error":f"Token refresh failed: {str(e)}"}),400

    # Fetch recent activities
    try:
        req = urllib.request.Request(
            "https://www.strava.com/api/v3/athlete/activities?per_page=5",
            headers={"Authorization": f"Bearer {token['access_token']}"}
        )
        with urllib.request.urlopen(req) as resp:
            activities = json.loads(resp.read().decode())

        # Save activities as diet/exercise log entries
        conn = get_db(); saved = 0
        for act in activities:
            date = act.get("start_date_local","")[:10]
            name = act.get("name","Activity")
            dist = round(act.get("distance",0)/1000, 2)
            cal  = round(act.get("calories",0))
            atype = act.get("type","Run")
            note = f"🏃 Strava: {name} | {dist}km | {atype}"
            if cal > 0:
                if USE_POSTGRES and PSYCOPG2_OK:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes) VALUES (%s,%s,%s,%s,%s,%s)",
                        (date,"Exercise",name,-cal,0,note)); cur.close()
                else:
                    conn.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes) VALUES (?,?,?,?,?,?)",
                        (date,"Exercise",name,-cal,0,note))
                saved += 1
        conn.commit(); conn.close()
        return jsonify({"success":True,"activities":len(activities),"saved":saved,
            "summary":[{"name":a.get("name"),"type":a.get("type"),"distance":round(a.get("distance",0)/1000,2),"calories":round(a.get("calories",0))} for a in activities]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),400

@app.route("/api/strava/disconnect", methods=["POST"])
@login_required
def strava_disconnect():
    conn = get_db()
    u = session.get("username")
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("UPDATE users SET strava_token=NULL WHERE username=%s",(u,)); cur.close()
    else:
        conn.execute("UPDATE users SET strava_token=NULL WHERE username=?",(u,))
    conn.commit(); conn.close()
    return jsonify({"success":True})
