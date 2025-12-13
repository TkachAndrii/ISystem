from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, make_response, g
import sqlite3, os, uuid
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import json
import time
import logging

app = Flask(__name__)
app.secret_key = "supersecret"

# Налаштування шляхів до БД
DB_PATH = os.getenv("DB_PATH", "/app/data/auth.db")
db_dir = os.path.dirname(DB_PATH)
os.makedirs(db_dir, exist_ok=True)

# Конфігурація сесій
SESSION_TTL = 30  # час життя сесії в секундах
CRM_URL = os.getenv("CRM_URL", "http://localhost:5001/dashboard")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# --- Робота з БД ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        # Таблиця користувачів
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user'
        )
        """)
        # Таблиця сесій (заміна Redis)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """)
        conn.commit()

def cleanup_expired_sessions():
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            conn.commit()
    except Exception as e:
        logger.error(f"Error cleaning up sessions: {e}")

# --- Маршрути ---

@app.route('/metrics')
def metrics():
    # Повертає стандартні метрики (GC, потоки тощо), без кастомних бізнес-метрик
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        logger.info("Login attempt for user: %s", username)

        try:
            with get_db_connection() as conn:
                cur = conn.execute(
                    "SELECT * FROM users WHERE username=? AND password=?", (username, password)
                )
                user = cur.fetchone()

            if user:
                token = str(uuid.uuid4())
                session_data = {
                    "name": username, 
                    "role": user["role"] if "role" in user.keys() else "user"
                }
                expires_at = time.time() + SESSION_TTL
                
                # Зберігаємо сесію в SQLite замість Redis
                with get_db_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO sessions (token, data, expires_at) VALUES (?, ?, ?)",
                        (token, json.dumps(session_data), expires_at)
                    )
                    conn.commit()
                
                logger.info("User %s authenticated. Token generated.", username)
                
                # Перенаправлення на CRM
                resp = make_response(redirect(CRM_URL))
                # Встановлюємо куку, щоб CRM міг її прочитати і відправити на валідацію
                resp.set_cookie("auth_token", token, max_age=SESSION_TTL, httponly=True)
                return resp
            else:
                logger.warning("Invalid login attempt for user: %s", username)
                flash("Invalid credentials")

        except Exception as e:
            logger.error("Error during login for user %s: %s", username, str(e))
            flash("Internal error, please try again.")

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
            flash("Account created. You can log in now.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:  
            flash("Username already exists.")
    return render_template("register.html")
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)