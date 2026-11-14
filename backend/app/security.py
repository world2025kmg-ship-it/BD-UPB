"""Mots de passe (scrypt), jetons de session signés (HMAC-SHA256) et limitation des essais.

Aucune dépendance externe : bibliothèque standard uniquement.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 10


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --------------------------------------------------------------------------- mots de passe
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


# Hachage factice : une tentative sur un identifiant inconnu coûte autant de temps.
DUMMY_HASH = hash_password("mot-de-passe-factice")


# --------------------------------------------------------------------------- jetons de session
def secret_key() -> bytes | None:
    value = os.getenv("UPB_SECRET_KEY", "")
    return value.encode() if len(value) >= MIN_SECRET_LENGTH else None


def session_ttl() -> int:
    return int(os.getenv("UPB_SESSION_HOURS", "8")) * 3600


def create_session_token(identifiant: str, ttl: int | None = None) -> str:
    key = secret_key()
    if key is None:
        raise RuntimeError("UPB_SECRET_KEY non configurée.")
    payload = _b64(
        json.dumps(
            {"sub": identifiant, "exp": int(time.time()) + (session_ttl() if ttl is None else ttl)},
            separators=(",", ":"),
        ).encode()
    )
    signature = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"v1.{payload}.{signature}"


def read_session_token(token: str) -> str | None:
    """Identifiant de l'administrateur si le jeton est authentique et non expiré."""
    key = secret_key()
    if key is None:
        return None
    try:
        version, payload, signature = token.split(".")
        expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
        if version != "v1" or not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(_unb64(payload))
        if int(claims["exp"]) < time.time():
            return None
        return str(claims["sub"])
    except (ValueError, KeyError, TypeError):
        return None


# --------------------------------------------------------------------------- clés d'API partenaires
def generate_api_key() -> str:
    return "upb_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Empreinte SHA-256 (les clés sont aléatoires et longues : pas besoin de scrypt)."""
    return hashlib.sha256(key.encode()).hexdigest()


# --------------------------------------------------------------------------- limitation des essais
class LoginThrottle:
    """Bloque temporairement après trop d'échecs (en mémoire, par processus)."""

    def __init__(self, max_failures: int = 5, window: int = 15 * 60):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = recent
        return recent

    def retry_after(self, *keys: str) -> int:
        """Secondes à attendre (0 si l'essai est autorisé)."""
        with self._lock:
            wait = 0
            for key in keys:
                recent = self._recent(key)
                if len(recent) >= self.max_failures:
                    wait = max(wait, int(self.window - (time.time() - recent[0])) + 1)
            return wait

    def fail(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._recent(key).append(time.time())

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


login_throttle = LoginThrottle()
