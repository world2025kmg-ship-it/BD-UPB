-- Champs nécessaires au portail étudiant et à l'import CSV.
ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS date_naissance DATE;
ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS allergies_affection TEXT;
ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS email VARCHAR(255);
ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS sexe VARCHAR(1)
    CHECK (sexe IS NULL OR sexe IN ('M', 'F'));

CREATE INDEX IF NOT EXISTS idx_etudiant_email ON etudiant (LOWER(email));

CREATE TABLE IF NOT EXISTS etudiant_modification (
    id             SERIAL PRIMARY KEY,
    etudiant_id    INTEGER NOT NULL REFERENCES etudiant(id) ON DELETE CASCADE,
    modifications JSONB NOT NULL,
    statut         VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
                   CHECK (statut IN ('EN_ATTENTE', 'APPROUVEE', 'REFUSEE')),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    reviewed_at    TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_etudiant_modification_pending
    ON etudiant_modification (etudiant_id)
    WHERE statut = 'EN_ATTENTE';
CREATE INDEX IF NOT EXISTS idx_etudiant_modification_status
    ON etudiant_modification (statut, created_at);
