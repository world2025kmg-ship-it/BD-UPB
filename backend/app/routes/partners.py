"""Gestion des intégrations (sites externes) : création, autorisations, clés d'API, suppression.

Réservé aux administrateurs. Une clé d'API n'est affichée qu'UNE fois, à sa génération ; seule son
empreinte SHA-256 est conservée. Aucune clé n'est jamais écrite dans le journal d'activité.
"""
import logging
import re
from datetime import date
from typing import Any

import psycopg
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb

from .. import audit
from .. import docs as d
from .. import security
from ..auth import require_admin
from ..db import Database, get_db
from ..errors import ApiError, respond
from .common import json_body, json_object, positive_int
from .partner import DEFAULT_FIELDS, PERMISSIONS, STUDENT_FIELDS

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

MAX_ACTIVE_KEYS = 10
MAX_IPS = 20
PARTNER_COLUMNS = (
    "id, nom, description, site_url, contact, permission, champs, ips_autorisees, limite_par_minute, "
    "actif, created_at, updated_at, derniere_utilisation, cree_par"
)


def _text(data: dict, key: str, maximum: int, label: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(422, f"{label} invalide.")
    value = value.strip()
    if len(value) > maximum:
        raise ApiError(422, f"{label} trop long ({maximum} caractères maximum).")
    return value or None


def _clean_fields(data: dict, *, creating: bool) -> dict[str, Any]:
    """Valide les champs d'une fiche d'intégration (seuls ceux présents sont retournés)."""
    out: dict[str, Any] = {}
    if creating:
        name = data.get("nom")
        if not isinstance(name, str) or not 3 <= len(name.strip()) <= 80:
            raise ApiError(422, "Le nom de l'intégration est obligatoire (3 à 80 caractères).")
        out["nom"] = name.strip()
    for key, maximum, label in (
        ("description", 1000, "Description"), ("site_url", 255, "Adresse du site"), ("contact", 255, "Contact"),
    ):
        if key in data:
            out[key] = _text(data, key, maximum, label)
    if out.get("site_url") and not re.match(r"^https?://[^\s/]+", out["site_url"], re.IGNORECASE):
        raise ApiError(422, "L'adresse du site doit commencer par http:// ou https://.")
    if "permission" in data or creating:
        permission = data.get("permission", "VERIFICATION")
        if permission not in PERMISSIONS:
            raise ApiError(422, "Permission invalide (VERIFICATION ou INSCRIPTION).")
        out["permission"] = permission
    if "champs" in data or creating:
        fields = data.get("champs", list(DEFAULT_FIELDS))
        if not isinstance(fields, list) or any(f not in STUDENT_FIELDS for f in fields):
            raise ApiError(422, "Champs invalides.")
        fields = [f for f in STUDENT_FIELDS if f in fields or f == "matricule"]  # ordre stable, matricule inclus
        out["champs"] = fields
    if "ips_autorisees" in data or creating:
        raw = data.get("ips_autorisees", [])
        if isinstance(raw, str):
            raw = [part for part in re.split(r"[\s,;]+", raw) if part]
        if not isinstance(raw, list) or len(raw) > MAX_IPS:
            raise ApiError(422, f"Liste d'adresses IP invalide ({MAX_IPS} entrées maximum).")
        networks = []
        for entry in raw:
            try:
                networks.append(str(security.parse_network(str(entry))))
            except ValueError:
                raise ApiError(422, f"Adresse IP ou réseau invalide : {str(entry)[:60]}")
        out["ips_autorisees"] = list(dict.fromkeys(networks))
    if "limite_par_minute" in data:
        limit = data["limite_par_minute"]
        if limit in (None, ""):
            out["limite_par_minute"] = None
        elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ApiError(422, "Le quota par minute doit être un entier entre 1 et 100 000 (ou vide).")
        else:
            out["limite_par_minute"] = limit
    return out


def _key_status(key: dict) -> str:
    if key["revoquee_le"] is not None:
        return "REVOQUEE"
    if key["expire_le"] is not None and key["expire_le"] < date.today():
        return "EXPIREE"
    return "ACTIVE"


def _list_partners(db: Database, only_id: int | None = None) -> list[dict]:
    with db.cursor() as cursor:
        cursor.execute(
            f"SELECT {PARTNER_COLUMNS} FROM partenaire"
            + (" WHERE id = %s" if only_id else "")
            + " ORDER BY LOWER(nom)",
            (only_id,) if only_id else (),
        )
        partners = cursor.fetchall()
        cursor.execute(
            "SELECT id, partenaire_id, prefixe, libelle, created_at, cree_par, expire_le, revoquee_le, "
            "derniere_utilisation, nb_utilisations FROM partenaire_cle ORDER BY created_at DESC, id DESC"
        )
        keys = cursor.fetchall()
        cursor.execute(
            """
            SELECT acteur,
                   COUNT(*) AS appels_total,
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS appels_24h,
                   COUNT(*) FILTER (WHERE statut = 'ECHEC'
                                    AND created_at >= NOW() - INTERVAL '24 hours') AS echecs_24h,
                   COUNT(*) FILTER (WHERE statut = 'SUCCES' AND details->>'deja_inscrit' = 'false') AS inscrits_marques,
                   MAX(created_at) AS derniere_activite
            FROM journal_activite
            WHERE acteur_type = 'PARTENAIRE' AND action IN ('INSCRIPTION_CONCOURS', 'VERIFICATION_PARTENAIRE')
            GROUP BY acteur
            """
        )
        stats = {row["acteur"]: row for row in cursor.fetchall()}
    empty = {"appels_total": 0, "appels_24h": 0, "echecs_24h": 0, "inscrits_marques": 0, "derniere_activite": None}
    for partner in partners:
        partner["cles"] = [
            {**{k: v for k, v in key.items() if k != "partenaire_id"}, "statut": _key_status(key)}
            for key in keys if key["partenaire_id"] == partner["id"]
        ]
        row = stats.get(partner["nom"])
        partner["stats"] = {k: (row[k] if row else empty[k]) for k in empty}
    return partners


def _new_key(cursor, partner_id: int, label: str | None, expires: date | None, who: str | None) -> dict:
    key = security.generate_api_key()
    cursor.execute(
        "INSERT INTO partenaire_cle (partenaire_id, prefixe, cle_hash, libelle, expire_le, cree_par) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, prefixe, libelle, expire_le",
        (partner_id, key[:12], security.hash_api_key(key), label, expires, who),
    )
    created = cursor.fetchone()
    return {**created, "valeur": key}


def _admin() -> str | None:
    return (audit.context.get() or {}).get("actor")


def _expiry(data: dict) -> date | None:
    value = data.get("expire_le")
    if value in (None, ""):
        return None
    try:
        expires = date.fromisoformat(str(value))
    except ValueError:
        raise ApiError(422, "Date d'expiration invalide. Format attendu : AAAA-MM-JJ.")
    if expires < date.today():
        raise ApiError(422, "La date d'expiration ne peut pas être dans le passé.")
    return expires


@router.get(
    "/api/admin_partenaires",
    tags=["Intégrations"],
    summary="Lister les intégrations (sites externes)",
    description="Chaque intégration avec ses autorisations, ses clés (sans jamais la valeur de la clé) "
    "et son activité (appels, échecs, inscriptions marquées).",
    response_model=None,
    responses={200: {"model": d.PartnersListResponse}, **d.errors(401, 500, 503)},
)
def list_partners(db: Database = Depends(get_db)):
    try:
        partners = _list_partners(db)
    except psycopg.Error:
        logger.exception("Lecture des intégrations")
        raise ApiError(500, "Impossible de charger les intégrations.")
    return respond(
        200,
        {
            "status": "success",
            "data": partners,
            "champs_disponibles": list(STUDENT_FIELDS),
            "champs_par_defaut": list(DEFAULT_FIELDS),
        },
    )


@router.post(
    "/api/admin_partenaires",
    tags=["Intégrations"],
    summary="Créer une intégration et générer sa première clé d'API",
    description="La clé (`cle.valeur`) n'est renvoyée qu'une seule fois : elle ne pourra plus être "
    "consultée, seulement remplacée par une nouvelle clé.",
    response_model=None,
    responses={201: {"model": d.PartnerCreatedResponse}, **d.errors(401, 409, 422, 500, 503)},
    openapi_extra=d.request_body(d.PartnerCreateBody, "Fiche de l'intégration."),
)
def create_partner(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    fields = _clean_fields(data, creating=True)
    label = _text(data, "libelle_cle", 80, "Libellé de la clé") or "Clé initiale"
    expires = _expiry(data)
    audit.annotate(
        target_type="partenaire", target=fields["nom"],
        permission=fields["permission"], champs=fields["champs"],
    )
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO partenaire (nom, description, site_url, contact, permission, champs, "
                    "ips_autorisees, limite_par_minute, cree_par) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING id",
                    (
                        fields["nom"], fields.get("description"), fields.get("site_url"), fields.get("contact"),
                        fields["permission"], Jsonb(fields["champs"]),
                        Jsonb(fields["ips_autorisees"]),
                        fields.get("limite_par_minute"), _admin(),
                    ),
                )
                partner_id = cursor.fetchone()["id"]
                key = _new_key(cursor, partner_id, label, expires, _admin())
        partner = _list_partners(db, partner_id)[0]
    except psycopg.errors.UniqueViolation:
        raise ApiError(409, "Une intégration porte déjà ce nom.")
    except psycopg.Error:
        logger.exception("Création d'une intégration")
        raise ApiError(500, "Impossible de créer l'intégration.")
    audit.annotate(partenaire_id=partner_id, cle_prefixe=key["prefixe"])
    return respond(
        201,
        {
            "status": "success",
            "message": "Intégration créée. Copiez la clé d'API maintenant : elle ne sera plus affichée.",
            "partenaire": partner,
            "cle": key,
        },
    )


@router.post(
    "/api/admin_partenaires/modifier",
    tags=["Intégrations"],
    summary="Modifier les autorisations d'une intégration, l'activer ou la désactiver",
    description="Seuls les champs présents sont modifiés. Le nom n'est pas modifiable (il identifie "
    "l'intégration dans le journal d'activité). `actif: false` coupe immédiatement toutes ses clés.",
    response_model=None,
    responses={200: {"model": d.PartnerResponse}, **d.errors(401, 404, 422, 500, 503)},
    openapi_extra=d.request_body(d.PartnerUpdateBody, "Identifiant et champs à modifier."),
)
def update_partner(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    partner_id = positive_int(data.get("id"))
    if not partner_id:
        raise ApiError(422, "Identifiant d'intégration invalide.")
    fields = _clean_fields(data, creating=False)
    if "actif" in data:
        if not isinstance(data["actif"], bool):
            raise ApiError(422, "Le champ actif doit être vrai ou faux.")
        fields["actif"] = data["actif"]
    if not fields:
        raise ApiError(422, "Aucune modification fournie.")
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("SELECT nom, actif FROM partenaire WHERE id = %s FOR UPDATE", (partner_id,))
                current = cursor.fetchone()
                if not current:
                    raise ApiError(404, "Intégration introuvable.")
                audit.annotate(target_type="partenaire", target=current["nom"], champs=sorted(fields))
                if "actif" in fields and fields["actif"] != current["actif"]:
                    audit.annotate(action="PARTENAIRE_ACTIVE" if fields["actif"] else "PARTENAIRE_DESACTIVE")
                assignments = ", ".join(f"{k} = %s" + ("::jsonb" if k in ("champs", "ips_autorisees") else "") for k in fields)
                values = [
                    Jsonb(v) if k in ("champs", "ips_autorisees") else v
                    for k, v in fields.items()
                ]
                cursor.execute(
                    f"UPDATE partenaire SET {assignments}, updated_at = NOW() WHERE id = %s",
                    (*values, partner_id),
                )
        partner = _list_partners(db, partner_id)[0]
    except psycopg.Error:
        logger.exception("Modification d'une intégration")
        raise ApiError(500, "Impossible de modifier l'intégration.")
    return respond(200, {"status": "success", "message": "Intégration mise à jour.", "partenaire": partner})


@router.post(
    "/api/admin_partenaires/cles",
    tags=["Intégrations"],
    summary="Générer une nouvelle clé d'API ou révoquer une clé",
    description="`generer` crée une clé supplémentaire (rotation sans coupure : l'ancienne reste valide "
    f"jusqu'à sa révocation ; {MAX_ACTIVE_KEYS} clés actives au maximum) et renvoie sa valeur **une seule fois**. "
    "`revoquer` désactive définitivement la clé `cle_id`.",
    response_model=None,
    responses={200: {"model": d.PartnerKeyResponse}, **d.errors(401, 404, 422, 500, 503)},
    openapi_extra=d.request_body(d.PartnerKeyBody, "Intégration et action."),
)
def manage_partner_key(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    partner_id = positive_int(data.get("id"))
    action = data.get("action")
    if not partner_id or action not in ("generer", "revoquer"):
        raise ApiError(422, "Intégration ou action invalide.")
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("SELECT nom FROM partenaire WHERE id = %s FOR UPDATE", (partner_id,))
                partner = cursor.fetchone()
                if not partner:
                    raise ApiError(404, "Intégration introuvable.")
                audit.annotate(target_type="partenaire", target=partner["nom"])
                if action == "generer":
                    label = _text(data, "libelle", 80, "Libellé de la clé")
                    expires = _expiry(data)
                    cursor.execute(
                        "SELECT COUNT(*) AS n FROM partenaire_cle WHERE partenaire_id = %s AND revoquee_le IS NULL "
                        "AND (expire_le IS NULL OR expire_le >= CURRENT_DATE)", (partner_id,),
                    )
                    if cursor.fetchone()["n"] >= MAX_ACTIVE_KEYS:
                        raise ApiError(422, f"{MAX_ACTIVE_KEYS} clés actives au maximum : révoquez-en une d'abord.")
                    key = _new_key(cursor, partner_id, label, expires, _admin())
                    audit.annotate(cle_prefixe=key["prefixe"], expire_le=expires.isoformat() if expires else None)
                else:
                    key_id = positive_int(data.get("cle_id"))
                    if not key_id:
                        raise ApiError(422, "Clé à révoquer invalide.")
                    audit.annotate(action="PARTENAIRE_CLE_REVOQUEE")
                    cursor.execute(
                        "UPDATE partenaire_cle SET revoquee_le = NOW() "
                        "WHERE id = %s AND partenaire_id = %s AND revoquee_le IS NULL RETURNING prefixe",
                        (key_id, partner_id),
                    )
                    revoked = cursor.fetchone()
                    if not revoked:
                        raise ApiError(404, "Clé introuvable ou déjà révoquée.")
                    audit.annotate(cle_prefixe=revoked["prefixe"])
        result = _list_partners(db, partner_id)[0]
    except psycopg.Error:
        logger.exception("Gestion des clés d'une intégration")
        raise ApiError(500, "Impossible de traiter la demande.")
    payload = {"status": "success", "partenaire": result}
    if action == "generer":
        payload.update(
            message="Nouvelle clé générée. Copiez-la maintenant : elle ne sera plus affichée.", cle=key
        )
    else:
        payload["message"] = "Clé révoquée : elle ne fonctionne plus."
    return respond(200, payload)


@router.post(
    "/api/admin_partenaires/supprimer",
    tags=["Intégrations"],
    summary="Supprimer une intégration et toutes ses clés",
    description="Irréversible. L'historique de ses appels reste dans le journal d'activité. "
    "Pour une coupure réversible, désactiver l'intégration plutôt que la supprimer.",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(401, 404, 422, 500, 503)},
    openapi_extra=d.request_body(d.PartnerIdBody, "Identifiant de l'intégration."),
)
def delete_partner(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    partner_id = positive_int((json_object(body) or {}).get("id"))
    if not partner_id:
        raise ApiError(422, "Identifiant d'intégration invalide.")
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("DELETE FROM partenaire WHERE id = %s RETURNING nom", (partner_id,))
                deleted = cursor.fetchone()
                if not deleted:
                    raise ApiError(404, "Intégration introuvable.")
    except psycopg.Error:
        logger.exception("Suppression d'une intégration")
        raise ApiError(500, "Impossible de supprimer l'intégration.")
    audit.annotate(target_type="partenaire", target=deleted["nom"])
    return respond(200, {"status": "success", "message": "Intégration supprimée."})
