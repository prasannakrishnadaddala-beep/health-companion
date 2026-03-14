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
            cur.execute(s)
        conn.commit(); cur.close()
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
    ]
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
        import anthropic
        return anthropic.Anthropic(api_key=key)
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
        cur.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 50")
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 50").fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/vitals", methods=["POST"])
@login_required
def add_vital():
    d = request.json; ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (ts,d.get("oxygen"),d.get("heart_rate"),d.get("bp_sys"),d.get("bp_dia"),d.get("temperature"),d.get("notes","")))
        cur.close()
    else:
        conn.execute("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (?,?,?,?,?,?,?)",
                     (ts,d.get("oxygen"),d.get("heart_rate"),d.get("bp_sys"),d.get("bp_dia"),d.get("temperature"),d.get("notes","")))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Medications ───────────────────────────────────────────────────────────────
@app.route("/api/medications", methods=["GET"])
@login_required
def get_medications():
    today = datetime.date.today().isoformat()
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM medications WHERE active=1")
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
        meds = [dict(r) for r in conn.execute("SELECT * FROM medications WHERE active=1").fetchall()]
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
        cur.execute("INSERT INTO medications (name,dose,frequency,times,color) VALUES (%s,%s,%s,%s,%s)",
                    (d["name"],d.get("dose",""),d.get("frequency",""),json.dumps(d.get("times",["09:00"])),d.get("color","#378ADD")))
        cur.close()
    else:
        conn.execute("INSERT INTO medications (name,dose,frequency,times,color) VALUES (?,?,?,?,?)",
                     (d["name"],d.get("dose",""),d.get("frequency",""),json.dumps(d.get("times",["09:00"])),d.get("color","#378ADD")))
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
        cur.execute("SELECT id,original_name,uploaded_at,analysis,file_type FROM health_records ORDER BY uploaded_at DESC")
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT id,original_name,uploaded_at,analysis,file_type FROM health_records ORDER BY uploaded_at DESC").fetchall()]
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
        cur.execute("INSERT INTO health_records (filename,original_name,uploaded_at,analysis,file_type) VALUES (%s,%s,%s,%s,%s)",
                    (filename,file.filename,datetime.datetime.now().isoformat(),analysis,ext)); cur.close()
    else:
        conn.execute("INSERT INTO health_records (filename,original_name,uploaded_at,analysis,file_type) VALUES (?,?,?,?,?)",
                     (filename,file.filename,datetime.datetime.now().isoformat(),analysis,ext))
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
        cur.execute("SELECT * FROM cycle_log ORDER BY start_date DESC LIMIT 12")
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM cycle_log ORDER BY start_date DESC LIMIT 12").fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/cycle", methods=["POST"])
@login_required
def add_cycle():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("INSERT INTO cycle_log (start_date,end_date,cycle_length,flow_intensity,symptoms,notes) VALUES (%s,%s,%s,%s,%s,%s)",
                    (d.get("start_date"),d.get("end_date"),d.get("cycle_length"),d.get("flow_intensity"),json.dumps(d.get("symptoms",[])),d.get("notes",""))); cur.close()
    else:
        conn.execute("INSERT INTO cycle_log (start_date,end_date,cycle_length,flow_intensity,symptoms,notes) VALUES (?,?,?,?,?,?)",
                     (d.get("start_date"),d.get("end_date"),d.get("cycle_length"),d.get("flow_intensity"),json.dumps(d.get("symptoms",[])),d.get("notes","")))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

# ── Diet ──────────────────────────────────────────────────────────────────────
@app.route("/api/diet", methods=["GET"])
@login_required
def get_diet():
    date = request.args.get("date", datetime.date.today().isoformat()); conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM diet_log WHERE date=%s ORDER BY id",(date,))
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM diet_log WHERE date=? ORDER BY id",(date,)).fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/diet", methods=["POST"])
@login_required
def add_diet():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes) VALUES (%s,%s,%s,%s,%s,%s)",
                    (d.get("date",datetime.date.today().isoformat()),d.get("meal_type"),d.get("food_items"),d.get("calories",0),d.get("water_ml",0),d.get("notes",""))); cur.close()
    else:
        conn.execute("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes) VALUES (?,?,?,?,?,?)",
                     (d.get("date",datetime.date.today().isoformat()),d.get("meal_type"),d.get("food_items"),d.get("calories",0),d.get("water_ml",0),d.get("notes","")))
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

# ── Appointments ──────────────────────────────────────────────────────────────
@app.route("/api/appointments", methods=["GET"])
@login_required
def get_appointments():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM appointments ORDER BY date,time")
        data = [dict(r) for r in cur.fetchall()]; cur.close()
    else:
        data = [dict(r) for r in conn.execute("SELECT * FROM appointments ORDER BY date,time").fetchall()]
    conn.close(); return jsonify(data)

@app.route("/api/appointments", methods=["POST"])
@login_required
def add_appointment():
    d = request.json; conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor()
        cur.execute("INSERT INTO appointments (doctor_name,specialty,date,time,location,reason,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (d["doctor_name"],d.get("specialty",""),d["date"],d.get("time",""),d.get("location",""),d.get("reason",""),d.get("notes",""))); cur.close()
    else:
        conn.execute("INSERT INTO appointments (doctor_name,specialty,date,time,location,reason,notes) VALUES (?,?,?,?,?,?,?)",
                     (d["doctor_name"],d.get("specialty",""),d["date"],d.get("time",""),d.get("location",""),d.get("reason",""),d.get("notes","")))
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
    try:
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 1"); lv = cur.fetchone()
            cur.execute("SELECT name,dose FROM medications WHERE active=1"); meds = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=%s",(today,)); mlogs = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM appointments WHERE date>=%s AND completed=0 ORDER BY date,time LIMIT 1",(today,)); appt = cur.fetchone()
            cur.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=%s",(today,)); diet = cur.fetchone()
            cur.execute("SELECT role,content FROM chat_history ORDER BY id DESC LIMIT 10"); hist = list(reversed([dict(r) for r in cur.fetchall()]))
            cur.close()
        else:
            lv = conn.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 1").fetchone()
            lv = dict(lv) if lv else None
            meds = [dict(r) for r in conn.execute("SELECT name,dose FROM medications WHERE active=1").fetchall()]
            mlogs = [dict(r) for r in conn.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=?",(today,)).fetchall()]
            appt = conn.execute("SELECT * FROM appointments WHERE date>=? AND completed=0 ORDER BY date,time LIMIT 1",(today,)).fetchone()
            appt = dict(appt) if appt else None
            diet = conn.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=?",(today,)).fetchone()
            diet = dict(diet) if diet else None
            hist = list(reversed([dict(r) for r in conn.execute("SELECT role,content FROM chat_history ORDER BY id DESC LIMIT 10").fetchall()]))
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
        if USE_POSTGRES and PSYCOPG2_OK:
            cur = conn.cursor()
            cur.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (%s,%s,%s)",("user",user_msg,ts))
            cur.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (%s,%s,%s)",("assistant",reply,ts))
            cur.close()
        else:
            conn.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (?,?,?)",("user",user_msg,ts))
            conn.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (?,?,?)",("assistant",reply,ts))
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
        cur.execute("SELECT role,content,timestamp FROM chat_history ORDER BY id DESC LIMIT 40")
        data = list(reversed([dict(r) for r in cur.fetchall()])); cur.close()
    else:
        data = list(reversed([dict(r) for r in conn.execute("SELECT role,content,timestamp FROM chat_history ORDER BY id DESC LIMIT 40").fetchall()]))
    conn.close(); return jsonify(data)

@app.route("/api/chat/clear", methods=["POST"])
@login_required
def clear_chat():
    conn = get_db()
    if USE_POSTGRES and PSYCOPG2_OK:
        cur = conn.cursor(); cur.execute("DELETE FROM chat_history"); cur.close()
    else:
        conn.execute("DELETE FROM chat_history")
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
