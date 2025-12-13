FROM python:3.9-slim

WORKDIR /app

# Копіюємо залежності та встановлюємо їх
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо весь код
COPY . .

# Запуск (команда буде перезаписана в docker-compose для деяких сервісів, але це база)
CMD ["python", "app.py"]