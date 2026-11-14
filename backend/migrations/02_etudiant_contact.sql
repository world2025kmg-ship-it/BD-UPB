-- Contact étudiant : adresse e-mail et sexe
-- À exécuter après 01_pointage.sql sur la base upb_concours

ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS email VARCHAR(255);
ALTER TABLE etudiant ADD COLUMN IF NOT EXISTS sexe VARCHAR(1)
    CHECK (sexe IS NULL OR sexe IN ('M', 'F'));

CREATE INDEX IF NOT EXISTS idx_etudiant_email ON etudiant (LOWER(email));

COMMENT ON COLUMN etudiant.email IS 'Adresse e-mail de l''étudiant, renseignée à l''inscription au concours';
COMMENT ON COLUMN etudiant.sexe IS 'M ou F, laissé NULL si non précisé';