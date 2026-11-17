"""Adresse IP du client, derrière 0, 1 ou plusieurs proxys.

Variable UPB_TRUSTED_PROXY_HOPS = nombre de proxys placés devant l'application (0 en local).

Pourquoi pas simplement le premier élément de X-Forwarded-For ? Chaque proxy AJOUTE l'adresse qui
s'est connectée à lui à la fin de l'en-tête, mais un client malveillant peut écrire ce qu'il veut au
début. Seules les entrées ajoutées par NOS proxys sont fiables : on lit donc l'en-tête depuis la
droite, en sautant `hops - 1` entrées (celles ajoutées par les proxys intermédiaires).
"""
import os
from typing import Any


def trusted_hops() -> int:
    try:
        return max(0, int(os.getenv("UPB_TRUSTED_PROXY_HOPS", "0") or 0))
    except ValueError:
        return 0


def client_ip(request: Any) -> str | None:
    peer = request.client.host if getattr(request, "client", None) else None
    hops = trusted_hops()
    if hops == 0:
        return peer
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(forwarded) >= hops:
        return forwarded[-hops]
    return peer  # en-tête absent ou trop court : on ne devine pas, on garde l'adresse de connexion
