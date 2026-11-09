-- Demandes d'ajout d'un dossier étudiant : un candidat dont le matricule est
-- inconnu renseigne ses informations ; un administrateur les valide avant
-- toute création dans la table etudiant.

CREATE TABLE IF NOT EXISTS etudiant_creation_demande (
    id              SERIAL PRIMARY KEY,
    matricule_saisi VARCHAR(40) NOT NULL,
    donnees         JSONB NOT NULL,
    statut          VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
                    CHECK (statut IN ('EN_ATTENTE', 'APPROUVEE', 'REFUSEE')),
    etudiant_id     INTEGER REFERENCES etudiant(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    reviewed_at     TIMESTAMP
);

-- Une seule demande en attente par matricule saisi
CREATE UNIQUE INDEX IF NOT EXISTS uq_etudiant_creation_pending
    ON etudiant_creation_demande (matricule_saisi)
    WHERE statut = 'EN_ATTENTE';
CREATE INDEX IF NOT EXISTS idx_etudiant_creation_status
    ON etudiant_creation_demande (statut, created_at);
