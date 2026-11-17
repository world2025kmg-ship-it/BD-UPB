"""Authentification des routes d'administration (jeton de session) et des sites partenaires (clé d'API)."""
from datetime import date

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


INVALID_PARTNER_KEY = "Clé d'API partenaire absente ou invalide."


def _reject_partner(status: int, message: str, reason: str, partner: str | None = None) -> None:
    """Refuse l'appel ; le motif précis n'est donné qu'au journal (pas à l'appelant)."""
    if partner:
        audit.set_actor("PARTENAIRE", partner)
    audit.annotate(raison=reason)
    raise ApiError(status, message)


def require_partner(
    api_key: str | None = Depends(api_key_scheme), db: Database = Depends(get_db)
) -> dict:
    """Contrôle la clé d'API d'un site partenaire.

    Vérifie dans l'ordre : clé connue, non révoquée, non expirée, partenaire actif, adresse IP
    autorisée, quota par minute. Retourne {id, nom, permission, champs}.
    """
    key = (api_key or "").strip()
    if not key:
        _reject_partner(401, INVALID_PARTNER_KEY, "cle_absente")
    key_hash = security.hash_api_key(key)
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT k.id AS cle_id, k.expire_le, k.revoquee_le,
                   p.id, p.nom, p.actif, p.permission, p.champs, p.ips_autorisees, p.limite_par_minute
            FROM partenaire_cle k JOIN partenaire p ON p.id = k.partenaire_id
            WHERE k.cle_hash = %s
            """,
            (key_hash,),
        )
        row = cursor.fetchone()
    if not row:
        _reject_partner(401, INVALID_PARTNER_KEY, "cle_inconnue")
    name = row["nom"]
    if row["revoquee_le"] is not None:
        _reject_partner(401, INVALID_PARTNER_KEY, "cle_revoquee", name)
    if row["expire_le"] is not None and row["expire_le"] < date.today():
        _reject_partner(401, INVALID_PARTNER_KEY, "cle_expiree", name)
    if not row["actif"]:
        _reject_partner(401, INVALID_PARTNER_KEY, "partenaire_desactive", name)
    client_ip = (audit.context.get() or {}).get("ip")
    if not security.ip_allowed(client_ip, row["ips_autorisees"] or []):
        _reject_partner(403, "Adresse IP non autorisée pour cette clé.", "ip_non_autorisee", name)
    limit = row["limite_par_minute"]
    wait = security.partner_rate.hit(f"partner:{row['id']}", limit)
    if wait:
        _reject_partner(
            429, f"Quota dépassé ({limit} appels par minute). Réessayez dans {wait} s.", "quota_depasse", name
        )
    with db.transaction():
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE partenaire_cle SET derniere_utilisation = NOW(), nb_utilisations = nb_utilisations + 1 "
                "WHERE id = %s",
                (row["cle_id"],),
            )
            cursor.execute("UPDATE partenaire SET derniere_utilisation = NOW() WHERE id = %s", (row["id"],))
    audit.set_actor("PARTENAIRE", name)
    return {"id": row["id"], "nom": name, "permission": row["permission"], "champs": row["champs"] or []}
