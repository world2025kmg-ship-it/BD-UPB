"""Crée un site partenaire et sa clé d'API, ou renouvelle / désactive la clé d'un partenaire.

Usage (depuis backend/) :
  python -m scripts.create_partner "Site officiel concours"            # crée (ou renouvelle la clé)
  python -m scripts.create_partner "Site officiel concours" --desactiver

La clé n'est affichée qu'une seule fois : seule son empreinte SHA-256 est enregistrée.
Renouveler la clé invalide l'ancienne immédiatement.
"""
import argparse
import sys

import psycopg

from app import config, security


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("nom")
    parser.add_argument("--desactiver", action="store_true", help="désactive le partenaire")
    args = parser.parse_args()
    nom = args.nom.strip()
    if not nom or len(nom) > 80:
        print("Nom invalide (1 à 80 caractères).")
        return 1

    with psycopg.connect(
        config.database_url(), connect_timeout=config.connect_timeout(), prepare_threshold=None
    ) as connection:
        if args.desactiver:
            updated = connection.execute(
                "UPDATE partenaire SET actif = FALSE WHERE LOWER(nom) = LOWER(%s)", (nom,)
            ).rowcount
            connection.commit()
            print("Partenaire désactivé." if updated else "Partenaire introuvable.")
            return 0 if updated else 1

        key = security.generate_api_key()
        connection.execute(
            """
            INSERT INTO partenaire (nom, cle_hash) VALUES (%s, %s)
            ON CONFLICT (LOWER(nom)) DO UPDATE SET cle_hash = EXCLUDED.cle_hash, actif = TRUE
            """,
            (nom, security.hash_api_key(key)),
        )
        connection.commit()
    print(f"Partenaire « {nom} » enregistré.")
    print("Clé d'API (à transmettre par un canal sûr ; elle ne sera plus affichée) :")
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
