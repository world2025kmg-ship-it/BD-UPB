"""Application FastAPI : API JSON (/api/...) + service du frontend statique."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from . import audit, config, db
from .netinfo import client_ip
from .docs_access import register_docs_routes
from .errors import UpbJSONResponse, register_error_handlers
from .routes import register_routers

logger = logging.getLogger(__name__)

DESCRIPTION = """
API JSON du concours **Génie UPB**.

## Conventions
* Corps JSON UTF-8 (`Content-Type: application/json`), sauf l'import CSV (`multipart/form-data`).
* Toute réponse a la forme `{"status": "success" | "error", "message": "..."}`.
* Dates : `AAAA-MM-JJ` (la saisie `JJ/MM/AAAA` et `JJ-MM-AAAA` est acceptée pour les dates de naissance).
* Horodatages : `AAAA-MM-JJ HH:MM:SS`.
* CORS ouvert à toutes les origines ; réponses `/api/*` non mises en cache.

## Authentification
* **Portail étudiant** (`/api/verifier`, `/api/demander_modification`,
  `/api/demander_creation`) : aucun jeton.
* **Administration et pointage** : connexion par **identifiant + mot de passe**
  (`POST /api/admin_login`), qui renvoie un jeton de session à envoyer en
  `Authorization: Bearer <jeton>` (bouton **Authorize** ci-dessus). `401` si le jeton
  est absent, invalide ou expiré ; `503` si le serveur n'est pas configuré.
* **Site officiel d'inscription au concours** : clé d'API partenaire en-tête `X-API-Key`
  (`/api/partenaire/verifier`), qui vérifie l'étudiant et le marque inscrit.
* Cette documentation est elle-même protégée (HTTP Basic).
* Toutes les actions sont tracées dans le **journal d'activité** (tag « Journal »).

## Intégrer l'API sur un site externe
Voir le guide `docs/GUIDE_INTEGRATION.md` du dépôt.
"""

TAGS = [
    {"name": "Portail étudiant", "description": "Routes publiques (sans jeton)."},
    {"name": "Administration", "description": "Routes réservées aux administrateurs (jeton Bearer)."},
    {"name": "Journal", "description": "Journal d'activité : traçabilité des actions (jeton Bearer)."},
    {"name": "Intégrations", "description": "Gestion des sites externes : autorisations et clés d'API (jeton Bearer)."},
    {"name": "Partenaires", "description": "Sites partenaires autorisés (clé d'API `X-API-Key`)."},
    {"name": "Pointage", "description": "Présences quotidiennes par QR code (jeton Bearer)."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        db.open_pool()
    except RuntimeError:
        logger.exception("Pool de connexions non ouvert (DATABASE_URL manquant ?)")
    yield
    db.close_pool()


def cors_origins() -> list[str]:
    """Origines autorisées à appeler l'API depuis un navigateur (UPB_CORS_ORIGINS, séparées par des
    virgules, ex. https://mon-site.netlify.app). Vide = toutes les origines (développement)."""
    origins = [o.strip().rstrip("/") for o in os.getenv("UPB_CORS_ORIGINS", "").split(",") if o.strip()]
    return origins or ["*"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="API Concours Génie UPB",
        version="1.0.0",
        summary="Vérification d'identité des étudiants, inscription au concours, administration et pointage.",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        docs_url=None,  # /docs, /redoc et /openapi.json sont protégés (docs_access.py)
        redoc_url=None,
        openapi_url=None,
        default_response_class=UpbJSONResponse,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def audit_trail(request: Request, call_next):
        """Contexte d'audit de la requête ; journalise l'événement une fois la réponse produite."""
        token = audit.begin(
            client_ip(request),
            request.headers.get("user-agent"),
            request.method,
            request.url.path,
        )
        try:
            response = await call_next(request)
            if audit.enabled():
                await run_in_threadpool(audit.finish_request, response.status_code)
            return response
        finally:
            audit.end(token)

    @app.api_route("/api/sante", methods=["GET", "HEAD"], include_in_schema=False)
    def health(base: bool = False):
        """Point de santé pour la supervision (UptimeRobot). Sans paramètre, il ne touche PAS la base :
        la supervision peut ainsi tourner sans empêcher Neon de se mettre en veille.
        `?base=1` vérifie aussi la connexion à la base (diagnostic manuel)."""
        if base:
            try:
                with db.audit_connection() as connection:
                    connection.execute("SELECT 1")
            except Exception:  # noqa: BLE001
                return UpbJSONResponse({"status": "error", "message": "Base de données injoignable."}, status_code=503)
        return UpbJSONResponse({"status": "ok"})

    register_error_handlers(app)
    register_routers(app)
    register_docs_routes(app)

    # Le frontend (dossier ../frontend) est servi à la racine, après les routes /api.
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
