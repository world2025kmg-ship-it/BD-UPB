-- Table : pointage_journalier
-- Enregistrement des présences par QR Code (Jour J)
-- Concours Génie UPB

CREATE TABLE IF NOT EXISTS pointage_journalier (
    id             SERIAL PRIMARY KEY,
    etudiant_id    INTEGER NOT NULL REFERENCES etudiant(id) ON DELETE CASCADE,
    date_pointage  DATE NOT NULL,
    jour_libelle   VARCHAR(80),
    heure_pointage TIMESTAMP DEFAULT NOW(),
    statut         VARCHAR(20) DEFAULT 'PRESENT' CHECK (statut IN ('PRESENT','RETARD','DOUBLE')),
    qr_code_valide BOOLEAN DEFAULT TRUE,
    created_at     TIMESTAMP DEFAULT NOW(),

    -- Un étudiant ne peut être pointé qu'une seule fois par jour
    CONSTRAINT uq_etudiant_date UNIQUE (etudiant_id, date_pointage)
);

-- Index pour les rapports rapides par date
CREATE INDEX IF NOT EXISTS idx_pointage_date ON pointage_journalier(date_pointage);
CREATE INDEX IF NOT EXISTS idx_pointage_etudiant_date ON pointage_journalier(etudiant_id, date_pointage);

COMMENT ON TABLE pointage_journalier IS 'Enregistrement des présences quotidiennes par scan QR Code lors du concours Génie UPB';
COMMENT ON COLUMN pointage_journalier.etudiant_id IS 'Référence vers la table etudiant';
COMMENT ON COLUMN pointage_journalier.date_pointage IS 'Date du concours (JJ-MM-AAAA)';
COMMENT ON COLUMN pointage_journalier.jour_libelle IS 'Libellé du jour (ex: Jour 1 - Écrits)';
COMMENT ON COLUMN pointage_journalier.statut IS 'PRESENT, RETARD ou DOUBLE (tentative de double pointage)';
COMMENT ON COLUMN pointage_journalier.qr_code_valide IS 'Indique si le QR code scanné était valide';