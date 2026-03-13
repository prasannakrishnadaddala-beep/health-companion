import os, json, datetime, hashlib, secrets
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.utils import secure_filename
import anthropic
import google_fit as gfit

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg'}
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)
DB_PATH = "health_data.db"

if USE_POSTGRES:
    import psycopg2, psycopg2.extras
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    import sqlite3

def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql):
    return sql.replace("?","%s") if USE_POSTGRES else sql

def rows(cur):
    if USE_POSTGRES:
        return [dict(r) for r in cur.fetchall()]
    return [dict(r) for r in cur.fetchall()]

def row1(cur):
    r = cur.fetchone()
    return dict(r) if r else None

def init_db():
    conn = get_db()
    tables = """
    CREATE TABLE IF NOT EXISTS users (id {pk}, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT);
    CREATE TABLE IF NOT EXISTS vitals (id {pk}, timestamp TEXT NOT NULL, oxygen REAL, heart_rate INTEGER, bp_sys INTEGER, bp_dia INTEGER, temperature REAL, notes TEXT);
    CREATE TABLE IF NOT EXISTS medications (id {pk}, name TEXT NOT NULL, dose TEXT, frequency TEXT, times TEXT, color TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS med_logs (id {pk}, med_id INTEGER, date TEXT, dose_index INTEGER, taken INTEGER DEFAULT 0, taken_at TEXT);
    CREATE TABLE IF NOT EXISTS health_records (id {pk}, filename TEXT, original_name TEXT, uploaded_at TEXT, analysis TEXT, file_type TEXT);
    CREATE TABLE IF NOT EXISTS cycle_log (id {pk}, start_date TEXT, end_date TEXT, cycle_length INTEGER, flow_intensity TEXT, symptoms TEXT, notes TEXT);
    CREATE TABLE IF NOT EXISTS diet_log (id {pk}, date TEXT, meal_type TEXT, food_items TEXT, calories INTEGER, water_ml INTEGER, notes TEXT);
    CREATE TABLE IF NOT EXISTS appointments (id {pk}, doctor_name TEXT, specialty TEXT, date TEXT, time TEXT, location TEXT, reason TEXT, notes TEXT, reminder_sent INTEGER DEFAULT 0, completed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS chat_history (id {pk}, role TEXT, content TEXT, timestamp TEXT);
    """.replace("{pk}", "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT")
    if USE_POSTGRES:
        cur = conn.cursor()
        for stmt in [s.strip() for s in tables.strip().split(";") if s.strip()]:
            cur.execute(stmt)
        conn.commit(); cur.close()
    else:
        conn.executescript(tables)
        conn.commit()
    conn.close()

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

def seed_admin():
    conn = get_db()
    uname = os.environ.get("ADMIN_USERNAME","admin")
    pwd   = os.environ.get("ADMIN_PASSWORD","healthmate123")
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users"); cnt = cur.fetchone()[0]
        if cnt == 0:
            cur.execute("INSERT INTO users (username,password_hash,created_at) VALUES (%s,%s,%s)",
                        (uname, hash_pw(pwd), datetime.datetime.now().isoformat()))
        conn.commit(); cur.close()
    else:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,?)",
                         (uname, hash_pw(pwd), datetime.datetime.now().isoformat()))
            conn.commit()
    conn.close()

def login_required(f):
    from functools import wraps
    @wraps(f)
    def dec(*a,**k):
        if not session.get("logged_in"):
            return (jsonify({"error":"Unauthorized"}),401) if request.path.startswith("/api/") else redirect(url_for("login"))
        return f(*a,**k)
    return dec

def get_client():
    key = os.environ.get("ANTHROPIC_API_KEY","")
    return anthropic.Anthropic(api_key=key) if key else None

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE username=%s AND password_hash=%s",(u,hash_pw(p)))
            user = cur.fetchone(); cur.close()
        else:
            user = conn.execute("SELECT * FROM users WHERE username=? AND password_hash=?",(u,hash_pw(p))).fetchone()
        conn.close()
        if user:
            session["logged_in"] = True; session["username"] = u
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def index(): return render_template("index.html")

@app.route("/googlefit-setup")
@login_required
def googlefit_setup_page(): return render_template("googlefit_setup.html")

@app.route("/health")
def health(): return jsonify({"status":"ok"})

@app.route("/api/vitals", methods=["GET"])
@login_required
def get_vitals():
    conn = get_db()
    if USE_POSTGRES:
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
    sql = q("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (?,?,?,?,?,?,?)")
    params = (ts,d.get("oxygen"),d.get("heart_rate"),d.get("bp_sys"),d.get("bp_dia"),d.get("temperature"),d.get("notes",""))
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,params); cur.close()
    else:
        conn.execute(sql,params)
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/medications", methods=["GET"])
@login_required
def get_medications():
    today = datetime.date.today().isoformat()
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM medications WHERE active=1")
        meds = [dict(r) for r in cur.fetchall()]
        result = []
        for m in meds:
            times = json.loads(m["times"])
            cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur2.execute("SELECT * FROM med_logs WHERE med_id=%s AND date=%s",(m["id"],today))
            logs = [dict(r) for r in cur2.fetchall()]; cur2.close()
            tm = {l["dose_index"]:bool(l["taken"]) for l in logs}
            result.append({**m,"times":times,"taken":[tm.get(i,False) for i in range(len(times))]})
        cur.close()
    else:
        meds = [dict(r) for r in conn.execute("SELECT * FROM medications WHERE active=1").fetchall()]
        result = []
        for m in meds:
            times = json.loads(m["times"])
            logs = [dict(r) for r in conn.execute("SELECT * FROM med_logs WHERE med_id=? AND date=?",(m["id"],today)).fetchall()]
            tm = {l["dose_index"]:bool(l["taken"]) for l in logs}
            result.append({**m,"times":times,"taken":[tm.get(i,False) for i in range(len(times))]})
    conn.close(); return jsonify(result)

@app.route("/api/medications", methods=["POST"])
@login_required
def add_medication():
    d = request.json; conn = get_db()
    sql = q("INSERT INTO medications (name,dose,frequency,times,color) VALUES (?,?,?,?,?)")
    p = (d["name"],d.get("dose",""),d.get("frequency",""),json.dumps(d.get("times",["09:00"])),d.get("color","#378ADD"))
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/medications/<int:mid>", methods=["DELETE"])
@login_required
def delete_medication(mid):
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute("UPDATE medications SET active=0 WHERE id=%s",(mid,)); cur.close()
    else:
        conn.execute("UPDATE medications SET active=0 WHERE id=?",(mid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/medications/log", methods=["POST"])
@login_required
def log_medication():
    d = request.json; today = datetime.date.today().isoformat(); conn = get_db()
    taken_at = datetime.datetime.now().isoformat() if d["taken"] else None
    if USE_POSTGRES:
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

@app.route("/api/records", methods=["GET"])
@login_required
def get_records():
    conn = get_db()
    if USE_POSTGRES:
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
    client = get_client()
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
            analysis = f"Analysis unavailable: {str(e)}"
    conn = get_db()
    sql = q("INSERT INTO health_records (filename,original_name,uploaded_at,analysis,file_type) VALUES (?,?,?,?,?)")
    p = (filename,file.filename,datetime.datetime.now().isoformat(),analysis,ext)
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close()
    return jsonify({"status":"ok","analysis":analysis})

@app.route("/api/records/<int:rid>", methods=["DELETE"])
@login_required
def delete_record(rid):
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute("DELETE FROM health_records WHERE id=%s",(rid,)); cur.close()
    else:
        conn.execute("DELETE FROM health_records WHERE id=?",(rid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/cycle", methods=["GET"])
@login_required
def get_cycle():
    conn = get_db()
    if USE_POSTGRES:
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
    sql = q("INSERT INTO cycle_log (start_date,end_date,cycle_length,flow_intensity,symptoms,notes) VALUES (?,?,?,?,?,?)")
    p = (d.get("start_date"),d.get("end_date"),d.get("cycle_length"),d.get("flow_intensity"),json.dumps(d.get("symptoms",[])),d.get("notes",""))
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/diet", methods=["GET"])
@login_required
def get_diet():
    date = request.args.get("date",datetime.date.today().isoformat()); conn = get_db()
    if USE_POSTGRES:
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
    sql = q("INSERT INTO diet_log (date,meal_type,food_items,calories,water_ml,notes) VALUES (?,?,?,?,?,?)")
    p = (d.get("date",datetime.date.today().isoformat()),d.get("meal_type"),d.get("food_items"),d.get("calories",0),d.get("water_ml",0),d.get("notes",""))
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/diet/<int:eid>", methods=["DELETE"])
@login_required
def delete_diet(eid):
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute("DELETE FROM diet_log WHERE id=%s",(eid,)); cur.close()
    else:
        conn.execute("DELETE FROM diet_log WHERE id=?",(eid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/appointments", methods=["GET"])
@login_required
def get_appointments():
    conn = get_db()
    if USE_POSTGRES:
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
    sql = q("INSERT INTO appointments (doctor_name,specialty,date,time,location,reason,notes) VALUES (?,?,?,?,?,?,?)")
    p = (d["doctor_name"],d.get("specialty",""),d["date"],d.get("time",""),d.get("location",""),d.get("reason",""),d.get("notes",""))
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/appointments/<int:aid>", methods=["PATCH"])
@login_required
def update_appointment(aid):
    d = request.json; conn = get_db()
    if "completed" in d:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute("UPDATE appointments SET completed=%s WHERE id=%s",(d["completed"],aid)); cur.close()
        else:
            conn.execute("UPDATE appointments SET completed=? WHERE id=?",(d["completed"],aid))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/appointments/<int:aid>", methods=["DELETE"])
@login_required
def delete_appointment(aid):
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute("DELETE FROM appointments WHERE id=%s",(aid,)); cur.close()
    else:
        conn.execute("DELETE FROM appointments WHERE id=?",(aid,))
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    d = request.json; user_msg = d.get("message","")
    if not user_msg: return jsonify({"error":"Empty"}),400
    client = get_client()
    if not client: return jsonify({"reply":"⚠️ ANTHROPIC_API_KEY not set in environment variables."})
    today = datetime.date.today().isoformat(); conn = get_db()
    def qry(sql,p=()): return conn.execute(sql,p) if not USE_POSTGRES else None
    if USE_POSTGRES:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 1"); lv = cur.fetchone()
        cur.execute("SELECT name,dose FROM medications WHERE active=1"); meds = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=%s",(today,)); mlogs = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT original_name,analysis FROM health_records ORDER BY uploaded_at DESC LIMIT 2"); recs = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM appointments WHERE date>=%s AND completed=0 ORDER BY date,time LIMIT 1",(today,)); appt = cur.fetchone()
        cur.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=%s",(today,)); diet = cur.fetchone()
        cur.execute("SELECT role,content FROM chat_history ORDER BY id DESC LIMIT 10"); hist = list(reversed([dict(r) for r in cur.fetchall()]))
        cur.close()
    else:
        lv = conn.execute("SELECT * FROM vitals ORDER BY timestamp DESC LIMIT 1").fetchone()
        lv = dict(lv) if lv else None
        meds = [dict(r) for r in conn.execute("SELECT name,dose FROM medications WHERE active=1").fetchall()]
        mlogs = [dict(r) for r in conn.execute("SELECT m.name,ml.taken FROM med_logs ml JOIN medications m ON ml.med_id=m.id WHERE ml.date=?",(today,)).fetchall()]
        recs = [dict(r) for r in conn.execute("SELECT original_name,analysis FROM health_records ORDER BY uploaded_at DESC LIMIT 2").fetchall()]
        appt = conn.execute("SELECT * FROM appointments WHERE date>=? AND completed=0 ORDER BY date,time LIMIT 1",(today,)).fetchone()
        appt = dict(appt) if appt else None
        diet = conn.execute("SELECT SUM(calories) as cal,SUM(water_ml) as water FROM diet_log WHERE date=?",(today,)).fetchone()
        diet = dict(diet) if diet else None
        hist = list(reversed([dict(r) for r in conn.execute("SELECT role,content FROM chat_history ORDER BY id DESC LIMIT 10").fetchall()]))
    conn.close()
    taken = [r["name"] for r in mlogs if r["taken"]]
    due = [m["name"] for m in meds if m["name"] not in taken]
    vs = f"O2:{lv['oxygen']}% HR:{lv['heart_rate']}bpm BP:{lv['bp_sys']}/{lv['bp_dia']} Temp:{lv['temperature']}°F" if lv else "No readings"
    sys_p = f"You are a compassionate AI health companion. Be warm, concise (3-5 sentences). Recommend doctor for medical decisions.\nToday:{today}\nVitals:{vs}\nMeds due:{','.join(due) or 'All taken'}\nAppointment:{appt['doctor_name']+' '+appt['date'] if appt else 'None'}\nDiet:{str(diet['cal'] or 0)+' kcal' if diet else 'No log'}\nRecords:{'; '.join([r['original_name'] for r in recs]) if recs else 'None'}"
    messages = [{"role":h["role"] if h["role"] in ("user","assistant") else "user","content":h["content"]} for h in hist]
    messages.append({"role":"user","content":user_msg})
    try:
        resp = client.messages.create(model="claude-opus-4-5",max_tokens=800,system=sys_p,messages=messages)
        reply = resp.content[0].text
    except Exception as e:
        reply = f"Error: {str(e)}"
    conn = get_db()
    ts = datetime.datetime.now().isoformat()
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (%s,%s,%s)",("user",user_msg,ts))
        cur.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (%s,%s,%s)",("assistant",reply,ts))
        cur.close()
    else:
        conn.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (?,?,?)",("user",user_msg,ts))
        conn.execute("INSERT INTO chat_history (role,content,timestamp) VALUES (?,?,?)",("assistant",reply,ts))
    conn.commit(); conn.close()
    return jsonify({"reply":reply})

@app.route("/api/chat/history")
@login_required
def get_chat_history():
    conn = get_db()
    if USE_POSTGRES:
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
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute("DELETE FROM chat_history"); cur.close()
    else:
        conn.execute("DELETE FROM chat_history")
    conn.commit(); conn.close(); return jsonify({"status":"ok"})

@app.route("/api/googlefit/status")
@login_required
def gf_status(): return jsonify({"libs_available":gfit.GOOGLE_LIBS_AVAILABLE,"configured":gfit.is_configured(),"authenticated":gfit.is_authenticated()})

@app.route("/api/googlefit/connect", methods=["POST"])
@login_required
def gf_connect():
    ok,msg = gfit.authenticate(); return jsonify({"success":ok,"message":msg})

@app.route("/api/googlefit/disconnect", methods=["POST"])
@login_required
def gf_disconnect():
    gfit.disconnect(); return jsonify({"success":True})

@app.route("/api/googlefit/sync", methods=["POST"])
@login_required
def gf_sync():
    data = gfit.get_latest_vitals()
    if "error" in data: return jsonify({"success":False,"error":data["error"]}),400
    conn = get_db(); ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sql = q("INSERT INTO vitals (timestamp,oxygen,heart_rate,bp_sys,bp_dia,temperature,notes) VALUES (?,?,?,?,?,?,?)")
    p = (ts,data.get("oxygen"),data.get("heart_rate"),data.get("bp_sys"),data.get("bp_dia"),data.get("temperature"),"📱 Google Fit")
    if USE_POSTGRES:
        cur = conn.cursor(); cur.execute(sql,p); cur.close()
    else:
        conn.execute(sql,p)
    conn.commit(); conn.close()
    return jsonify({"success":True,"data":data})

if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db(); seed_admin()
    port = int(os.environ.get("PORT",5000))
    debug = os.environ.get("FLASK_ENV","production") == "development"
    print(f"\n{'='*50}\n  Health Companion {'DEV' if debug else 'PROD'}\n  http://127.0.0.1:{port}\n  DB: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}\n{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
