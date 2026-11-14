-- Journal d'activité : trace de toutes les actions menées sur et à travers le système
-- (administrateurs, sites partenaires, candidats, accès à la documentation).
-- Table en ajout seul : l'application n'expose aucune modification ni suppression.
-- Les mots de passe, jetons et clés d'API ne sont jamais enregistrés ; les valeurs
-- personnelles modifiées non plus (seuls les noms des champs concernés le sont).

CREATE TABLE IF NOT EXISTS journal_activite (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    acteur_type  VARCHAR(20) NOT NULL
                 CHECK (acteur_type IN ('ADMIN', 'PARTENAIRE', 'PUBLIC', 'DOCUMENTATION', 'SYSTEME')),
    acteur       VARCHAR(80),
    categorie    VARCHAR(30) NOT NULL,
    action       VARCHAR(60) NOT NULL,
    statut       VARCHAR(10) NOT NULL CHECK (statut IN ('SUCCES', 'ECHEC')),
    cible_type   VARCHAR(30),
    cible        VARCHAR(120),
    message      TEXT,
    details      JSONB,
    code_http    SMALLINT,
    ip           VARCHAR(45),
    methode      VARCHAR(8),
    chemin       VARCHAR(200),
    user_agent   VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS idx_journal_created_at ON journal_activite (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_journal_action     ON journal_activite (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_acteur     ON journal_activite (LOWER(acteur), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_categorie  ON journal_activite (categorie, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_cible      ON journal_activite (LOWER(cible));
