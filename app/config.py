import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _build_database_url() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "3306")
    user = quote_plus(os.getenv("DB_USER", "spoolbeacon"))
    password = quote_plus(os.getenv("DB_PASSWORD", ""))
    name = os.getenv("DB_NAME", "spoolbeacon")
    return f"mysql+asyncmy://{user}:{password}@{host}:{port}/{name}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    DATABASE_URL = _build_database_url()
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    QUART_AUTH_COOKIE_SECURE = os.getenv("QUART_AUTH_COOKIE_SECURE", "false").lower() == "true"
    QUART_AUTH_COOKIE_SAMESITE = "Lax"
