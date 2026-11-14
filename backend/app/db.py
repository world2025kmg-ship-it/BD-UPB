"""Connexion PostgreSQL (Neon) : pool de connexions partagé + dépendance FastAPI."""
import logging
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def open_pool() -> None:
    """Ouvre le pool sans bloquer : une base injoignable n'empêche pas le démarrage."""
    global _pool
    if _pool is not None:
        return
    _pool = ConnectionPool(
        conninfo=config.database_url(),
        min_size=1,
        max_size=5,
        open=False,
        check=ConnectionPool.check_connection,  # Neon coupe les connexions inactives
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": config.connect_timeout(),
            "prepare_threshold": None,  # compatible avec le pooler Neon (PgBouncer)
        },
    )
    _pool.open(wait=False)


_audit_pool: ConnectionPool | None = None


def audit_connection():
    """Connexion issue d'un petit pool DÉDIÉ au journal d'activité (jamais le pool des requêtes :
    pas d'interblocage possible, et l'écriture n'est pas liée à la transaction de la requête)."""
    global _audit_pool
    if _audit_pool is None:
        _audit_pool = ConnectionPool(
            conninfo=config.database_url(),
            min_size=0,
            max_size=2,
            open=False,
            check=ConnectionPool.check_connection,
            kwargs={"connect_timeout": config.connect_timeout(), "prepare_threshold": None},
        )
        _audit_pool.open(wait=False)
    return _audit_pool.connection(timeout=config.connect_timeout())


def close_pool() -> None:
    global _pool, _audit_pool
    if _pool is not None:
        _pool.close()
        _pool = None
    if _audit_pool is not None:
        _audit_pool.close()
        _audit_pool = None


class Database:
    """Accès paresseux à une connexion du pool : rien n'est emprunté tant que la
    route n'interroge pas la base (les validations d'entrée n'en dépendent donc pas)."""

    def __init__(self) -> None:
        self._manager = None
        self._connection: psycopg.Connection | None = None

    def _get(self) -> psycopg.Connection:
        if self._connection is None:
            if _pool is None:
                open_pool()
            assert _pool is not None
            self._manager = _pool.connection(timeout=config.connect_timeout())
            self._connection = self._manager.__enter__()
        return self._connection

    def cursor(self, *args, **kwargs):
        return self._get().cursor(*args, **kwargs)

    def transaction(self):
        return self._get().transaction()

    def release(self) -> None:
        if self._manager is not None:
            self._manager.__exit__(None, None, None)
            self._manager = None
            self._connection = None


def get_db() -> Iterator[Database]:
    database = Database()
    try:
        yield database
    finally:
        database.release()
