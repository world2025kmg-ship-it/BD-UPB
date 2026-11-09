import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import psycopg
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("upb_api")
ADMIN_TOKEN_MIN_LENGTH = 43
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60

app = FastAPI(
    title="API Concours Génie UPB",
    description="API FastAPI de gestion des dossiers étudiants et des présences.",
    version="1.0.0",
)

origins = [
    origin.strip()
    for origin in os.getenv("UPB_CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def disable_admin_response_caching(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health", include_in_schema=False)
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Requête invalide."
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": message},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info("Requête FastAPI invalide: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": "Données invalides."},
    )


@app.exception_handler(psycopg.Error)
async def database_error_handler(_: Request, exc: psycopg.Error) -> JSONResponse:
    logger.exception("Erreur PostgreSQL UPB", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Erreur de base de données."},
    )


@contextmanager
def database() -> Iterator[psycopg.Connection]:
    required = ("UPB_DB_HOST", "UPB_DB_NAME", "UPB_DB_USER", "UPB_DB_PASS")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise HTTPException(
            status_code=503,
            detail="Base de données non configurée. Définissez les variables UPB_DB_*.",
        )
    try:
        with psycopg.connect(
            host=os.environ["UPB_DB_HOST"],
            port=int(os.getenv("UPB_DB_PORT", "5432")),
            dbname=os.environ["UPB_DB_NAME"],
            user=os.environ["UPB_DB_USER"],
            password=os.environ["UPB_DB_PASS"],
            connect_timeout=5,
            options="-c statement_timeout=20000 -c lock_timeout=5000",
            row_factory=dict_row,
        ) as connection:
            yield connection
    except psycopg.Error:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Configuration PostgreSQL invalide.") from exc


async def json_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    return data


def required_text(data: dict[str, Any], field: str, max_length: int = 150) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise HTTPException(status_code=422, detail=f"Valeur invalide pour {field}.")
    return value.strip()


def optional_text(
    data: dict[str, Any], field: str, max_length: int, *, nullable: bool = True
) -> str | None:
    if field not in data:
        return None
    value = data[field]
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"Valeur invalide pour {field}.")
    value = value.strip()
    if len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"Le champ {field} est trop long.")
    return value or (None if nullable else "")


def parse_student_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
        if parsed <= date.today():
            return parsed.isoformat()
    return None


def student_matricule(nom: str, prenoms: str, birth_date: str) -> str:
    parsed_date = parse_student_date(birth_date)
    if not parsed_date:
        raise HTTPException(status_code=422, detail="Date de naissance invalide.")

    def normalized_name(value: str) -> str:
        ascii_name = unicodedata.normalize("NFKD", value.strip())
        letters = re.sub(r"[^A-Za-z]", "", ascii_name.encode("ascii", "ignore").decode()).upper()
        if not letters:
            raise HTTPException(status_code=422, detail="Nom ou prénom invalide.")
        return letters

    birth = date.fromisoformat(parsed_date)
    return (
        normalized_name(nom)[:3]
        + normalized_name(prenoms)[:1]
        + birth.strftime("%d%m%y")
        + "0001"
    )


def validate_email(value: str | None) -> str | None:
    if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    return value


def validate_sex(value: str | None) -> str | None:
    if value:
        value = value.upper()
        if value not in ("M", "F"):
            raise HTTPException(status_code=422, detail="Le sexe doit être M ou F.")
    return value


def reference_id(cursor: Any, table: str, label: str | None) -> int | None:
    if not label or not label.strip():
        return None
    if table not in ("filiere", "niveau"):
        raise ValueError("Référence invalide.")
    cursor.execute(f"SELECT id FROM {table} WHERE LOWER(libelle) = LOWER(%s) LIMIT 1", (label.strip(),))
    row = cursor.fetchone()
    if row:
        return int(row["id"])
    cursor.execute(f"INSERT INTO {table} (libelle) VALUES (%s) RETURNING id", (label.strip(),))
    return int(cursor.fetchone()["id"])


def configured_admin_token() -> str:
    expected = os.getenv("UPB_ADMIN_TOKEN", "")
    if len(expected) < ADMIN_TOKEN_MIN_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=(
                "Accès administrateur non configuré. Définissez UPB_ADMIN_TOKEN "
                "avec un jeton généré par generate_admin_token.py."
            ),
        )
    return expected


def create_admin_session(secret: str) -> str:
    payload = {
        "exp": int(time.time()) + ADMIN_SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    signing_input = f"v1.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"v1.{encoded_payload}.{encoded_signature}"


def is_valid_admin_session(token: str, secret: str) -> bool:
    try:
        version, encoded_payload, encoded_signature = token.split(".")
        if version != "v1":
            return False
        signing_input = f"{version}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        supplied_signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            )
        )
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("exp"), int)
            and payload["exp"] > int(time.time())
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False


def admin_required(authorization: str = Header(default="")) -> None:
    expected = configured_admin_token()
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not is_valid_admin_session(supplied.strip(), expected)
    ):
        raise HTTPException(status_code=401, detail="Accès réservé aux administrateurs.")


def public_student(cursor: Any, matricule: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT e.matricule, e.nom, e.prenoms, e.date_naissance,
               e.telephone, e.email, e.sexe, e.allergies_affection,
               f.libelle AS filiere, n.libelle AS niveau
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        WHERE e.matricule = %s
        """,
        (matricule,),
    )
    return cursor.fetchone()


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/admin.html", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(BASE_DIR / "admin.html")


@app.get("/scanner.html", include_in_schema=False)
def scanner_page() -> FileResponse:
    return FileResponse(BASE_DIR / "scanner.html")


@app.get("/genie.css", include_in_schema=False)
def stylesheet() -> FileResponse:
    return FileResponse(BASE_DIR / "genie.css")


@app.get("/genie-fx.js", include_in_schema=False)
def effects_script() -> FileResponse:
    return FileResponse(BASE_DIR / "genie-fx.js", media_type="application/javascript")


@app.get("/logo-upb.png", include_in_schema=False)
def logo() -> FileResponse:
    return FileResponse(BASE_DIR / "logo-upb.png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(BASE_DIR / "logo-upb.png", media_type="image/png")


@app.get("/v1/models")
def list_openai_compatible_models() -> dict[str, Any]:
    return {"object": "list", "data": []}


@app.post("/api/admin/auth/login")
def admin_auth_login(authorization: str = Header(default="")) -> JSONResponse:
    expected = configured_admin_token()
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(supplied.strip(), expected)
    ):
        raise HTTPException(status_code=401, detail="Jeton administrateur invalide.")
    return JSONResponse(
        content={
            "status": "success",
            "access_token": create_admin_session(expected),
            "token_type": "bearer",
            "expires_in": ADMIN_SESSION_TTL_SECONDS,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/admin/auth/verify")
def admin_auth_check(_: None = Depends(admin_required)) -> JSONResponse:
    return JSONResponse(
        content={"status": "success", "message": "Session administrateur valide."},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/students/verify")
async def verify_student(request: Request) -> dict[str, Any]:
    data = await json_body(request)
    matricule = data.get("matricule")
    if not isinstance(matricule, str) or not matricule.strip() or len(matricule.strip()) > 40:
        raise HTTPException(status_code=400, detail="Matricule requis.")
    with database() as connection, connection.cursor() as cursor:
        student = public_student(cursor, matricule.strip().upper())
    if not student:
        raise HTTPException(
            status_code=404,
            detail="Matricule non reconnu. Vous pouvez soumettre une demande de création de dossier.",
        )
    return {"status": "success", "message": "Vos informations ont été retrouvées.", "data": student}


@app.post("/api/student-registration-requests", status_code=201)
async def request_student_registration(request: Request) -> dict[str, Any]:
    data = await json_body(request)
    nom = required_text(data, "nom")
    prenoms = required_text(data, "prenoms")
    birth_date = parse_student_date(data.get("date_naissance"))
    if not birth_date:
        raise HTTPException(status_code=422, detail="Date de naissance invalide.")
    filiere = required_text(data, "filiere")
    niveau = required_text(data, "niveau")
    telephone = optional_text(data, "telephone", 40)
    email = validate_email(optional_text(data, "email", 255))
    sexe = validate_sex(optional_text(data, "sexe", 1))
    allergies = optional_text(data, "allergies_affection", 2000)
    matricule = student_matricule(nom, prenoms, birth_date)

    with database() as connection, connection.cursor() as cursor:
        if public_student(cursor, matricule):
            raise HTTPException(
                status_code=409,
                detail=f"Un dossier existe déjà avec le matricule {matricule}. Vérifiez vos informations avec ce matricule.",
            )
        cursor.execute(
            """
            SELECT id FROM etudiant_inscription_demande
            WHERE matricule = %s AND statut = 'EN_ATTENTE'
            """,
            (matricule,),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Une demande est déjà en attente pour ce dossier.")
        try:
            cursor.execute(
                """
                INSERT INTO etudiant_inscription_demande
                    (matricule, nom, prenoms, date_naissance, filiere, niveau,
                     telephone, email, sexe, allergies_affection)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    matricule,
                    nom,
                    prenoms,
                    birth_date,
                    filiere,
                    niveau,
                    telephone,
                    email,
                    sexe,
                    allergies,
                ),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(
                status_code=409,
                detail="Une demande est déjà en attente pour ce dossier.",
            ) from exc
    return {
        "status": "success",
        "message": "Votre demande de création de dossier a été transmise. Le matricule sera activé après validation par un administrateur.",
        "matricule": matricule,
    }


@app.get("/api/admin/students")
def get_students(
    q: str = Query(default="", max_length=150),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    search = f"%{q.strip()}%"
    where = ""
    params: list[Any] = []
    if q.strip():
        where = """
        WHERE e.matricule ILIKE %s OR e.nom ILIKE %s OR e.prenoms ILIKE %s
           OR f.libelle ILIKE %s OR n.libelle ILIKE %s OR e.email ILIKE %s
        """
        params = [search] * 6
    base = """
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
    """
    offset = (page - 1) * per_page
    with database() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) AS total {base} {where}", params)
        total = int(cursor.fetchone()["total"])
        cursor.execute(
            f"""
            SELECT e.id, e.matricule, e.nom, e.prenoms, e.date_naissance,
                   e.telephone, e.email, e.sexe, e.allergies_affection,
                   f.libelle AS filiere, n.libelle AS niveau,
                   c.numero_candidat, c.statut
            {base} {where}
            ORDER BY e.nom ASC
            LIMIT %s OFFSET %s
            """,
            [*params, per_page, offset],
        )
        students = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM etudiant")
        all_students = int(cursor.fetchone()["total"])
        cursor.execute("SELECT COUNT(DISTINCT etudiant_id) AS total FROM candidat_concours")
        enrolled = int(cursor.fetchone()["total"])
        cursor.execute(
            "SELECT COUNT(*) AS total FROM etudiant WHERE telephone IS NOT NULL AND TRIM(telephone) <> ''"
        )
        telephones = int(cursor.fetchone()["total"])
        cursor.execute(
            "SELECT COUNT(*) AS total FROM etudiant WHERE email IS NOT NULL AND TRIM(email) <> ''"
        )
        emails = int(cursor.fetchone()["total"])
        cursor.execute(
            """
            SELECT COALESCE(f.libelle, 'Non renseignée') AS libelle, COUNT(e.id) AS total
            FROM etudiant e LEFT JOIN filiere f ON e.filiere_id = f.id
            GROUP BY COALESCE(f.libelle, 'Non renseignée') ORDER BY total DESC
            """
        )
        filieres = cursor.fetchall()
        cursor.execute(
            """
            SELECT COALESCE(n.libelle, 'Non renseigné') AS libelle, COUNT(e.id) AS total
            FROM etudiant e LEFT JOIN niveau n ON e.niveau_id = n.id
            GROUP BY COALESCE(n.libelle, 'Non renseigné') ORDER BY total DESC
            """
        )
        niveaux = cursor.fetchall()
    return {
        "status": "success",
        "data": students,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "stats": {
            "total": all_students,
            "inscrits": enrolled,
            "telephones": telephones,
            "emails": emails,
            "filieres": filieres,
            "niveaux": niveaux,
        },
    }


@app.patch("/api/admin/students/{student_id}")
async def update_student(
    request: Request,
    student_id: int,
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    data = await json_body(request)
    if student_id < 1:
        raise HTTPException(status_code=400, detail="Données incomplètes.")

    limits = {
        "telephone": 40,
        "email": 255,
        "sexe": 1,
        "allergies_affection": 2000,
        "nom": 150,
        "prenoms": 150,
        "filiere": 150,
        "niveau": 150,
    }
    changes: dict[str, Any] = {}
    for field, max_length in limits.items():
        if field in data:
            value = optional_text(data, field, max_length)
            if field in ("nom", "prenoms") and not value:
                raise HTTPException(status_code=422, detail="Le nom et les prénoms sont obligatoires.")
            if field == "email":
                value = validate_email(value)
            if field == "sexe":
                value = validate_sex(value)
            changes[field] = value
    if "date_naissance" in data and data["date_naissance"]:
        birth_date = parse_student_date(data["date_naissance"])
        if not birth_date:
            raise HTTPException(status_code=422, detail="Date de naissance invalide.")
        changes["date_naissance"] = birth_date
    if not changes:
        raise HTTPException(status_code=400, detail="Données incomplètes.")

    with database() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, matricule, nom, prenoms, date_naissance FROM etudiant WHERE id = %s FOR UPDATE",
            (student_id,),
        )
        student = cursor.fetchone()
        if not student:
            raise HTTPException(status_code=404, detail="Étudiant introuvable.")
        if any(field in changes for field in ("nom", "prenoms", "date_naissance")):
            matricule = student_matricule(
                changes.get("nom", student["nom"]),
                changes.get("prenoms", student["prenoms"]),
                changes.get("date_naissance", student["date_naissance"].isoformat()),
            )
            cursor.execute(
                "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                (matricule, student_id),
            )
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail="Le matricule calculé existe déjà.")
            changes["matricule"] = matricule

        updates: dict[str, Any] = {}
        for field, value in changes.items():
            if field in ("filiere", "niveau"):
                updates[f"{field}_id"] = reference_id(cursor, field, value)
            else:
                updates[field] = value
        columns = ", ".join(f"{field} = %s" for field in updates)
        cursor.execute(
            f"UPDATE etudiant SET {columns} WHERE id = %s",
            [*updates.values(), student_id],
        )
    return {
        "status": "success",
        "message": "Fiche étudiant mise à jour avec succès !",
        "data": {"matricule": changes["matricule"]} if "matricule" in changes else {},
    }


@app.post("/api/student-change-requests", status_code=201)
async def request_student_modification(request: Request) -> dict[str, str]:
    data = await json_body(request)
    matricule = data.get("matricule")
    if not isinstance(matricule, str) or not matricule.strip():
        raise HTTPException(status_code=400, detail="Matricule requis.")
    editable = {
        "nom": 150,
        "prenoms": 150,
        "date_naissance": 10,
        "filiere": 150,
        "niveau": 150,
        "telephone": 40,
        "email": 255,
        "sexe": 1,
        "allergies_affection": 2000,
    }
    changes: dict[str, Any] = {}
    for field, limit in editable.items():
        if field not in data:
            continue
        value = optional_text(data, field, limit)
        if field in ("nom", "prenoms") and not value:
            raise HTTPException(
                status_code=422,
                detail="Le nom et les prénoms sont obligatoires (150 caractères maximum).",
            )
        if field == "date_naissance":
            value = parse_student_date(value)
            if not value:
                raise HTTPException(status_code=422, detail="Date de naissance invalide.")
        elif field == "email":
            value = validate_email(value)
        elif field == "sexe":
            value = validate_sex(value)
        elif field in ("filiere", "niveau") and value == "":
            value = None
        changes[field] = value
    if not changes:
        raise HTTPException(status_code=422, detail="Aucune modification proposée.")

    with database() as connection, connection.cursor() as cursor:
        student = public_student(cursor, matricule.strip().upper())
        if not student:
            raise HTTPException(status_code=404, detail="Matricule non reconnu.")
        different = {
            field: value
            for field, value in changes.items()
            if str(student.get(field) or "") != str(value or "")
        }
        if not different:
            raise HTTPException(status_code=422, detail="Aucune différence à soumettre.")
        cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule.strip().upper(),))
        student_id = cursor.fetchone()["id"]
        try:
            cursor.execute(
                "INSERT INTO etudiant_modification (etudiant_id, modifications) VALUES (%s, %s)",
                (student_id, Jsonb(different)),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(
                status_code=409,
                detail="Une demande est déjà en attente pour ce dossier.",
            ) from exc
    return {
        "status": "success",
        "message": "Votre demande a été transmise. Les changements seront appliqués après validation par un administrateur.",
    }


async def admin_modifications_impl(request: Request) -> dict[str, Any]:
    if request.method == "GET":
        with database() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.id, 'modification' AS request_type, m.etudiant_id,
                       m.modifications, m.created_at, e.matricule, e.nom, e.prenoms,
                       e.date_naissance
                FROM etudiant_modification m
                JOIN etudiant e ON e.id = m.etudiant_id
                WHERE m.statut = 'EN_ATTENTE'
                UNION ALL
                SELECT r.id, 'inscription' AS request_type, NULL AS etudiant_id,
                       jsonb_build_object(
                           'nom', r.nom, 'prenoms', r.prenoms,
                           'date_naissance', r.date_naissance, 'filiere', r.filiere,
                           'niveau', r.niveau, 'telephone', r.telephone, 'email', r.email,
                           'sexe', r.sexe, 'allergies_affection', r.allergies_affection
                       ) AS modifications,
                       r.created_at, r.matricule, r.nom, r.prenoms, r.date_naissance
                FROM etudiant_inscription_demande r
                WHERE r.statut = 'EN_ATTENTE'
                ORDER BY created_at ASC
                """
            )
            requests = cursor.fetchall()
        return {"status": "success", "data": requests}

    data = await json_body(request)
    try:
        request_id = int(data.get("id"))
    except (TypeError, ValueError):
        request_id = 0
    action = data.get("action")
    request_type = data.get("request_type", "modification")
    if request_id < 1 or action not in ("approve", "reject") or request_type not in (
        "modification",
        "inscription",
    ):
        raise HTTPException(status_code=400, detail="Demande ou action invalide.")

    with database() as connection, connection.cursor() as cursor:
        table = (
            "etudiant_modification"
            if request_type == "modification"
            else "etudiant_inscription_demande"
        )
        cursor.execute(
            f"SELECT * FROM {table} WHERE id = %s AND statut = 'EN_ATTENTE' FOR UPDATE",
            (request_id,),
        )
        pending = cursor.fetchone()
        if not pending:
            raise HTTPException(status_code=404, detail="Demande déjà traitée ou introuvable.")
        if action == "reject":
            cursor.execute(
                f"UPDATE {table} SET statut = 'REFUSEE', reviewed_at = NOW() WHERE id = %s",
                (request_id,),
            )
            return {"status": "success", "message": "Demande refusée."}

        if request_type == "inscription":
            cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (pending["matricule"],))
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail="Un dossier existe déjà avec ce matricule.")
            filiere_id = reference_id(cursor, "filiere", pending["filiere"])
            niveau_id = reference_id(cursor, "niveau", pending["niveau"])
            cursor.execute(
                """
                INSERT INTO etudiant
                    (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                     allergies_affection, filiere_id, niveau_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pending["matricule"],
                    pending["nom"],
                    pending["prenoms"],
                    pending["date_naissance"],
                    pending["telephone"],
                    pending["email"],
                    pending["sexe"],
                    pending["allergies_affection"],
                    filiere_id,
                    niveau_id,
                ),
            )
            cursor.execute(
                "UPDATE etudiant_inscription_demande SET statut = 'APPROUVEE', reviewed_at = NOW() WHERE id = %s",
                (request_id,),
            )
            return {
                "status": "success",
                "message": "Demande approuvée et dossier étudiant créé.",
                "matricule": pending["matricule"],
            }

        changes = pending["modifications"]
        allowed = {
            "nom",
            "prenoms",
            "date_naissance",
            "filiere",
            "niveau",
            "telephone",
            "email",
            "sexe",
            "allergies_affection",
        }
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise HTTPException(status_code=422, detail="Contenu de demande invalide.")
        cursor.execute(
            "SELECT matricule, nom, prenoms, date_naissance FROM etudiant WHERE id = %s FOR UPDATE",
            (pending["etudiant_id"],),
        )
        student = cursor.fetchone()
        if not student:
            raise HTTPException(status_code=404, detail="Étudiant associé à la demande introuvable.")
        if any(field in changes for field in ("nom", "prenoms", "date_naissance")):
            matricule = student_matricule(
                changes.get("nom", student["nom"]),
                changes.get("prenoms", student["prenoms"]),
                changes.get("date_naissance", student["date_naissance"].isoformat()),
            )
            cursor.execute(
                "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                (matricule, pending["etudiant_id"]),
            )
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail="Le matricule calculé existe déjà.")
            changes["matricule"] = matricule
        updates: dict[str, Any] = {}
        for field, value in changes.items():
            if field in ("filiere", "niveau"):
                updates[f"{field}_id"] = reference_id(cursor, field, value)
            else:
                updates[field] = value
        columns = ", ".join(f"{field} = %s" for field in updates)
        cursor.execute(
            f"UPDATE etudiant SET {columns} WHERE id = %s",
            [*updates.values(), pending["etudiant_id"]],
        )
        cursor.execute(
            "UPDATE etudiant_modification SET statut = 'APPROUVEE', reviewed_at = NOW() WHERE id = %s",
            (request_id,),
        )
    return {
        "status": "success",
        "message": "Demande approuvée et informations mises à jour.",
        "matricule": changes.get("matricule", student["matricule"]),
    }


@app.get("/api/admin/student-requests", operation_id="list_admin_modifications")
async def list_admin_modifications(
    request: Request,
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    return await admin_modifications_impl(request)


@app.post("/api/admin/student-requests", operation_id="review_admin_modification")
async def review_admin_modification(
    request: Request,
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    return await admin_modifications_impl(request)


@app.post("/api/admin/students/import", status_code=201)
async def import_students(
    fichier: UploadFile = File(...),
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    if not fichier.filename or Path(fichier.filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=422, detail="Le fichier doit être un CSV de 5 Mo maximum.")
    raw = await fichier.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Le fichier doit être un CSV de 5 Mo maximum.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Le fichier CSV doit être encodé en UTF-8.") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="Le fichier CSV est vide.")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="Le fichier CSV est vide.")

    aliases = {
        "prenom": "prenoms",
        "date_de_naissance": "date_naissance",
        "date_naiss": "date_naissance",
        "e_mail": "email",
        "allergies": "allergies_affection",
        "allergie_affection": "allergies_affection",
        "allergies_affections": "allergies_affection",
    }
    headers = []
    for field in reader.fieldnames:
        normalized = re.sub(
            r"[^a-z0-9]+",
            "_",
            unicodedata.normalize("NFKD", field.strip())
            .encode("ascii", "ignore")
            .decode()
            .lower(),
        ).strip("_")
        headers.append(aliases.get(normalized, normalized))
    required = {"nom", "prenoms", "date_naissance", "filiere", "niveau"}
    if not required.issubset(headers):
        raise HTTPException(
            status_code=422,
            detail="Colonnes obligatoires : nom, prenoms, date_naissance, filiere, niveau.",
        )
    rows = list(reader)
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="Le fichier dépasse la limite de 5 000 lignes.")
    errors: list[str] = []
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):
        normalized_row = {
            headers[index]: (value or "").strip()
            for index, value in enumerate(row.values())
            if index < len(headers)
        }
        nom = normalized_row.get("nom", "")
        prenoms = normalized_row.get("prenoms", "")
        birth_date = parse_student_date(normalized_row.get("date_naissance"))
        filiere = normalized_row.get("filiere", "")
        niveau = normalized_row.get("niveau", "")
        email = normalized_row.get("email", "")
        sex = normalized_row.get("sexe", "").upper()
        if not nom or not prenoms or not birth_date or not filiere or not niveau:
            errors.append(f"Ligne {number} : nom, prénoms, date de naissance, filière et niveau sont obligatoires.")
            continue
        try:
            validate_email(email or None)
            validate_sex(sex or None)
            matricule = student_matricule(nom, prenoms, birth_date)
        except HTTPException as exc:
            errors.append(f"Ligne {number} : {exc.detail}")
            continue
        if matricule in seen:
            errors.append(f"Ligne {number} : matricule dupliqué dans le CSV ({matricule}).")
            continue
        seen.add(matricule)
        prepared.append(
            {
                "matricule": matricule,
                "nom": nom,
                "prenoms": prenoms,
                "date_naissance": birth_date,
                "filiere": filiere,
                "niveau": niveau,
                "telephone": normalized_row.get("telephone") or None,
                "email": email or None,
                "sexe": sex or None,
                "allergies_affection": normalized_row.get("allergies_affection") or None,
            }
        )
    if errors:
        raise HTTPException(status_code=422, detail="Aucune ligne importée : " + " ".join(errors[:50]))
    if not prepared:
        raise HTTPException(status_code=422, detail="Le fichier ne contient aucun étudiant à importer.")

    matricules: list[str] = []
    with database() as connection, connection.cursor() as cursor:
        for student in prepared:
            cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (student["matricule"],))
            if cursor.fetchone():
                raise HTTPException(
                    status_code=422,
                    detail=f"Aucune ligne importée : le matricule {student['matricule']} existe déjà.",
                )
            filiere_id = reference_id(cursor, "filiere", student["filiere"])
            niveau_id = reference_id(cursor, "niveau", student["niveau"])
            cursor.execute(
                """
                INSERT INTO etudiant
                    (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                     allergies_affection, filiere_id, niveau_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    student["matricule"],
                    student["nom"],
                    student["prenoms"],
                    student["date_naissance"],
                    student["telephone"],
                    student["email"],
                    student["sexe"],
                    student["allergies_affection"],
                    filiere_id,
                    niveau_id,
                ),
            )
            matricules.append(student["matricule"])
    return {
        "status": "success",
        "message": f"{len(matricules)} étudiant(s) importé(s) avec succès.",
        "imported": len(matricules),
        "matricules": matricules,
    }


async def pointage_impl(
    request: Request,
    date_value: str | None,
    jour: str | None,
) -> dict[str, Any]:
    if request.method == "GET":
        selected_date = date_value or date.today().isoformat()
        parsed_date = parse_student_date(selected_date)
        if not parsed_date or parsed_date != selected_date:
            raise HTTPException(status_code=400, detail="Date invalide. Format attendu : AAAA-MM-JJ.")
        with database() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id, e.id AS etudiant_id, e.matricule, e.nom, e.prenoms,
                       f.libelle AS filiere, n.libelle AS niveau,
                       c.numero_candidat, p.jour_libelle, p.heure_pointage,
                       p.statut, p.qr_code_valide, p.created_at
                FROM pointage_journalier p
                JOIN etudiant e ON p.etudiant_id = e.id
                LEFT JOIN filiere f ON e.filiere_id = f.id
                LEFT JOIN niveau n ON e.niveau_id = n.id
                LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
                WHERE p.date_pointage = %s
                ORDER BY p.heure_pointage ASC
                """,
                (parsed_date,),
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) AS total FROM candidat_concours")
            total_enrolled = int(cursor.fetchone()["total"])
        total_present = len(rows)
        rate = round(total_present / total_enrolled * 100, 2) if total_enrolled else 0
        return {
            "status": "success",
            "date": parsed_date,
            "jour": jour,
            "stats": {
                "total_presents": total_present,
                "total_inscrits": total_enrolled,
                "taux_presence": f"{rate} %",
            },
            "data": rows,
        }

    data = await json_body(request)
    matricule = data.get("matricule")
    if not isinstance(matricule, str) or not matricule.strip():
        raise HTTPException(status_code=400, detail="Le champ 'matricule' est requis.")
    point_date = data.get("date") or date.today().isoformat()
    parsed_date = parse_student_date(point_date)
    if not parsed_date or parsed_date != point_date:
        raise HTTPException(status_code=400, detail="Date invalide. Format attendu : AAAA-MM-JJ.")
    jour_label = data.get("jour_libelle")
    if jour_label is not None and not isinstance(jour_label, str):
        raise HTTPException(status_code=422, detail="Valeur invalide pour jour_libelle.")
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule.strip().upper(),))
        student = cursor.fetchone()
        if not student:
            raise HTTPException(status_code=404, detail="Matricule introuvable dans la base UPB.")
        cursor.execute(
            """
            SELECT statut FROM pointage_journalier
            WHERE etudiant_id = %s AND date_pointage = %s
            """,
            (student["id"], parsed_date),
        )
        existing = cursor.fetchone()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Double pointage détecté : cet étudiant a déjà été pointé le {parsed_date}.",
            )
        try:
            cursor.execute(
                """
                INSERT INTO pointage_journalier
                    (etudiant_id, date_pointage, jour_libelle, heure_pointage, statut, qr_code_valide, created_at)
                VALUES (%s, %s, %s, NOW(), 'PRESENT', TRUE, NOW())
                RETURNING id, heure_pointage
                """,
                (student["id"], parsed_date, jour_label),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Double pointage détecté : cet étudiant a déjà été pointé le {parsed_date}.",
            ) from exc
        inserted = cursor.fetchone()
    return {
        "status": "success",
        "message": f"Présence enregistrée avec succès pour le matricule {matricule.strip().upper()}.",
        "data": {
            "id": inserted["id"],
            "etudiant_id": student["id"],
            "matricule": matricule.strip().upper(),
            "date_pointage": parsed_date,
            "statut": "PRESENT",
            "heure_pointage": inserted["heure_pointage"],
        },
    }


@app.get("/api/admin/attendance", operation_id="list_pointages")
async def list_pointages(
    request: Request,
    date_value: str | None = Query(default=None, alias="date"),
    jour: str | None = Query(default=None),
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    return await pointage_impl(request, date_value, jour)


@app.post("/api/admin/attendance", operation_id="create_pointage", status_code=201)
async def create_pointage(
    request: Request,
    _: None = Depends(admin_required),
) -> dict[str, Any]:
    return await pointage_impl(request, None, None)
