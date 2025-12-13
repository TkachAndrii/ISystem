import time
import random
import os
import requests
import logging

# Налаштування
AUTH_URL = os.getenv("AUTH_URL", "http://auth_service:5000")
CRM_URL = os.getenv("CRM_URL", "http://crm_service:5001")
TARGET_OPS = int(os.getenv("TARGET_OPS_PER_SEC", 5))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] STRESSER: %(message)s")
logger = logging.getLogger(__name__)

class UserBot:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.token = None

    def register(self):
        try:
            self.session.post(f"{AUTH_URL}/register", data={"username": self.username, "password": self.password})
        except Exception:
            pass # Ігноруємо, якщо вже існує

    def login(self):
        try:
            resp = self.session.post(f"{AUTH_URL}/login", data={"username": self.username, "password": self.password})
            # Auth сервіс встановлює куку 'auth_token' або редіректить з токеном.
            # Нам достатньо того, що requests.Session зберіг куки.
            if resp.status_code == 200:
                logger.info(f"User {self.username} logged in.")
                return True
        except Exception as e:
            logger.error(f"Login failed: {e}")
        return False

    def create_order(self):
        items = ["Laptop", "Mouse", "Keyboard", "Monitor", "USB Drive"]
        try:
            data = {
                "item": random.choice(items),
                "price": random.randint(10, 2000)
            }
            resp = self.session.post(f"{CRM_URL}/api/orders", json=data, timeout=1)
            return resp.status_code == 201
        except Exception as e:
            logger.error(f"Error creating order: {e}")
            return False

def main():
    logger.info("Waiting for services to start...")
    time.sleep(10) # Даємо час Auth та Mongo запуститися

    bot = UserBot("load_tester", "12345")
    bot.register()
    
    if not bot.login():
        logger.error("Could not login. Exiting.")
        return

    logger.info("Starting load generation...")
    
    while True:
        start_time = time.time()
        
        # Виконуємо дію
        bot.create_order()
        
        # Контроль швидкості (ops/sec)
        elapsed = time.time() - start_time
        sleep_time = (1.0 / TARGET_OPS) - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

if __name__ == "__main__":
    main()