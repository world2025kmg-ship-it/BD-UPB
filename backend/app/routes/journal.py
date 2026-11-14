"""Journal d'activité : consultation, export, déconnexion et enregistrement des exports du navigateur."""
import csv
import io
import json
import logging
from datetime import date, timedelta
from math import ceil
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from .. import audit
from .. import docs as d
from ..auth import require_admin
from ..db import Database, get_db
from ..errors import ApiError, respond
from .common import json_body, json_object

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

STATUSES = ("SUCCES", "ECHEC")
ACTOR_TYPES = ("ADMIN", "PARTENAIRE", "PUBLIC", "DOCUMENTATION", "SYSTEME")
EXPORT_FORMATS = ("excel", "pdf")
EXPORT_TARGETS = ("etudiants", "pointage")
EXPORT_MAX_ROWS = 20000
COLUMNS = (
    "id, created_at, acteur_type, acteur, categorie, action, statut, cible_type, cible, "
    "message, details, code_http, ip, methode, chemin, user_agent"
)


def _day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, f"{label} invalide. Format attendu : AAAA-MM-JJ.")


def _filters(
    q: str, categorie: str, action: str, acteur_type: str, statut: str, date_debut: str, date_fin: str
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append(
            "(acteur ILIKE %s OR action ILIKE %s OR cible ILIKE %s OR message ILIKE %s OR ip ILIKE %s)"
        )
        params += [pattern] * 5
    if categorie:
        clauses.append("categorie = %s")
        params.append(categorie.upper())
    if action:
        clauses.append("action = %s")
        params.append(action.upper())
    if acteur_type:
        if acteur_type.upper() not in ACTOR_TYPES:
            raise ApiError(400, "Type d'acteur invalide.")
        clauses.append("acteur_type = %s")
        params.append(acteur_type.upper())
    if statut:
        if statut.upper() not in STATUSES:
            raise ApiError(400, "Statut invalide (SUCCES ou ECHEC).")
        clauses.append("statut = %s")
        params.append(statut.upper())
    start, end = _day(date_debut, "Date de début"), _day(date_fin, "Date de fin")
    if start:
        clauses.append("created_at >= %s")
        params.append(start)
    if end:
        clauses.append("created_at < %s")
        params.append(end + timedelta(days=1))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@router.get(
    "/api/admin_journal",
    tags=["Journal"],
    summary="Consulter le journal d'activité",
    description="Événements les plus récents en premier. Filtres combinables : recherche libre "
    "(acteur, action, cible, message, IP), catégorie, action, type d'acteur, statut et période.",
    response_model=None,
    responses={200: {"model": d.JournalResponse}, **d.errors(400, 401, 500, 503)},
)
def list_journal(
    page: int = Query(default=1, ge=1, description="Numéro de page."),
    per_page: int = Query(default=50, ge=1, le=200, description="Taille de page (200 maximum)."),
    q: str = Query(default="", description="Recherche libre (acteur, action, cible, message, IP)."),
    categorie: str = Query(default="", description="Ex. `AUTH`, `ETUDIANT`, `DEMANDE`, `POINTAGE`…"),
    action: str = Query(default="", description="Code d'action exact, ex. `ADMIN_CONNEXION`."),
    acteur_type: str = Query(default="", description="`ADMIN`, `PARTENAIRE`, `PUBLIC` ou `DOCUMENTATION`."),
    statut: str = Query(default="", description="`SUCCES` ou `ECHEC`."),
    date_debut: str = Query(default="", description="`AAAA-MM-JJ` (inclus)."),
    date_fin: str = Query(default="", description="`AAAA-MM-JJ` (inclus)."),
    db: Database = Depends(get_db),
):
    where, params = _filters(q, categorie, action, acteur_type, statut, date_debut, date_fin)
    try:
        with db.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total FROM journal_activite{where}", params)
            total = cursor.fetchone()["total"]
            cursor.execute(
                f"SELECT {COLUMNS} FROM journal_activite{where} "
                "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                (*params, per_page, (page - 1) * per_page),
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS dernieres_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'
                                        AND statut = 'ECHEC') AS echecs_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'
                                        AND action IN ('ADMIN_CONNEXION', 'ACCES_REFUSE')
                                        AND statut = 'ECHEC') AS acces_refuses_24h
                FROM journal_activite
                """
            )
            stats = cursor.fetchone()
    except psycopg.Error:
        logger.exception("Lecture du journal d'activité")
        raise ApiError(500, "Impossible de charger le journal d'activité.")
    return respond(
        200,
        {
            "status": "success",
            "data": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": ceil(total / per_page),
            "stats": stats,
            "actions": audit.ACTIONS,
            "categories": audit.CATEGORIES,
        },
    )


def _safe(value: Any) -> str:
    """Neutralise l'injection de formules dans Excel/LibreOffice."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get(
    "/api/admin_journal/export",
    tags=["Journal"],
    summary="Exporter le journal en CSV",
    description="Mêmes filtres que la consultation ; 20 000 lignes au maximum. Fichier CSV UTF-8 "
    "(séparateur `;`). L'export est lui-même enregistré dans le journal.",
    response_class=Response,
    response_model=None,
    responses={200: {"content": {"text/csv": {}}, "description": "Fichier CSV."}, **d.errors(400, 401, 500, 503)},
)
def export_journal(
    q: str = Query(default=""),
    categorie: str = Query(default=""),
    action: str = Query(default=""),
    acteur_type: str = Query(default=""),
    statut: str = Query(default=""),
    date_debut: str = Query(default=""),
    date_fin: str = Query(default=""),
    db: Database = Depends(get_db),
):
    where, params = _filters(q, categorie, action, acteur_type, statut, date_debut, date_fin)
    try:
        with db.cursor() as cursor:
            cursor.execute(
                f"SELECT {COLUMNS} FROM journal_activite{where} "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (*params, EXPORT_MAX_ROWS),
            )
            rows = cursor.fetchall()
    except psycopg.Error:
        logger.exception("Export du journal d'activité")
        raise ApiError(500, "Impossible d'exporter le journal d'activité.")

    audit.annotate(
        lignes=len(rows),
        filtres={k: v for k, v in dict(q=q, categorie=categorie, action=action, acteur_type=acteur_type,
                                       statut=statut, date_debut=date_debut, date_fin=date_fin).items() if v},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Date", "Type d'acteur", "Acteur", "Catégorie", "Action", "Statut", "Type de cible", "Cible",
        "Message", "Code HTTP", "IP", "Méthode", "Chemin", "Détails",
    ])
    for row in rows:
        writer.writerow([
            _safe(row["created_at"].strftime("%Y-%m-%d %H:%M:%S")), _safe(row["acteur_type"]),
            _safe(row["acteur"]), _safe(row["categorie"]), _safe(row["action"]), _safe(row["statut"]),
            _safe(row["cible_type"]), _safe(row["cible"]), _safe(row["message"]), _safe(row["code_http"]),
            _safe(row["ip"]), _safe(row["methode"]), _safe(row["chemin"]),
            _safe(json.dumps(row["details"], ensure_ascii=False) if row["details"] else ""),
        ])
    filename = f"journal_activite_{date.today().isoformat()}.csv"
    return Response(
        "﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/api/admin_logout",
    tags=["Journal"],
    summary="Se déconnecter (enregistre la déconnexion)",
    description="Les sessions étant sans état, la déconnexion consiste à oublier le jeton côté "
    "navigateur ; cet appel permet de la tracer dans le journal.",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(401, 503)},
)
def admin_logout():
    return respond(200, {"status": "success", "message": "Déconnexion enregistrée."})


@router.post(
    "/api/admin_export",
    tags=["Journal"],
    summary="Enregistrer un export réalisé dans le navigateur",
    description="Les exports Excel/PDF de la liste des étudiants et du pointage sont générés dans "
    "le navigateur : l'interface signale chaque export pour qu'il soit tracé.",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(400, 401, 503)},
    openapi_extra=d.request_body(d.ExportLogBody, "Format et contenu exporté."),
)
def log_export(body: Any = Depends(json_body)):
    data = json_object(body) or {}
    export_format, target = data.get("format"), data.get("cible")
    if export_format not in EXPORT_FORMATS or target not in EXPORT_TARGETS:
        raise ApiError(400, "Export invalide.")
    audit.annotate(target_type="export", target=target, format=export_format)
    lines = data.get("lignes")
    if isinstance(lines, int) and not isinstance(lines, bool) and 0 <= lines <= 1_000_000:
        audit.annotate(lignes=lines)
    return respond(200, {"status": "success", "message": "Export enregistré."})
