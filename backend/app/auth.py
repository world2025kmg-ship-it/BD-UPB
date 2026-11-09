"""Authentification des routes d'administration : jeton de session obtenu par /api/admin_login."""
from fastapi import Depends
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from . import audit, security
from .db import Database, get_db
from .errors import ApiError

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AdminBearer",
    description="Jeton de session administrateur, obtenu avec `POST /api/admin_login` "
    "(identifiant + mot de passe). Valable quelques heures.",
)

NOT_CONFIGURED = (
    "Accès administrateur non configuré. "
    f"Définissez UPB_SECRET_KEY ({security.MIN_SECRET_LENGTH} caractères minimum)."
)


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Retourne l'identifiant de l'administrateur connecté."""
    if security.secret_key() is None:
        raise ApiError(503, NOT_CONFIGURED)
    identifiant = security.read_session_token(credentials.credentials.strip()) if credentials else None
    if not identifiant:
        raise ApiError(401, "Accès réservé aux administrateurs.")
    audit.set_actor("ADMIN", identifiant)
    return identifiant


api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="PartnerApiKey",
    description="Clé d'API remise à un site partenaire (ex. site officiel d'inscription au concours).",
)


def require_partner(
    api_key: str | None = Depends(api_key_scheme), db: Database = Depends(get_db)
) -> str:
    """Retourne le nom du partenaire dont la clé d'API est valide et active."""
    key = (api_key or "").strip()
    if not key:
        raise ApiError(401, "Clé d'API partenaire absente ou invalide.")
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT nom, actif FROM partenaire WHERE cle_hash = %s", (security.hash_api_key(key),)
        )
        row = cursor.fetchone()
    if not row or not row["actif"]:
        raise ApiError(401, "Clé d'API partenaire absente ou invalide.")
    with db.transaction():
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE partenaire SET derniere_utilisation = NOW() WHERE cle_hash = %s",
                (security.hash_api_key(key),),
            )
    audit.set_actor("PARTENAIRE", row["nom"])
    return row["nom"]
