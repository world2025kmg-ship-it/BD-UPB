"""Journal d'activité : traçabilité de toutes les actions menées sur et à travers le système.

Fonctionnement
  * Un middleware (main.py) ouvre un *contexte d'audit* par requête (IP, méthode, chemin…) et,
    une fois la réponse produite, enregistre UN événement pour les routes listées dans
    ROUTE_EVENTS (le statut vient du code HTTP). Toute réponse 401 sur une route /api non
    listée est aussi journalisée (tentative d'accès refusée).
  * Les routes enrichissent l'événement avec `annotate(...)` (cible, détails, action précise).
  * Les gestionnaires d'erreurs y ajoutent le message d'erreur ; l'authentification y inscrit
    l'acteur (administrateur ou partenaire).
  * `log(...)` enregistre un événement supplémentaire (ex. chaque demande d'un traitement groupé).

Garanties
  * L'écriture utilise un petit pool de connexions dédié, indépendant de celui des requêtes :
    elle ne peut pas être annulée par la transaction de la requête ni provoquer d'interblocage.
  * Un échec d'écriture est consigné dans les logs applicatifs mais ne fait jamais échouer
    l'action de l'utilisateur.
  * Jamais de mot de passe, jeton ou clé d'API ; pour les modifications de dossier, seuls les
    NOMS des champs sont conservés, pas leurs valeurs.
"""
import contextvars
import json
import logging
import os
from typing import Any

from . import db

logger = logging.getLogger(__name__)

SUCCES, ECHEC = "SUCCES", "ECHEC"

# (méthode, chemin) -> (action, catégorie, type d'acteur par défaut, seulement_les_échecs)
ROUTE_EVENTS: dict[tuple[str, str], tuple[str, str, str, bool]] = {
    ("POST", "/api/admin_login"): ("ADMIN_CONNEXION", "AUTH", "ADMIN", False),
    ("POST", "/api/admin_logout"): ("ADMIN_DECONNEXION", "AUTH", "ADMIN", False),
    ("POST", "/api/verifier"): ("ETUDIANT_VERIFIE", "ETUDIANT", "PUBLIC", False),
    ("POST", "/api/demander_modification"): ("DEMANDE_CORRECTION_SOUMISE", "DEMANDE", "PUBLIC", False),
    ("POST", "/api/demander_creation"): ("DEMANDE_CREATION_SOUMISE", "DEMANDE", "PUBLIC", False),
    ("POST", "/api/partenaire/verifier"): ("INSCRIPTION_CONCOURS", "INSCRIPTION", "PARTENAIRE", False),
    ("POST", "/api/update_etudiant"): ("ETUDIANT_MODIFIE", "ETUDIANT", "ADMIN", False),
    ("POST", "/api/update_telephone"): ("ETUDIANT_TELEPHONE_MODIFIE", "ETUDIANT", "ADMIN", False),
    ("POST", "/api/admin_modifications"): ("DEMANDE_CORRECTION_TRAITEE", "DEMANDE", "ADMIN", False),
    ("POST", "/api/admin_creations"): ("DEMANDE_CREATION_TRAITEE", "DEMANDE", "ADMIN", False),
    ("POST", "/api/admin_approve_all"): ("DEMANDES_APPROUVEES_EN_MASSE", "DEMANDE", "ADMIN", False),
    ("POST", "/api/import_etudiants"): ("IMPORT_ETUDIANTS_CSV", "IMPORT", "ADMIN", False),
    ("POST", "/api/pointage"): ("POINTAGE_ENREGISTRE", "POINTAGE", "ADMIN", False),
    ("POST", "/api/admin_export"): ("EXPORT_DONNEES", "EXPORT", "ADMIN", False),
    ("GET", "/api/admin_journal/export"): ("JOURNAL_EXPORTE", "JOURNAL", "ADMIN", False),
    ("GET", "/docs"): ("DOCUMENTATION_CONSULTEE", "DOCUMENTATION", "DOCUMENTATION", False),
    ("GET", "/redoc"): ("DOCUMENTATION_CONSULTEE", "DOCUMENTATION", "DOCUMENTATION", False),
    ("GET", "/openapi.json"): ("DOCUMENTATION_CONSULTEE", "DOCUMENTATION", "DOCUMENTATION", True),
}

# Libellés lisibles (utilisés par l'interface et l'export)
CATEGORIES = [
    "AUTH", "ETUDIANT", "DEMANDE", "INSCRIPTION", "POINTAGE", "IMPORT", "EXPORT", "JOURNAL", "DOCUMENTATION",
]
ACTIONS = sorted(
    {a for a, *_ in ROUTE_EVENTS.values()}
    | {
        "ACCES_REFUSE",
        "DEMANDE_CORRECTION_APPROUVEE", "DEMANDE_CORRECTION_REFUSEE",
        "DEMANDE_CREATION_APPROUVEE", "DEMANDE_CREATION_REFUSEE",
    }
)

context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("audit_context", default=None)


def enabled() -> bool:
    return os.getenv("UPB_AUDIT_ENABLED", "1") != "0"


def begin(ip: str | None, user_agent: str | None, method: str, path: str) -> contextvars.Token:
    return context.set(
        {
            "ip": ip, "user_agent": user_agent, "method": method, "path": path,
            "actor_type": None, "actor": None,
            "action": None, "target_type": None, "target": None, "message": None, "details": {},
        }
    )


def end(token: contextvars.Token) -> None:
    context.reset(token)


def set_actor(actor_type: str, actor: str | None) -> None:
    ctx = context.get()
    if ctx is not None:
        ctx["actor_type"], ctx["actor"] = actor_type, actor


def annotate(
    *,
    action: str | None = None,
    target_type: str | None = None,
    target: str | None = None,
    message: str | None = None,
    **details: Any,
) -> None:
    """Complète l'événement de la requête en cours (les détails s'additionnent)."""
    ctx = context.get()
    if ctx is None:
        return
    if action:
        ctx["action"] = action
    if target_type:
        ctx["target_type"] = target_type
    if target:
        ctx["target"] = target
    if message:
        ctx["message"] = message
    ctx["details"].update({k: v for k, v in details.items() if v is not None})


def _clip(value: Any, size: int) -> str | None:
    return None if value is None else str(value)[:size]


def _write(row: dict) -> None:
    sql = (
        "INSERT INTO journal_activite (acteur_type, acteur, categorie, action, statut, cible_type, cible, "
        "message, details, code_http, ip, methode, chemin, user_agent) "
        "VALUES (%(actor_type)s, %(actor)s, %(category)s, %(action)s, %(status)s, %(target_type)s, %(target)s, "
        "%(message)s, %(details)s::jsonb, %(http)s, %(ip)s, %(method)s, %(path)s, %(user_agent)s)"
    )
    with db.audit_connection() as connection:
        connection.execute(sql, row)


def log(
    action: str,
    category: str,
    *,
    status: str = SUCCES,
    actor_type: str | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target: str | None = None,
    message: str | None = None,
    details: dict | None = None,
    http: int | None = None,
) -> None:
    """Enregistre un événement. Ne lève jamais d'exception."""
    if not enabled():
        return
    ctx = context.get() or {}
    row = {
        "actor_type": actor_type or ctx.get("actor_type") or "PUBLIC",
        "actor": _clip(actor if actor is not None else ctx.get("actor"), 80),
        "category": category,
        "action": action,
        "status": status,
        "target_type": _clip(target_type, 30),
        "target": _clip(target, 120),
        "message": _clip(message, 500),
        "details": json.dumps(details, ensure_ascii=False, default=str) if details else None,
        "http": http,
        "ip": _clip(ctx.get("ip"), 45),
        "method": _clip(ctx.get("method"), 8),
        "path": _clip(ctx.get("path"), 200),
        "user_agent": _clip(ctx.get("user_agent"), 200),
    }
    try:
        _write(row)
    except Exception:  # noqa: BLE001 - la traçabilité ne doit jamais casser l'action
        logger.exception("Échec d'écriture du journal d'activité (%s)", action)


def finish_request(status_code: int) -> None:
    """Appelé par le middleware après la réponse : journalise l'événement de la requête."""
    ctx = context.get()
    if ctx is None or not enabled():
        return
    key = (ctx["method"], ctx["path"])
    entry = ROUTE_EVENTS.get(key)
    ok = status_code < 400
    if entry is None:
        if status_code == 401 and ctx["path"].startswith("/api/"):
            log(
                "ACCES_REFUSE", "AUTH", status=ECHEC, actor_type="ADMIN" if ctx["actor_type"] is None else None,
                message=ctx["message"] or "Authentification refusée.", http=status_code,
                details={"chemin": ctx["path"], "methode": ctx["method"]},
            )
        return
    action, category, default_actor, failures_only = entry
    if failures_only and ok:
        return
    log(
        ctx["action"] or action,
        category,
        status=SUCCES if ok else ECHEC,
        actor_type=ctx["actor_type"] or default_actor,
        target_type=ctx["target_type"],
        target=ctx["target"],
        message=ctx["message"] if not ok else None,
        details=ctx["details"] or None,
        http=status_code,
    )
