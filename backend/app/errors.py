"""Réponses JSON normalisées {status, message} et gestion d'erreurs."""
import datetime as dt
import decimal
import json
import logging
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import audit

logger = logging.getLogger(__name__)


def _default(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Type non sérialisable : {type(value).__name__}")


class UpbJSONResponse(JSONResponse):
    """JSON UTF-8 non échappé ; dates 'AAAA-MM-JJ', horodatages 'AAAA-MM-JJ HH:MM:SS'."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_default).encode("utf-8")


def respond(status_code: int, payload: dict) -> UpbJSONResponse:
    return UpbJSONResponse(payload, status_code=status_code)


class ApiError(Exception):
    """Erreur métier convertie en {"status": "error", "message": ...}."""

    def __init__(self, status_code: int, message: str, **extra: Any):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError):
        extra_errors = error.extra.get("errors")
        audit.annotate(message=error.message, erreurs=extra_errors[:10] if extra_errors else None)
        return respond(
            error.status_code,
            {"status": "error", "message": error.message, **error.extra},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, error: StarletteHTTPException):
        messages = {404: "Ressource introuvable.", 405: "Méthode non autorisée."}
        message = messages.get(error.status_code) or str(error.detail)
        audit.annotate(message=message)
        response = respond(error.status_code, {"status": "error", "message": message})
        for name, value in (error.headers or {}).items():
            response.headers[name] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        return respond(400, {"status": "error", "message": "Requête invalide."})

    @app.exception_handler(psycopg.Error)
    async def database_error(_request: Request, error: psycopg.Error):
        audit.annotate(message="Erreur de base de données.")
        logger.error(
            "Erreur de base de données non gérée",
            exc_info=(type(error), error, error.__traceback__),
        )
        return respond(500, {"status": "error", "message": "Erreur de base de données."})
