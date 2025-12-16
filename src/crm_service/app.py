from flask import Flask, render_template, request, redirect, jsonify, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pymongo import MongoClient
import requests, os, logging
from bson import ObjectId

app = Flask(__name__)

# --- Налаштування ---
AUTH_SERVICE_EXTERNAL = os.getenv("AUTH_SERVICE_URL", "http://localhost:5000")
AUTH_SERVICE_INTERNAL = os.getenv("AUTH_SERVICE_INNER", "http://auth_service:5000")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] CRM: %(message)s")
logger = logging.getLogger(__name__)

# Підключення до Mongo з обробкою помилок
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    orders_db = mongo_client["crm"]["orders"]
except Exception as e:
    logger.error(f"Mongo connection failed: {e}")
    orders_db = None


# --- Хелпер валідації ---
def validate_session(req):
    token = req.cookies.get("auth_token")
    if not token:
        return None

    try:
        # Запит до Auth Service
        resp = requests.get(
            f"{AUTH_SERVICE_INTERNAL}/api/validate",
            params={"token": token},
            timeout=2.0
        )

        # ВАЖЛИВО: Перевіряємо статус перед тим, як читати JSON
        if resp.status_code != 200:
            logger.warning(f"Auth Check Failed. Status: {resp.status_code}, Body: {resp.text[:100]}")
            return None

        data = resp.json()  # Тепер це безпечно
        if data.get("status") == "ok":
            return data

    except ValueError:
        logger.error("Auth Service returned non-JSON response!")
    except Exception as e:
        logger.error(f"Auth Service unreachable: {e}")

    return None


# --- Маршрути ---
@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/")
def home():
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    user = validate_session(request)
    if not user:
        return redirect(f"{AUTH_SERVICE_EXTERNAL}/login")

    orders = []
    if orders_db is not None:
        try:
            orders = list(orders_db.find({"username": user["name"]}))
            for o in orders: o["_id"] = str(o["_id"])
        except Exception as e:
            logger.error(f"DB Read Error: {e}")

    return render_template("dashboard.html", user=user, orders=orders)


@app.route("/api/orders", methods=["POST"])
def create_order():
    user = validate_session(request)
    if not user: return jsonify({"error": "Unauthorized"}), 403

    if orders_db is None: return jsonify({"error": "DB Error"}), 500

    data = request.json
    if not data.get("item") or not data.get("price"):
        return jsonify({"error": "Invalid data"}), 400

    order = {
        "username": user["name"],
        "item": data["item"],
        "price": data["price"]
    }
    res = orders_db.insert_one(order)
    return jsonify({"status": "created", "id": str(res.inserted_id)}), 201


@app.route("/api/orders/<order_id>", methods=["DELETE"])
def delete_order(order_id):
    user = validate_session(request)
    if not user: return jsonify({"error": "Unauthorized"}), 403

    if orders_db is not None:
        orders_db.delete_one({"_id": ObjectId(order_id), "username": user["name"]})
        return jsonify({"status": "deleted"}), 200
    return jsonify({"error": "DB Error"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)