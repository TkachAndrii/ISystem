from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, make_response
import sqlite3, os, uuid, json, time, logging
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Gauge, Histogram

app = Flask(__name__)
app.secret_key = "supersecret"

# --- Налаштування ---
DB_PATH = os.getenv("DB_PATH", "/app/data/auth.db")
db_dir = os.path.dirname(DB_PATH)
os.makedirs(db_dir, exist_ok=True)

SESSION_TTL = 30
CRM_URL = os.getenv("CRM_URL", "http://localhost:5001/dashboard")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] AUTH: %(message)s")
logger = logging.getLogger(__name__)

# --- МЕТРИКИ PROMETHEUS (Які ми повернули) ---
REQUEST_COUNT = Counter('auth_requests_total', 'Total requests', ['method', 'endpoint'])
LOGIN_SUCCESS = Counter('auth_login_success_total', 'Successful logins')
LOGIN_FAILED = Counter('auth_login_failed_total', 'Failed logins')
ACTIVE_SESSIONS = Gauge('auth_active_sessions', 'Number of active sessions')
REQUEST_LATENCY = Histogram('auth_request_latency_seconds', 'Request latency')
TOKEN_VALIDATION_LATENCY = Histogram('auth_token_validation_latency_seconds', 'Token validation latency')

# --- Middleware для підрахунку запитів ---
@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    if request.path != '/metrics':
        latency = time.time() - request.start_time
        REQUEST_LATENCY.observe(latency)
        REQUEST_COUNT.labels(method=request.method, endpoint=request.path).inc()
    return response

# --- БД Хелпери ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def update_active_sessions_gauge():
    """Оновлює лічильник активних сесій на графіку"""
    try:
        with get_db_connection() as conn:
            # Рахуємо тільки не прострочені сесії
            count = conn.execute("SELECT count(*) FROM sessions WHERE expires_at > ?", (time.time(),)).fetchone()[0]
            ACTIVE_SESSIONS.set(count)
    except Exception:
        pass

def init_db():
    try:
        with get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    role TEXT DEFAULT 'user'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            conn.commit()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"DB Init failed: {e}")

def cleanup_expired_sessions():
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            conn.commit()
        update_active_sessions_gauge()
    except Exception:
        pass

# --- Маршрути ---

@app.route('/metrics')
def metrics():
    # Оновлюємо кількість сесій перед віддачею метрик
    update_active_sessions_gauge()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        try:
            with get_db_connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)).fetchone()

            if user:
                # ВАЖЛИВО: Оновлюємо метрику успішного входу
                LOGIN_SUCCESS.inc()
                
                token = str(uuid.uuid4())
                session_data = {"name": username, "role": user["role"] if "role" in user.keys() else "user"}
                expires_at = time.time() + SESSION_TTL
                
                with get_db_connection() as conn:
                    conn.execute("INSERT OR REPLACE INTO sessions (token, data, expires_at) VALUES (?, ?, ?)", 
                                 (token, json.dumps(session_data), expires_at))
                    conn.commit()
                
                update_active_sessions_gauge()

                resp = make_response(redirect(CRM_URL))
                resp.set_cookie("auth_token", token, max_age=SESSION_TTL, httponly=True)
                return resp
            else:
                # ВАЖЛИВО: Оновлюємо метрику невдалого входу
                LOGIN_FAILED.inc()
                flash("Invalid credentials")
        except Exception as e:
            logger.error(f"Login error: {e}")
            flash("Internal error")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        try:
            with get_db_connection() as conn:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
                conn.commit()
            flash("Account created. Please log in.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.")
        except Exception as e:
            logger.error(f"Register error: {e}")
            flash("Error creating account.")
    return render_template("register.html")

@app.route("/api/validate")
def validate():
    start_time = time.time()
    token = request.args.get("token") or request.cookies.get("auth_token")
    
    if not token:
        return jsonify({"status": "error", "message": "No token"}), 401

    cleanup_expired_sessions()

    try:
        with get_db_connection() as conn:
            session = conn.execute("SELECT data, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()

        if not session:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        if session["expires_at"] < time.time():
            return jsonify({"status": "error", "message": "Expired"}), 401
        
        # ВАЖЛИВО: Записуємо час валідації для графіка Latency
        TOKEN_VALIDATION_LATENCY.observe(time.time() - start_time)

        return jsonify({"status": "ok", **json.loads(session["data"])})

    except Exception as e:
        logger.error(f"Validation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
