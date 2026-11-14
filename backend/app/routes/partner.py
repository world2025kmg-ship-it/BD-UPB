"""Routes réservées aux sites partenaires (clé d'API) : vérification + marquage « inscrit »."""
import logging
import secrets
from typing import Any

import psycopg
from fastapi import APIRouter, Depends

from .. import docs as d
from .. import audit
from ..auth import require_partner
from ..db import Database, get_db
from ..errors import ApiError, respond
from .common import json_body, json_object

logger = logging.getLogger(__name__)
router = APIRouter()

UNKNOWN_MATRICULE = "Matricule non reconnus dans la base I-UPB."


@router.post(
    "/api/partenaire/verifier",
    tags=["Partenaires"],
    summary="Vérifier un étudiant et le marquer inscrit au concours",
    description=(
        "Réservé au site officiel d'inscription (en-tête `X-API-Key`). Retrouve l'étudiant par "
        "son matricule, **le marque inscrit au concours** s'il ne l'est pas encore, et renvoie "
        "son identité. L'appel est idempotent : répéter la requête ne crée pas de doublon et "
        "renvoie le numéro de candidat existant. Si `numero_candidat` est fourni et que "
        "l'étudiant n'est pas encore inscrit, ce numéro est enregistré ; sinon un numéro "
        "`GENIE-2026-XXXXX` est généré."
    ),
    response_model=None,
    responses={
        200: {"model": d.PartnerVerifyResponse, "description": "Étudiant vérifié et inscrit."},
        **d.errors(400, 401, 404, 500),
    },
    openapi_extra=d.request_body(d.PartnerVerifyBody, "Matricule et numéro de candidat facultatif."),
)
def partner_verify(
    body: Any = Depends(json_body),
    partner: str = Depends(require_partner),
    db: Database = Depends(get_db),
):
    data = json_object(body)
    matricule = data.get("matricule") if data else None
    matricule = matricule.strip().upper() if isinstance(matricule, str) else ""
    if not matricule or len(matricule) > 40:
        raise ApiError(400, "Matricule requis.")
    audit.annotate(target_type="matricule", target=matricule)
    given_number = data.get("numero_candidat")
    if given_number is not None:
        if not isinstance(given_number, str) or len(given_number.strip()) > 40:
            raise ApiError(400, "numero_candidat invalide (40 caractères maximum).")
        given_number = given_number.strip() or None

    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.id, e.matricule, e.nom, e.prenoms, e.date_naissance,
                           e.telephone, e.email, e.sexe, e.allergies_affection,
                           f.libelle AS filiere, n.libelle AS niveau
                    FROM etudiant e
                    LEFT JOIN filiere f ON e.filiere_id = f.id
                    LEFT JOIN niveau n ON e.niveau_id = n.id
                    WHERE e.matricule = %s
                    """,
                    (matricule,),
                )
                student = cursor.fetchone()
                if not student:
                    raise ApiError(404, UNKNOWN_MATRICULE)
                student_id = student.pop("id")

                cursor.execute(
                    "SELECT numero_candidat, statut FROM candidat_concours "
                    "WHERE etudiant_id = %s ORDER BY id LIMIT 1 FOR UPDATE",
                    (student_id,),
                )
                existing = cursor.fetchone()
                if existing:
                    registration = {**existing, "deja_inscrit": True}
                else:
                    number = given_number or "GENIE-2026-" + secrets.token_hex(3)[:5].upper()
                    cursor.execute(
                        "INSERT INTO candidat_concours (etudiant_id, numero_candidat, statut) "
                        "VALUES (%s, %s, 'EN_ATTENTE')",
                        (student_id, number),
                    )
                    registration = {
                        "numero_candidat": number,
                        "statut": "EN_ATTENTE",
                        "deja_inscrit": False,
                    }
    except psycopg.Error:
        logger.exception("Vérification partenaire UPB (%s)", partner)
        raise ApiError(500, "Erreur lors de la vérification des informations.")

    audit.annotate(
        numero_candidat=registration["numero_candidat"], deja_inscrit=registration["deja_inscrit"]
    )
    return respond(
        200,
        {
            "status": "success",
            "message": "Étudiant vérifié et inscrit au concours.",
            "data": student,
            "inscription": registration,
        },
    )
