CREATE TABLE IF NOT EXISTS etudiant_inscription_demande (
    id                  SERIAL PRIMARY KEY,
    matricule           VARCHAR(40) NOT NULL,
    nom                 VARCHAR(150) NOT NULL,
    prenoms             VARCHAR(150) NOT NULL,
    date_naissance      DATE NOT NULL,
    filiere             VARCHAR(150) NOT NULL,
    niveau              VARCHAR(150) NOT NULL,
    telephone           VARCHAR(40),
    email               VARCHAR(255),
    sexe                VARCHAR(1) CHECK (sexe IS NULL OR sexe IN ('M', 'F')),
    allergies_affection TEXT,
    statut              VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
                        CHECK (statut IN ('EN_ATTENTE', 'APPROUVEE', 'REFUSEE')),
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    reviewed_at         TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_etudiant_inscription_demande_pending
    ON etudiant_inscription_demande (matricule)
    WHERE statut = 'EN_ATTENTE';
CREATE INDEX IF NOT EXISTS idx_etudiant_inscription_demande_status
    ON etudiant_inscription_demande (statut, created_at);

INSERT INTO niveau (libelle)
SELECT 'Master'
WHERE NOT EXISTS (
    SELECT 1 FROM niveau WHERE LOWER(libelle) = LOWER('Master')
);
