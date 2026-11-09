"""Configuration par variables d'environnement (fichier .env chargé au démarrage)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

load_dotenv(BACKEND_DIR / ".env")

CSV_MAX_BYTES = 5 * 1024 * 1024
CSV_MAX_ROW_NUMBER = 5001  # ligne 1 = en-tête, donc 5 000 lignes de données


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Variable d'environnement DATABASE_URL non définie.")
    return url


def connect_timeout() -> int:
    return int(os.getenv("UPB_DB_CONNECT_TIMEOUT", "10"))


def default_per_page() -> int:
    return int(os.getenv("UPB_DEFAULT_PER_PAGE", "20"))


def max_per_page() -> int:
    return int(os.getenv("UPB_MAX_PER_PAGE", "100"))
