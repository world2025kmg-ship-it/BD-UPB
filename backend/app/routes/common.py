from typing import Any

from fastapi import Request


async def json_body(request: Request) -> Any:
    """Corps JSON brut (None si absent ou invalide) ; la validation se fait dans les routes."""
    try:
        return await request.json()
    except Exception:
        return None


def json_object(data: Any) -> dict | None:
    return data if isinstance(data, dict) else None


def positive_int(value: Any) -> int | None:
    """Entier > 0 (accepte aussi une chaîne numérique)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None
