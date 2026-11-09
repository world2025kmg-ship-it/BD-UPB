-- Sites partenaires autorisés à vérifier un étudiant et à le marquer inscrit au concours
-- (ex. site officiel d'inscription). Authentification par clé d'API : seule l'empreinte
-- SHA-256 est stockée, la clé n'est affichée qu'une fois, à sa création
-- (python -m scripts.create_partner).

CREATE TABLE IF NOT EXISTS partenaire (
    id                    SERIAL PRIMARY KEY,
    nom                   VARCHAR(80) NOT NULL,
    cle_hash              CHAR(64) NOT NULL UNIQUE,
    actif                 BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    derniere_utilisation  TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_partenaire_nom ON partenaire (LOWER(nom));
