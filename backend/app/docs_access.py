"""Documentation interactive (/docs, /redoc, /openapi.json) protégée par autorisation.

Accès par authentification HTTP Basic (le navigateur affiche sa fenêtre de connexion) :
  * mot de passe UPB_DOCS_PASSWORD (10 caractères minimum), nom d'utilisateur libre :
    à remettre aux intégrateurs autorisés par l'administrateur ;
  * ou identifiant + mot de passe d'un compte administrateur.
Le mot de passe de la documentation ne donne aucun droit sur l'API d'administration.
"""
import hmac
import os

import psycopg
from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.exceptions import HTTPException

from . import audit
from .db import Database, get_db
from .routes.session import check_credentials

basic = HTTPBasic(auto_error=False, realm="Documentation API UPB")


def require_docs_access(
    credentials: HTTPBasicCredentials | None = Depends(basic), db: Database = Depends(get_db)
) -> None:
    if credentials:
        audit.set_actor("DOCUMENTATION", credentials.username[:80])
    if credentials and credentials.password:
        docs_password = os.getenv("UPB_DOCS_PASSWORD", "")
        if len(docs_password) >= 10 and hmac.compare_digest(
            credentials.password.encode(), docs_password.encode()
        ):
            return
        try:
            if check_credentials(db, credentials.username, credentials.password):
                return
        except psycopg.Error:
            pass  # base indisponible : accès refusé plutôt qu'erreur détaillée
    raise HTTPException(
        401,
        "Documentation réservée : autorisation de l'administrateur requise.",
        headers={"WWW-Authenticate": 'Basic realm="Documentation API UPB"'},
    )


def register_docs_routes(app: FastAPI) -> None:
    guard = [Depends(require_docs_access)]

    @app.get("/openapi.json", include_in_schema=False, dependencies=guard)
    def openapi_json():
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=guard)
    def swagger_ui():
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — Swagger UI",
            swagger_ui_parameters={"persistAuthorization": True},
        )

    @app.get("/redoc", include_in_schema=False, dependencies=guard)
    def redoc():
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} — ReDoc")
