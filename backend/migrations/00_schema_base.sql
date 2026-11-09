-- Schéma principal minimal requis par l'API (concours Génie UPB).
--
-- NOTE : le schéma d'origine n'était pas versionné dans le dépôt. Ce fichier
-- est DÉDUIT UNIQUEMENT des requêtes SQL du code (colonnes lues/écrites). Il
-- ne crée que ce que le code utilise. Les colonnes ajoutées par
-- 02_etudiant_contact.sql et 03_inscription_migrations.sql (date_naissance,
-- allergies_affection, email, sexe) ne sont pas dupliquées ici.
-- Toutes les instructions sont idempotentes.

CREATE TABLE IF NOT EXISTS filiere (
    id      SERIAL PRIMARY KEY,
    libelle VARCHAR(150) NOT NULL
);

CREATE TABLE IF NOT EXISTS niveau (
    id      SERIAL PRIMARY KEY,
    libelle VARCHAR(150) NOT NULL
);

CREATE TABLE IF NOT EXISTS etudiant (
    id          SERIAL PRIMARY KEY,
    matricule   VARCHAR(40)  NOT NULL UNIQUE,
    nom         VARCHAR(150) NOT NULL,
    prenoms     VARCHAR(150) NOT NULL,
    telephone   VARCHAR(40),
    filiere_id  INTEGER REFERENCES filiere(id),
    niveau_id   INTEGER REFERENCES niveau(id)
);

CREATE TABLE IF NOT EXISTS candidat_concours (
    id              SERIAL PRIMARY KEY,
    etudiant_id     INTEGER NOT NULL REFERENCES etudiant(id) ON DELETE CASCADE,
    numero_candidat VARCHAR(40) NOT NULL,
    statut          VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
);
