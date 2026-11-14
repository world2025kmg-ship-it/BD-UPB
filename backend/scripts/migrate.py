"""Applique les fichiers backend/migrations/*.sql dans l'ordre numérique sur la base Neon.

Utilise DATABASE_URL_DIRECT (connexion Neon sans pooler, recommandée pour le DDL)
si elle est définie, sinon DATABASE_URL.

Usage (depuis backend/) :  python -m scripts.migrate
Les scripts sont idempotents (IF NOT EXISTS) : ils peuvent être rejoués sans risque.
"""
from pathlib import Path

import os

import psycopg

from app import config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(
        os.getenv("DATABASE_URL_DIRECT", "").strip() or config.database_url(), connect_timeout=config.connect_timeout(), prepare_threshold=None
    ) as connection:
        for path in files:
            print(f"-> {path.name}")
            connection.execute(path.read_text(encoding="utf-8"))
        connection.commit()
    print(f"{len(files)} migration(s) appliquée(s).")


if __name__ == "__main__":
    main()
