-- Gestion des intégrations (sites externes) : fiche partenaire enrichie et clés d'API multiples.
--
--  * permission : VERIFICATION (vérifie l'identité, lecture seule) ou INSCRIPTION (vérifie ET marque
--    l'étudiant inscrit au concours) ;
--  * champs : données de l'étudiant que le partenaire reçoit (minimum nécessaire) ;
--  * ips_autorisees : liste d'adresses IP / réseaux CIDR autorisés (vide = toutes) ;
--  * limite_par_minute : quota d'appels par minute (NULL = illimité) ;
--  * partenaire_cle : plusieurs clés par partenaire (rotation sans coupure), expiration, révocation.
-- Idempotent. Les partenaires existants conservent leur comportement : permission INSCRIPTION,
-- toutes les données, aucune restriction ; leur clé est reprise dans partenaire_cle.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'partenaire' AND column_name = 'permission'
    ) THEN
        ALTER TABLE partenaire ADD COLUMN permission VARCHAR(20) NOT NULL DEFAULT 'VERIFICATION'
            CHECK (permission IN ('VERIFICATION', 'INSCRIPTION'));
        ALTER TABLE partenaire ADD COLUMN champs JSONB NOT NULL
            DEFAULT '["matricule","nom","prenoms","date_naissance","filiere","niveau"]';
        -- Partenaires créés avant cette migration : comportement inchangé
        UPDATE partenaire
        SET permission = 'INSCRIPTION',
            champs = '["matricule","nom","prenoms","date_naissance","telephone","email","sexe","allergies_affection","filiere","niveau"]';
    END IF;
END $$;

ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS description       TEXT;
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS site_url          VARCHAR(255);
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS contact           VARCHAR(255);
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS ips_autorisees    JSONB NOT NULL DEFAULT '[]';
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS limite_par_minute INTEGER
    CHECK (limite_par_minute IS NULL OR limite_par_minute > 0);
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMP;
ALTER TABLE partenaire ADD COLUMN IF NOT EXISTS cree_par          VARCHAR(60);

CREATE TABLE IF NOT EXISTS partenaire_cle (
    id                   SERIAL PRIMARY KEY,
    partenaire_id        INTEGER NOT NULL REFERENCES partenaire(id) ON DELETE CASCADE,
    prefixe              VARCHAR(16) NOT NULL,
    cle_hash             CHAR(64) NOT NULL UNIQUE,
    libelle              VARCHAR(80),
    created_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    cree_par             VARCHAR(60),
    expire_le            DATE,
    revoquee_le          TIMESTAMP,
    derniere_utilisation TIMESTAMP,
    nb_utilisations      BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_partenaire_cle_partenaire ON partenaire_cle (partenaire_id);

-- Reprise des clés existantes (l'ancienne colonne devient inutile)
ALTER TABLE partenaire ALTER COLUMN cle_hash DROP NOT NULL;
INSERT INTO partenaire_cle (partenaire_id, prefixe, cle_hash, libelle, created_at)
SELECT id, 'upb_••••', cle_hash, 'Clé initiale', created_at
FROM partenaire
WHERE cle_hash IS NOT NULL
ON CONFLICT (cle_hash) DO NOTHING;
UPDATE partenaire SET cle_hash = NULL WHERE cle_hash IS NOT NULL;
