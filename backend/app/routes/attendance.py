"""Pointage quotidien par QR Code."""
import html
import logging
import re
from datetime import date
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query

from .. import audit
from ..auth import require_admin
from ..db import Database, get_db
from ..errors import ApiError, respond
from .. import docs as d
from .common import json_body, json_object

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_ERROR = "Date invalide. Format attendu : AAAA-MM-JJ."


def parse_iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


@router.get(
    "/api/pointage",
    tags=["Pointage"],
    summary="Rapport de présence d'une date",
    response_model=None,
    responses={200: {"model": d.AttendanceReportResponse}, **d.errors(400, 401, 500, 503)},
)
def get_attendance(
    date_: str | None = Query(default=None, alias="date", description="`AAAA-MM-JJ` ; aujourd'hui par défaut."),
    jour: str | None = Query(default=None, description="Libellé du jour, renvoyé tel quel."),
    db: Database = Depends(get_db),
):
    parsed_date = parse_iso_date(date_ if date_ is not None else date.today().isoformat())
    if not parsed_date:
        raise ApiError(400, DATE_ERROR)

    try:
        with db.cursor() as cursor:
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
    except psycopg.Error as error:
        logger.exception("Lecture du pointage UPB")
        raise ApiError(500, "Erreur base de données : " + str(error).strip())

    total_registered = 0
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM candidat_concours")
            total_registered = cursor.fetchone()["total"]
    except psycopg.Error:
        logger.exception("Comptage des inscrits UPB")

    total_present = len(rows)
    rate = round(total_present / total_registered * 100, 2) if total_registered > 0 else 0
    return respond(
        200,
        {
            "status": "success",
            "date": parsed_date,
            "jour": jour,
            "stats": {
                "total_presents": total_present,
                "total_inscrits": total_registered,
                "taux_presence": f"{rate:g} %",
            },
            "data": rows,
        },
    )


@router.post(
    "/api/pointage",
    tags=["Pointage"],
    summary="Enregistrer une présence (scan QR code)",
    description="Un étudiant ne peut être pointé qu'une fois par date (`409` sinon).",
    response_model=None,
    responses={
        201: {"model": d.AttendanceRecordedResponse},
        **d.errors(400, 401, 404, 409, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.AttendanceBody, "Matricule scanné, date et libellé du jour."),
)
def record_attendance(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body)
    if (
        not data
        or not isinstance(data.get("matricule"), str)
        or not data["matricule"].strip()
    ):
        raise ApiError(400, "Le champ 'matricule' est requis.")

    matricule = data["matricule"].strip()
    audit.annotate(target_type="matricule", target=matricule)
    parsed_date = parse_iso_date(data.get("date", date.today().isoformat()))
    if not parsed_date:
        raise ApiError(400, DATE_ERROR)
    day_label = data.get("jour_libelle")
    if day_label is not None and not isinstance(day_label, str):
        raise ApiError(422, "Valeur invalide pour jour_libelle.")
    audit.annotate(date=parsed_date, jour=day_label)

    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                student = cursor.fetchone()
                if not student:
                    raise ApiError(404, "Matricule introuvable dans la base UPB.")

                cursor.execute(
                    """
                    SELECT id, statut FROM pointage_journalier
                    WHERE etudiant_id = %s AND date_pointage = %s
                    """,
                    (student["id"], parsed_date),
                )
                existing = cursor.fetchone()
                if existing:
                    raise ApiError(
                        409,
                        "Double pointage détecté : cet étudiant a déjà été pointé le "
                        f"{parsed_date}.",
                        data={"statut_existant": existing["statut"]},
                    )

                cursor.execute(
                    """
                    INSERT INTO pointage_journalier
                        (etudiant_id, date_pointage, jour_libelle,
                         heure_pointage, statut, qr_code_valide, created_at)
                    VALUES (%s, %s, %s, NOW(), 'PRESENT', TRUE, NOW())
                    RETURNING id, heure_pointage
                    """,
                    (student["id"], parsed_date, day_label),
                )
                pointage = cursor.fetchone()
    except psycopg.errors.UniqueViolation as error:
        # Course sur le double pointage : la contrainte uq_etudiant_date a tranché.
        if error.diag.constraint_name == "uq_etudiant_date":
            raise ApiError(
                409, "Double pointage détecté : cet étudiant a déjà été pointé ce jour."
            )
        logger.exception("Contrainte unique durant le pointage UPB")
        raise ApiError(500, "Erreur base de données : " + str(error).strip())
    except psycopg.Error as error:
        logger.exception("Enregistrement du pointage UPB")
        raise ApiError(500, "Erreur base de données : " + str(error).strip())

    return respond(
        201,
        {
            "status": "success",
            "message": "Présence enregistrée avec succès pour le matricule "
            f"{html.escape(matricule, quote=True)}.",
            "data": {
                "id": pointage["id"],
                "etudiant_id": student["id"],
                "matricule": matricule,
                "date_pointage": parsed_date,
                "statut": "PRESENT",
                "heure_pointage": pointage["heure_pointage"],
            },
        },
    )
