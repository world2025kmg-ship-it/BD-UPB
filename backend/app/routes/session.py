"""Connexion administrateur par identifiant + mot de passe."""
import logging
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request

from .. import audit
from .. import docs as d
from .. import security
from ..auth import NOT_CONFIGURED
from ..db import Database, get_db
from ..errors import ApiError, respond
from .common import json_body, json_object

logger = logging.getLogger(__name__)
router = APIRouter()


def check_credentials(db: Database, identifiant: str, password: str) -> str | None:
    """Identifiant canonique si l'identifiant et le mot de passe sont valides et le compte actif."""
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT identifiant, mot_de_passe_hash, actif FROM administrateur "
            "WHERE LOWER(identifiant) = LOWER(%s)",
            (identifiant,),
        )
        row = cursor.fetchone()
    valid = security.verify_password(password, row["mot_de_passe_hash"] if row else security.DUMMY_HASH)
    return row["identifiant"] if row and row["actif"] and valid else None


@router.post(
    "/api/admin_login",
    tags=["Administration"],
    summary="Se connecter (identifiant + mot de passe)",
    description="Renvoie un jeton de session à envoyer ensuite en `Authorization: Bearer <jeton>` "
    "sur toutes les routes d'administration. Après 5 échecs, l'accès est bloqué 15 minutes.",
    response_model=None,
    responses={
        200: {"model": d.LoginResponse, "description": "Connexion réussie."},
        429: {"model": d.ErrorResponse, "description": "Trop de tentatives échouées."},
        **d.errors(400, 401, 500, 503),
    },
    openapi_extra=d.request_body(d.LoginBody, "Identifiant et mot de passe de l'administrateur."),
)
def admin_login(request: Request, body: Any = Depends(json_body), db: Database = Depends(get_db)):
    if security.secret_key() is None:
        raise ApiError(503, NOT_CONFIGURED)
    data = json_object(body) or {}
    identifiant, password = data.get("identifiant"), data.get("password")
    if (
        not isinstance(identifiant, str)
        or not identifiant.strip()
        or not isinstance(password, str)
        or not password
    ):
        raise ApiError(400, "Identifiant et mot de passe requis.")
    identifiant = identifiant.strip()
    audit.set_actor("ADMIN", identifiant)  # identifiant présenté (même en cas d'échec)

    client = request.client.host if request.client else "inconnu"
    keys = (f"ip:{client}", f"id:{identifiant.lower()}")
    wait = security.login_throttle.retry_after(*keys)
    if wait:
        raise ApiError(
            429, f"Trop de tentatives échouées. Réessayez dans {max(1, wait // 60)} minute(s)."
        )

    try:
        canonical = check_credentials(db, identifiant, password)
        if canonical:
            with db.transaction():
                with db.cursor() as cursor:
                    cursor.execute(
                        "UPDATE administrateur SET derniere_connexion = NOW() "
                        "WHERE LOWER(identifiant) = LOWER(%s)",
                        (canonical,),
                    )
    except psycopg.Error:
        logger.exception("Connexion administrateur UPB")
        raise ApiError(500, "Erreur de base de données.")

    if not canonical:
        security.login_throttle.fail(*keys)
        raise ApiError(401, "Identifiant ou mot de passe incorrect.")
    security.login_throttle.reset(*keys)
    audit.set_actor("ADMIN", canonical)
    return respond(
        200,
        {
            "status": "success",
            "message": "Connexion réussie.",
            "token": security.create_session_token(canonical),
            "expires_in": security.session_ttl(),
            "identifiant": canonical,
        },
    )
