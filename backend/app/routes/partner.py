"""Routes réservées aux sites partenaires (clé d'API) : vérification, et marquage « inscrit » si autorisé."""
import logging
import secrets
from typing import Any

import psycopg
from fastapi import APIRouter, Depends

from .. import audit
from .. import docs as d
from ..auth import require_partner
from ..db import Database, get_db
from ..errors import ApiError, respond
from ..student_helpers import normalize_student_name, parse_student_date
from .common import json_body, json_object

logger = logging.getLogger(__name__)
router = APIRouter()

UNKNOWN_MATRICULE = "Matricule non reconnus dans la base I-UPB."

# Données d'un étudiant qu'une intégration peut recevoir (ordre de la réponse)
STUDENT_FIELDS = (
    "matricule", "nom", "prenoms", "date_naissance", "filiere", "niveau",
    "sexe", "email", "telephone", "allergies_affection",
)
DEFAULT_FIELDS = ("matricule", "nom", "prenoms", "date_naissance", "filiere", "niveau")
PERMISSIONS = ("VERIFICATION", "INSCRIPTION")


def _same_name(declared: str, actual: str) -> bool:
    """Comparaison insensible à la casse, aux accents, espaces et tirets."""
    try:
        return normalize_student_name(declared) == normalize_student_name(actual)
    except ValueError:
        return False


def _matches(declared: dict[str, str], student: dict) -> dict[str, bool]:
    result = {}
    for field in ("nom", "prenoms"):
        if field in declared:
            result[field] = _same_name(declared[field], student[field])
    if "date_naissance" in declared:
        parsed = parse_student_date(declared["date_naissance"])
        result["date_naissance"] = parsed is not None and parsed == str(student["date_naissance"])
    return result


@router.post(
    "/api/partenaire/verifier",
    tags=["Partenaires"],
    summary="Vérifier un étudiant (et l'inscrire au concours si la clé y est autorisée)",
    description=(
        "Réservé aux sites partenaires autorisés (en-tête `X-API-Key`). **Le matricule suffit** : le site "
        "n'a pas à demander d'autres informations à l'utilisateur. `verifie: true` confirme que le "
        "matricule existe (`404` sinon). La réponse renvoie l'identité de l'étudiant, **limitée aux "
        "champs autorisés pour ce partenaire** (un partenaire peut être limité au seul matricule).\n\n"
        "Facultatif : si le site a tout de même collecté `nom`, `prenoms` et/ou `date_naissance`, il peut "
        "les envoyer ; `correspondance` indique pour chacun s'il correspond au dossier, **sans révéler "
        "les valeurs** (utile pour confirmer une identité sans recevoir ces données).\n\n"
        "* Permission **VERIFICATION** : lecture seule. `inscription` indique si l'étudiant est déjà "
        "inscrit au concours (`null` sinon). Aucun marquage n'est effectué.\n"
        "* Permission **INSCRIPTION** : l'étudiant est **marqué inscrit au concours** s'il ne l'est pas "
        "encore. L'appel est idempotent. Si `numero_candidat` est fourni, il est enregistré à la "
        "première inscription ; sinon un numéro `GENIE-2026-XXXXX` est généré.\n\n"
        "Erreurs d'accès : `401` clé absente, inconnue, révoquée, expirée ou partenaire désactivé ; "
        "`403` adresse IP non autorisée, ou `numero_candidat` fourni sans permission INSCRIPTION ; "
        "`429` quota d'appels par minute dépassé."
    ),
    response_model=None,
    responses={
        200: {"model": d.PartnerVerifyResponse, "description": "Étudiant vérifié."},
        **d.errors(400, 401, 403, 404, 429, 500),
    },
    openapi_extra=d.request_body(d.PartnerVerifyBody, "Matricule et numéro de candidat facultatif."),
)
def partner_verify(
    body: Any = Depends(json_body),
    partner: dict = Depends(require_partner),
    db: Database = Depends(get_db),
):
    can_register = partner["permission"] == "INSCRIPTION"
    allowed_fields = [f for f in STUDENT_FIELDS if f in partner["champs"]]
    audit.annotate(
        action="INSCRIPTION_CONCOURS" if can_register else "VERIFICATION_PARTENAIRE",
        category="INSCRIPTION" if can_register else "PARTENAIRE",
    )

    data = json_object(body)
    matricule = data.get("matricule") if data else None
    matricule = matricule.strip().upper() if isinstance(matricule, str) else ""
    if not matricule or len(matricule) > 40:
        raise ApiError(400, "Matricule requis.")
    audit.annotate(target_type="matricule", target=matricule)
    given_number = data.get("numero_candidat")
    if given_number is not None:
        if not can_register:
            raise ApiError(403, "Cette clé n'est pas autorisée à inscrire des étudiants.")
        if not isinstance(given_number, str) or len(given_number.strip()) > 40:
            raise ApiError(400, "numero_candidat invalide (40 caractères maximum).")
        given_number = given_number.strip() or None
    declared: dict[str, str] = {}
    for field in ("nom", "prenoms", "date_naissance"):
        if data.get(field) is not None:
            if not isinstance(data[field], str):
                raise ApiError(400, f"Valeur invalide pour {field}.")
            if data[field].strip():
                declared[field] = data[field].strip()

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
                    "WHERE etudiant_id = %s ORDER BY id LIMIT 1" + (" FOR UPDATE" if can_register else ""),
                    (student_id,),
                )
                existing = cursor.fetchone()
                registration = None
                if existing:
                    registration = {**existing, "deja_inscrit": True}
                elif can_register:
                    number = given_number or "GENIE-2026-" + secrets.token_hex(3)[:5].upper()
                    cursor.execute(
                        "INSERT INTO candidat_concours (etudiant_id, numero_candidat, statut) "
                        "VALUES (%s, %s, 'EN_ATTENTE')",
                        (student_id, number),
                    )
                    registration = {"numero_candidat": number, "statut": "EN_ATTENTE", "deja_inscrit": False}
    except psycopg.Error:
        logger.exception("Vérification partenaire UPB (%s)", partner["nom"])
        raise ApiError(500, "Erreur lors de la vérification des informations.")

    if registration:
        audit.annotate(
            numero_candidat=registration["numero_candidat"], deja_inscrit=registration["deja_inscrit"]
        )
    return respond(
        200,
        {
            "status": "success",
            "message": "Étudiant vérifié et inscrit au concours." if can_register else "Étudiant vérifié.",
            "verifie": True,
            "correspondance": _matches(declared, student) if declared else None,
            "data": {field: student[field] for field in allowed_fields},
            "inscription": registration,
        },
    )
