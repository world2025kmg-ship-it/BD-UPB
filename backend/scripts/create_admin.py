"""Crée un administrateur, ou change son mot de passe s'il existe déjà.

Usage (depuis backend/) :  python -m scripts.create_admin IDENTIFIANT
Le mot de passe est demandé au clavier (jamais passé en argument ni affiché).
Option : --desactiver  pour désactiver le compte.
"""
import argparse
import getpass
import sys

import psycopg

from app import config, security


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("identifiant")
    parser.add_argument("--desactiver", action="store_true", help="désactive le compte")
    args = parser.parse_args()
    identifiant = args.identifiant.strip()
    if not identifiant or len(identifiant) > 60:
        print("Identifiant invalide (1 à 60 caractères).")
        return 1

    with psycopg.connect(
        config.database_url(), connect_timeout=config.connect_timeout(), prepare_threshold=None
    ) as connection:
        if args.desactiver:
            updated = connection.execute(
                "UPDATE administrateur SET actif = FALSE WHERE LOWER(identifiant) = LOWER(%s)",
                (identifiant,),
            ).rowcount
            print("Compte désactivé." if updated else "Identifiant introuvable.")
            return 0 if updated else 1

        password = getpass.getpass("Mot de passe : ")
        if len(password) < security.MIN_PASSWORD_LENGTH:
            print(f"Mot de passe trop court ({security.MIN_PASSWORD_LENGTH} caractères minimum).")
            return 1
        if getpass.getpass("Confirmer le mot de passe : ") != password:
            print("Les mots de passe ne correspondent pas.")
            return 1
        connection.execute(
            """
            INSERT INTO administrateur (identifiant, mot_de_passe_hash)
            VALUES (%s, %s)
            ON CONFLICT (LOWER(identifiant))
            DO UPDATE SET mot_de_passe_hash = EXCLUDED.mot_de_passe_hash, actif = TRUE
            """,
            (identifiant, security.hash_password(password)),
        )
        connection.commit()
    print(f"Administrateur « {identifiant} » enregistré.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
