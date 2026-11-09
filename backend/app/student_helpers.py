"""Matricules, dates, références et encodage."""
import re
import unicodedata
from datetime import date, datetime

import psycopg


def normalize_student_name(value: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", value.strip()).encode("ascii", "ignore").decode("ascii")
    )
    letters = re.sub(r"[^A-Za-z]", "", ascii_name).upper()
    if not letters:
        raise ValueError("Nom ou prénom invalide.")
    return letters


def parse_student_date(value) -> str | None:
    """Accepte AAAA-MM-JJ, JJ/MM/AAAA, JJ-MM-AAAA ; refuse les dates futures."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat() if value <= date.today() else None
    value = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(value, pattern).date()
        except ValueError:
            continue
        if parsed <= date.today() and parsed.strftime(pattern) == value:
            return parsed.isoformat()
    return None


def student_matricule(nom: str, prenoms: str, date_naissance) -> str:
    parsed = parse_student_date(date_naissance)
    if parsed is None:
        raise ValueError("Date de naissance invalide.")
    birth = date.fromisoformat(parsed)
    return (
        normalize_student_name(nom)[:3]
        + normalize_student_name(prenoms)[:1]
        + birth.strftime("%d%m%y")
        + "0001"
    )


def lookup_reference_id(cursor: psycopg.Cursor, table: str, label) -> int | None:
    """Retourne l'id de la filière/du niveau (créé s'il n'existe pas)."""
    if label is None or not str(label).strip():
        return None
    if table not in {"filiere", "niveau"}:
        raise ValueError("Référence invalide.")
    label = str(label).strip()
    cursor.execute(
        f"SELECT id FROM {table} WHERE LOWER(libelle) = LOWER(%s) LIMIT 1", (label,)
    )
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute(f"INSERT INTO {table} (libelle) VALUES (%s) RETURNING id", (label,))
    return cursor.fetchone()["id"]


def valid_email(value: str) -> bool:
    if not value:
        return True
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def byte_length(value: str) -> int:
    return len(value.encode("utf-8"))


def fix_encoding(value):
    """Corrige l'UTF-8 doublement encodé (Ã© au lieu de é)."""
    if not isinstance(value, str) or not re.search(r"Ã[\xA2-\xAF©]", value):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
