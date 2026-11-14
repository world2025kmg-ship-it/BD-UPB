"""Routes d'administration : contrôle du jeton, liste des étudiants, demandes, import CSV."""
import csv
import io
import logging
import re
import unicodedata
from math import ceil
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, File, Query, UploadFile

from .. import config
from .. import audit
from ..auth import require_admin
from ..db import Database, get_db
from ..errors import ApiError, respond
from ..student_helpers import (
    fix_encoding,
    lookup_reference_id,
    parse_student_date,
    student_matricule,
    valid_email,
)
from .. import docs as d
from .common import json_body, json_object, positive_int

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

MODIFICATION_FIELDS = {
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


@router.post(
    "/api/admin_auth_check",
    tags=["Administration"],
    summary="Vérifier le jeton administrateur",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(401, 503)},
)
def check_admin_auth():
    return respond(200, {"status": "success", "message": "Accès administrateur autorisé."})


def _query_int(value: str | None, default: int) -> int:
    """Entier de la query string : défaut si absent, 0 si non numérique."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return 0


@router.get(
    "/api/get_etudiants",
    tags=["Administration"],
    summary="Lister et rechercher les étudiants",
    description="Recherche partielle (insensible à la casse) sur matricule, nom, prénoms "
    "et filière, triée par nom, avec statistiques globales.",
    response_model=None,
    responses={200: {"model": d.StudentsListResponse}, **d.errors(401, 500, 503)},
)
def get_students(
    page: str | None = Query(default=None, description="Numéro de page (1 par défaut)."),
    per_page: str | None = Query(default=None, description="Taille de page (20 par défaut, 100 maximum)."),
    q: str = Query(default="", description="Recherche partielle."),
    db: Database = Depends(get_db),
):
    page_number = max(1, _query_int(page, 1))
    size = min(config.max_per_page(), max(1, _query_int(per_page, config.default_per_page())))
    offset = (page_number - 1) * size
    search = q.strip()

    base = """
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
    """
    where = ""
    parameters: tuple = ()
    if search:
        where = """
            WHERE e.matricule ILIKE %s
               OR e.nom ILIKE %s
               OR e.prenoms ILIKE %s
               OR f.libelle ILIKE %s
        """
        pattern = f"%{search}%"
        parameters = (pattern, pattern, pattern, pattern)

    stats: dict[str, Any] = {
        "total": 0,
        "inscrits": 0,
        "telephones": 0,
        "emails": 0,
        "filieres": [],
        "niveaux": [],
    }
    try:
        with db.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total {base} {where}", parameters)
            total = cursor.fetchone()["total"]
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
                (*parameters, size, offset),
            )
            students = cursor.fetchall()
    except psycopg.Error as error:
        logger.exception("Lecture des étudiants UPB")
        raise ApiError(500, str(error).strip() or "Erreur de base de données.")

    for student in students:
        for field, value in student.items():
            student[field] = fix_encoding(value)

    # Statistiques globales : indépendantes de la pagination et de la recherche.
    # Sans statistiques, la liste reste affichable.
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM etudiant")
            stats["total"] = cursor.fetchone()["total"]
            cursor.execute("SELECT COUNT(DISTINCT etudiant_id) AS total FROM candidat_concours")
            stats["inscrits"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM etudiant "
                "WHERE telephone IS NOT NULL AND TRIM(telephone) <> ''"
            )
            stats["telephones"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM etudiant "
                "WHERE email IS NOT NULL AND TRIM(email) <> ''"
            )
            stats["emails"] = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COALESCE(f.libelle, 'Non renseignée') AS libelle, COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN filiere f ON e.filiere_id = f.id
                GROUP BY COALESCE(f.libelle, 'Non renseignée')
                ORDER BY total DESC
                """
            )
            stats["filieres"] = cursor.fetchall()
            cursor.execute(
                """
                SELECT COALESCE(n.libelle, 'Non renseigné') AS libelle, COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN niveau n ON e.niveau_id = n.id
                GROUP BY COALESCE(n.libelle, 'Non renseigné')
                ORDER BY total DESC
                """
            )
            stats["niveaux"] = cursor.fetchall()
        for key in ("filieres", "niveaux"):
            for row in stats[key]:
                row["libelle"] = fix_encoding(row["libelle"])
    except psycopg.Error:
        logger.exception("Statistiques étudiants UPB")

    return respond(
        200,
        {
            "status": "success",
            "data": students,
            "total": total,
            "page": page_number,
            "per_page": size,
            "total_pages": ceil(total / size),
            "stats": stats,
        },
    )


@router.get(
    "/api/admin_modifications",
    tags=["Administration"],
    summary="Lister les demandes de correction en attente",
    response_model=None,
    responses={200: {"model": d.ModificationsListResponse}, **d.errors(401, 500, 503)},
)
def list_modifications(db: Database = Depends(get_db)):
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.id, m.etudiant_id, m.modifications, m.created_at,
                       e.matricule, e.nom, e.prenoms, e.date_naissance
                FROM etudiant_modification m
                JOIN etudiant e ON e.id = m.etudiant_id
                WHERE m.statut = 'EN_ATTENTE'
                ORDER BY m.created_at ASC
                """
            )
            requests = cursor.fetchall()
    except psycopg.Error:
        logger.exception("Lecture des demandes UPB")
        raise ApiError(500, "Impossible de charger les demandes.")
    return respond(200, {"status": "success", "data": requests})


def _decide_modification(db: Database, request_id: int, action: str) -> dict:
    """Approuve ou refuse une demande de correction (transaction propre)."""
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, etudiant_id, modifications
                    FROM etudiant_modification
                    WHERE id = %s AND statut = 'EN_ATTENTE'
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                modification = cursor.fetchone()
                if not modification:
                    raise ApiError(404, "Demande déjà traitée ou introuvable.")

                if action == "reject":
                    cursor.execute(
                        """
                        UPDATE etudiant_modification
                        SET statut = 'REFUSEE', reviewed_at = NOW()
                        WHERE id = %s
                        """,
                        (request_id,),
                    )
                    return {"message": "Demande refusée."}

                changes = modification["modifications"]
                if (
                    not isinstance(changes, dict)
                    or not changes
                    or set(changes) - MODIFICATION_FIELDS
                ):
                    raise ApiError(422, "Contenu de demande invalide.")

                cursor.execute(
                    """
                    SELECT matricule, nom, prenoms, date_naissance
                    FROM etudiant WHERE id = %s FOR UPDATE
                    """,
                    (modification["etudiant_id"],),
                )
                student = cursor.fetchone()
                if not student:
                    raise ApiError(422, "Étudiant associé à la demande introuvable.")

                matricule = student["matricule"]
                if {"nom", "prenoms", "date_naissance"} & changes.keys():
                    nom = str(changes.get("nom") or student["nom"])
                    prenoms = str(changes.get("prenoms") or student["prenoms"])
                    birth_date = str(changes.get("date_naissance") or student["date_naissance"] or "")
                    if not birth_date:
                        raise ApiError(
                            422, "Une date de naissance est requise pour recalculer le matricule."
                        )
                    try:
                        matricule = student_matricule(nom, prenoms, birth_date)
                    except ValueError as error:
                        raise ApiError(422, str(error))
                    cursor.execute(
                        "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                        (matricule, modification["etudiant_id"]),
                    )
                    if cursor.fetchone():
                        raise ApiError(
                            422, "Le matricule calculé existe déjà pour un autre étudiant."
                        )
                    changes["matricule"] = matricule

                for field, column in (("filiere", "filiere_id"), ("niveau", "niveau_id")):
                    if field in changes:
                        label = changes.pop(field)
                        changes[column] = (
                            None if label is None else lookup_reference_id(cursor, field, label)
                        )

                assignments = []
                values = []
                for field, value in changes.items():
                    if field == "date_naissance" and value is not None:
                        value = parse_student_date(str(value))
                        if not value:
                            raise ApiError(422, "Date de naissance invalide.")
                    if field in ("telephone", "email", "sexe", "allergies_affection") and value == "":
                        value = None
                    assignments.append(f"{field} = %s")
                    values.append(value)
                cursor.execute(
                    f"UPDATE etudiant SET {', '.join(assignments)} WHERE id = %s",
                    (*values, modification["etudiant_id"]),
                )
                cursor.execute(
                    """
                    UPDATE etudiant_modification
                    SET statut = 'APPROUVEE', reviewed_at = NOW()
                    WHERE id = %s
                    """,
                    (request_id,),
                )
    except psycopg.Error:
        logger.exception("Traitement de demande UPB")
        raise ApiError(500, "Erreur lors du traitement de la demande.")

    return {
        "message": "Demande approuvée et informations mises à jour.",
        "matricule": matricule,
    }


@router.post(
    "/api/admin_modifications",
    tags=["Administration"],
    summary="Approuver ou refuser une demande de correction",
    description="`approve` applique les changements (et recalcule le matricule si le nom, "
    "les prénoms ou la date de naissance changent) ; `reject` la refuse.",
    response_model=None,
    responses={
        200: {"model": d.ModificationDecisionResponse},
        **d.errors(400, 401, 404, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.ProcessModificationBody, "Demande et décision."),
)
def process_modification(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    request_id = positive_int(data.get("id"))
    action = data.get("action", "")
    if not request_id or action not in ("approve", "reject"):
        raise ApiError(400, "Demande ou action invalide.")
    audit.annotate(
        action="DEMANDE_CORRECTION_APPROUVEE" if action == "approve" else "DEMANDE_CORRECTION_REFUSEE",
        target_type="demande_correction", target=str(request_id),
    )
    result = _decide_modification(db, request_id, action)
    audit.annotate(matricule=result.get("matricule"))
    return respond(200, {"status": "success", **result})


CSV_ALIASES = {
    "prenom": "prenoms",
    "date_de_naissance": "date_naissance",
    "date_naiss": "date_naissance",
    "e_mail": "email",
    "allergies": "allergies_affection",
    "allergie_affection": "allergies_affection",
    "allergies_affections": "allergies_affection",
}
CSV_REQUIRED = {"nom", "prenoms", "date_naissance", "filiere", "niveau"}


def _normalize_header(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", str(value).strip()).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")


@router.post(
    "/api/import_etudiants",
    tags=["Administration"],
    summary="Importer des étudiants depuis un CSV",
    description="Champ multipart `fichier` (.csv, UTF-8, 5 Mo et 5 000 lignes maximum, "
    "séparateur `,` ou `;`). Colonnes obligatoires : `nom`, `prenoms`, `date_naissance`, "
    "`filiere`, `niveau` ; facultatives : `telephone`, `email`, `sexe`, "
    "`allergies_affection`. Tout ou rien : une erreur annule l'import.",
    response_model=None,
    responses={
        201: {"model": d.ImportResponse, "description": "Étudiants importés."},
        **d.errors(400, 401, 500, 503),
        422: {"model": d.ImportErrorResponse, "description": "Fichier ou lignes invalides."},
    },
)
def import_students(
    fichier: UploadFile | None = File(default=None), db: Database = Depends(get_db)
):
    if fichier is None:
        raise ApiError(400, "Sélectionnez un fichier CSV valide.")
    audit.annotate(target_type="fichier", target=fichier.filename)
    too_big = "Le fichier doit être un CSV de 5 Mo maximum."
    if not fichier.filename or not fichier.filename.lower().endswith(".csv"):
        raise ApiError(422, too_big)
    content = fichier.file.read(config.CSV_MAX_BYTES + 1)
    if len(content) > config.CSV_MAX_BYTES:
        raise ApiError(422, too_big)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(422, "Le fichier CSV doit être encodé en UTF-8.")
    if not text:
        raise ApiError(422, "Le fichier CSV est vide.")

    try:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        semicolon_header = next(csv.reader(io.StringIO(first_line), delimiter=";"), [])
        comma_header = next(csv.reader(io.StringIO(first_line), delimiter=","), [])
        delimiter = ";" if len(semicolon_header) >= len(comma_header) else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        headers = next(reader, [])
    except csv.Error:
        raise ApiError(422, "Fichier CSV invalide.")

    headers = [_normalize_header(value) for value in headers]
    headers = [CSV_ALIASES.get(header, header) for header in headers]
    if not CSV_REQUIRED.issubset(headers):
        raise ApiError(
            422, "Colonnes obligatoires : nom, prenoms, date_naissance, filiere, niveau."
        )

    errors: list[str] = []
    seen_matricules: list[str] = []  # ordre d'insertion
    seen_set: set[str] = set()
    reference_cache: dict[str, dict[str, int | None]] = {"filiere": {}, "niveau": {}}
    imported = 0
    try:
        with db.transaction():
            with db.cursor() as cursor:
                for row_number, values in enumerate(reader, start=2):
                    if not any(value.strip() for value in values):
                        continue
                    if row_number > config.CSV_MAX_ROW_NUMBER:
                        errors.append("Le fichier dépasse la limite de 5 000 lignes.")
                        break
                    row = {
                        key: (values[index].strip() if index < len(values) else "")
                        for index, key in enumerate(headers)
                    }
                    nom = row.get("nom", "")
                    prenoms = row.get("prenoms", "")
                    birth_date = parse_student_date(row.get("date_naissance", ""))
                    filiere = row.get("filiere", "")
                    niveau = row.get("niveau", "")
                    email = row.get("email", "")
                    sexe = row.get("sexe", "").upper()
                    telephone = row.get("telephone", "")
                    allergies = row.get("allergies_affection", "")

                    if not nom or not prenoms or not birth_date or not filiere or not niveau:
                        errors.append(
                            f"Ligne {row_number} : nom, prénoms, date de naissance, "
                            "filière et niveau sont obligatoires."
                        )
                        continue
                    if not valid_email(email):
                        errors.append(f"Ligne {row_number} : adresse e-mail invalide.")
                        continue
                    if sexe and sexe not in ("M", "F"):
                        errors.append(f"Ligne {row_number} : le sexe doit être M ou F.")
                        continue
                    try:
                        matricule = student_matricule(nom, prenoms, birth_date)
                    except ValueError as error:
                        errors.append(f"Ligne {row_number} : {error}")
                        continue

                    cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                    if cursor.fetchone() or matricule in seen_set:
                        errors.append(
                            f"Ligne {row_number} : matricule déjà présent ou dupliqué "
                            f"dans le CSV ({matricule})."
                        )
                        continue

                    reference_ids = {}
                    for table, label in (("filiere", filiere), ("niveau", niveau)):
                        key = label.strip().lower()
                        if key not in reference_cache[table]:
                            reference_cache[table][key] = lookup_reference_id(
                                cursor, table, label
                            )
                        reference_ids[table] = reference_cache[table][key]
                    cursor.execute(
                        """
                        INSERT INTO etudiant
                            (matricule, nom, prenoms, date_naissance, telephone,
                             email, sexe, allergies_affection, filiere_id, niveau_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            matricule,
                            nom,
                            prenoms,
                            birth_date,
                            telephone or None,
                            email or None,
                            sexe or None,
                            allergies or None,
                            reference_ids["filiere"],
                            reference_ids["niveau"],
                        ),
                    )
                    seen_matricules.append(matricule)
                    seen_set.add(matricule)
                    imported += 1

                # Lever une exception ici annule toute la transaction (aucune ligne importée).
                if errors:
                    raise ApiError(
                        422,
                        "Aucune ligne importée : corrigez les erreurs puis réessayez.",
                        errors=errors[:50],
                    )
                if imported == 0:
                    raise ApiError(422, "Le fichier ne contient aucun étudiant à importer.")
    except psycopg.Error:
        logger.exception("Import CSV UPB")
        raise ApiError(
            500, "Erreur lors de l'import. Vérifiez le format CSV et le schéma de la base."
        )

    audit.annotate(importes=imported)
    return respond(
        201,
        {
            "status": "success",
            "message": f"{imported} étudiant(s) importé(s) avec succès.",
            "imported": imported,
            "matricules": seen_matricules,
        },
    )


@router.get(
    "/api/admin_creations",
    tags=["Administration"],
    summary="Lister les demandes d'ajout de dossier en attente",
    response_model=None,
    responses={200: {"model": d.CreationsListResponse}, **d.errors(401, 500, 503)},
)
def list_creations(db: Database = Depends(get_db)):
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, matricule_saisi, donnees, created_at
                FROM etudiant_creation_demande
                WHERE statut = 'EN_ATTENTE'
                ORDER BY created_at ASC
                """
            )
            requests = cursor.fetchall()
    except psycopg.Error:
        logger.exception("Lecture des demandes de création UPB")
        raise ApiError(500, "Impossible de charger les demandes.")
    return respond(200, {"status": "success", "data": requests})


def _decide_creation(db: Database, request_id: int, action: str) -> dict:
    """Approuve (crée l'étudiant) ou refuse une demande d'ajout de dossier."""
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, donnees FROM etudiant_creation_demande
                    WHERE id = %s AND statut = 'EN_ATTENTE'
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                demande = cursor.fetchone()
                if not demande:
                    raise ApiError(404, "Demande déjà traitée ou introuvable.")

                if action == "reject":
                    cursor.execute(
                        """
                        UPDATE etudiant_creation_demande
                        SET statut = 'REFUSEE', reviewed_at = NOW()
                        WHERE id = %s
                        """,
                        (request_id,),
                    )
                    return {"message": "Demande refusée."}

                fields = demande["donnees"]
                if (
                    not isinstance(fields, dict)
                    or not (MODIFICATION_FIELDS >= set(fields))
                    or not all(fields.get(k) for k in ("nom", "prenoms", "date_naissance"))
                ):
                    raise ApiError(422, "Contenu de demande invalide.")
                try:
                    matricule = student_matricule(
                        fields["nom"], fields["prenoms"], fields["date_naissance"]
                    )
                except ValueError as error:
                    raise ApiError(422, str(error))
                cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                if cursor.fetchone():
                    raise ApiError(
                        422, "Le matricule calculé existe déjà pour un autre étudiant."
                    )

                cursor.execute(
                    """
                    INSERT INTO etudiant
                        (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                         allergies_affection, filiere_id, niveau_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        matricule,
                        fields["nom"],
                        fields["prenoms"],
                        fields["date_naissance"],
                        fields.get("telephone") or None,
                        fields.get("email") or None,
                        fields.get("sexe") or None,
                        fields.get("allergies_affection") or None,
                        lookup_reference_id(cursor, "filiere", fields.get("filiere")),
                        lookup_reference_id(cursor, "niveau", fields.get("niveau")),
                    ),
                )
                student_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    UPDATE etudiant_creation_demande
                    SET statut = 'APPROUVEE', reviewed_at = NOW(), etudiant_id = %s
                    WHERE id = %s
                    """,
                    (student_id, request_id),
                )
    except psycopg.Error:
        logger.exception("Traitement de demande de création UPB")
        raise ApiError(500, "Erreur lors du traitement de la demande.")

    return {
        "message": "Demande approuvée : le dossier étudiant a été créé.",
        "matricule": matricule,
    }


@router.post(
    "/api/admin_creations",
    tags=["Administration"],
    summary="Approuver ou refuser une demande d'ajout de dossier",
    description="`approve` crée l'étudiant avec le matricule calculé (nom, prénom, date de "
    "naissance) ; `reject` refuse la demande.",
    response_model=None,
    responses={
        200: {"model": d.CreationDecisionResponse},
        **d.errors(400, 401, 404, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.ProcessModificationBody, "Demande et décision."),
)
def process_creation(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    request_id = positive_int(data.get("id"))
    action = data.get("action", "")
    if not request_id or action not in ("approve", "reject"):
        raise ApiError(400, "Demande ou action invalide.")
    audit.annotate(
        action="DEMANDE_CREATION_APPROUVEE" if action == "approve" else "DEMANDE_CREATION_REFUSEE",
        target_type="demande_creation", target=str(request_id),
    )
    result = _decide_creation(db, request_id, action)
    audit.annotate(matricule=result.get("matricule"))
    return respond(200, {"status": "success", **result})


def _approve_all(db: Database, table: str, decide, kind: str) -> dict:
    """Approuve chaque demande en attente indépendamment ; les échecs sont listés."""
    with db.cursor() as cursor:
        cursor.execute(f"SELECT id FROM {table} WHERE statut = 'EN_ATTENTE' ORDER BY created_at ASC, id ASC")
        ids = [row["id"] for row in cursor.fetchall()]
    approved, failures = 0, []
    for request_id in ids:
        try:
            result = decide(db, request_id, "approve")
            approved += 1
            audit.log(
                f"DEMANDE_{kind}_APPROUVEE", "DEMANDE", target_type=f"demande_{kind.lower()}",
                target=str(request_id), details={"mode": "masse", "matricule": result.get("matricule")},
            )
        except ApiError as error:
            failures.append({"id": request_id, "message": error.message})
            audit.log(
                f"DEMANDE_{kind}_APPROUVEE", "DEMANDE", status=audit.ECHEC, target_type=f"demande_{kind.lower()}",
                target=str(request_id), message=error.message, details={"mode": "masse"},
            )
    return {"approuvees": approved, "echecs": failures}


@router.post(
    "/api/admin_approve_all",
    tags=["Administration"],
    summary="Approuver toutes les demandes en attente d'un coup",
    description="Approuve toutes les demandes de correction et d'ajout de dossier en attente. "
    "Chaque demande est traitée indépendamment : une demande invalide (ex. conflit de "
    "matricule) est laissée en attente et listée dans `echecs`, sans bloquer les autres.",
    response_model=None,
    responses={200: {"model": d.ApproveAllResponse}, **d.errors(401, 500, 503)},
)
def approve_all(db: Database = Depends(get_db)):
    modifications = _approve_all(db, "etudiant_modification", _decide_modification, "CORRECTION")
    creations = _approve_all(db, "etudiant_creation_demande", _decide_creation, "CREATION")
    total = modifications["approuvees"] + creations["approuvees"]
    failed = len(modifications["echecs"]) + len(creations["echecs"])
    audit.annotate(
        approuvees=total, echecs=failed,
        corrections=modifications["approuvees"], creations=creations["approuvees"],
    )
    message = f"{total} demande(s) approuvée(s)."
    if failed:
        message += f" {failed} demande(s) n'ont pas pu être approuvées et restent en attente."
    return respond(
        200,
        {"status": "success", "message": message, "modifications": modifications, "creations": creations},
    )
