"""Dépannage en ligne de commande : crée un partenaire (permission INSCRIPTION, toutes les données) et une clé d'API.
La gestion courante des intégrations se fait depuis l'onglet « Intégrations » de la page admin.

Usage (depuis backend/) :
  python -m scripts.create_partner "Site officiel concours"            # crée le partenaire ou lui ajoute une clé
  python -m scripts.create_partner "Site officiel concours" --desactiver

La clé n'est affichée qu'une seule fois : seule son empreinte SHA-256 est enregistrée.
Chaque exécution ajoute une nouvelle clé ; les précédentes restent valides jusqu'à leur révocation
(onglet « Intégrations » de la page admin).
"""
import argparse
import json
import sys

import psycopg

from app import config, security

ALL_FIELDS = [
    "matricule", "nom", "prenoms", "date_naissance", "filiere", "niveau",
    "sexe", "email", "telephone", "allergies_affection",
]


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
        row = connection.execute(
            """
            INSERT INTO partenaire (nom, permission, champs, cree_par)
            VALUES (%s, 'INSCRIPTION', %s::jsonb, 'script')
            ON CONFLICT (LOWER(nom)) DO UPDATE SET actif = TRUE, updated_at = NOW()
            RETURNING id
            """,
            (nom, json.dumps(ALL_FIELDS)),
        ).fetchone()
        connection.execute(
            "INSERT INTO partenaire_cle (partenaire_id, prefixe, cle_hash, libelle, cree_par) "
            "VALUES (%s, %s, %s, 'Créée par script', 'script')",
            (row[0], key[:12], security.hash_api_key(key)),
        )
        connection.commit()
    print(f"Partenaire « {nom} » enregistré.")
    print("Clé d'API (à transmettre par un canal sûr ; elle ne sera plus affichée) :")
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
