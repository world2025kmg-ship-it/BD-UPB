-- Comptes administrateurs (connexion par identifiant + mot de passe).
-- Le mot de passe n'est jamais stocké en clair : hachage scrypt avec sel
-- (format `scrypt$n$r$p$sel$hash`). Création : python -m scripts.create_admin

CREATE TABLE IF NOT EXISTS administrateur (
    id                 SERIAL PRIMARY KEY,
    identifiant        VARCHAR(60) NOT NULL,
    mot_de_passe_hash  TEXT NOT NULL,
    actif              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    derniere_connexion TIMESTAMP
);

-- Identifiant unique, sans distinction de casse
CREATE UNIQUE INDEX IF NOT EXISTS uq_administrateur_identifiant
    ON administrateur (LOWER(identifiant));
