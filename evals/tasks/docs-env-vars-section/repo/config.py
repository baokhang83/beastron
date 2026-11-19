import os

PORT = int(os.environ.get("APP_PORT", "8080"))
DATABASE_URL = os.environ.get("APP_DB_URL", "sqlite:///shop.db")
LOG_LEVEL = os.environ.get("APP_LOG_LEVEL", "INFO")
