from flask import Flask, render_template, request, redirect, jsonify, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pymongo import MongoClient
import requests, os, logging
from bson import ObjectId

app = Flask(__name__)

# --- CONFIGURATION ---
# URL для перенаправлення користувача (у браузері)
AUTH_SERVICE_EXTERNAL = os.getenv("AUTH_SERVICE_URL", "http://localhost:5000")
# URL для внутрішнього зв'язку між контейнерами
AUTH_SERVICE_INTERNAL = os.getenv("AUTH_SERVICE_INNER", "http://auth_service:5000")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/")
MONGO_DB = "crm"
MONGO_COLLECTION = "orders"

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    orders_db = mongo_client[MONGO_DB][MONGO_COLLECTION]
    # Перевірка з'єднання
    mongo_client.server_info()
except Exception as e:
    print(f"CRITICAL: MongoDB connection failed: {e}")
    orders_db = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- HELPER: Validate Session ---
def validate_session(req):
    """
    Перевіряє токен користувача, звертаючись до Auth Service.
    """
    token = req.cookies.get("auth_token")
    if not token:
        return None

    try:
        # Відправляємо запит до Auth Service
        resp = requests.get(
            f"{AUTH_SERVICE_INTERNAL}/api/validate",
            params={"token": token},
            timeout=1.5
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ok":
            return data # Повертає {status: ok, name: ..., role: ...}
    except Exception as e:
        logger.error(f"Failed to validate token via Auth Service: {e}")
    
    return None

# --- ROUTES ---

@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/dashboard")
def dashboard():
    if orders_db is None:
        return "Database Connection Error", 500

    user_session = validate_session(request)
    if not user_session:
        # Якщо сесія невалідна — редірект на логін
        return redirect(f"{AUTH_SERVICE_EXTERNAL}/login")

    # Отримуємо замовлення користувача
    username = user_session.get("name")
    try:
        user_orders = list(orders_db.find({"username": username}))
        # Конвертуємо ObjectId в рядок для шаблону
        for o in user_orders:
            o["_id"] = str(o["_id"])
    except Exception as e:
        logger.error(f"DB Error: {e}")
        user_orders = []

    return render_template("dashboard.html", user=user_session, orders=user_orders)

# --- API ENDPOINTS ---

@app.route("/api/orders", methods=["POST"])
def create_order():
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    item = data.get("item")
    price = data.get("price")

    if not item or not price:
        return jsonify({"error": "Missing item or price"}), 400

    order = {
        "username": session["name"],
        "item": item,
        "price": price
    }
    
    res = orders_db.insert_one(order)
    return jsonify({"status": "created", "id": str(res.inserted_id)}), 201

@app.route("/api/orders/<order_id>", methods=["DELETE"])
def delete_order(order_id):
    session = validate_session(request)
    if not session:
        return jsonify({"error": "Unauthorized"}), 403

    res = orders_db.delete_one({"_id": ObjectId(order_id), "username": session["name"]})
    if res.deleted_count > 0:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"error": "Not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)