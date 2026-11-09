# Journal de restructuration

Objectif : projet 100 % FastAPI (backend) + HTML/CSS/JS (frontend), sans aucune
trace de PHP, avec PostgreSQL hébergé sur Neon. Rien n'est inventé : le code
Python est la transcription du PHP d'origine (via `transcription_python/`).

Chaque étape ci-dessous est ajoutée au moment où elle est réalisée.

## Étape 1 — Séparation frontend / backend et migrations SQL

- Création des dossiers `frontend/`, `backend/` et `docs/`.
- Déplacés vers `frontend/` (contenu inchangé à cette étape) :
  `index.html`, `admin.html`, `scanner.html`, `genie.css`, `genie-fx.js`, `logo-upb.png`.
- Déplacés vers `backend/migrations/` : `01_pointage.sql`, `02_etudiant_contact.sql`.
- Restauré depuis l'historique git (commit `7310468`) puis placé dans
  `backend/migrations/` : `03_inscription_migrations.sql`. Il avait été supprimé
  (commit `da5fb19`) alors que le code en dépend (table `etudiant_modification`,
  colonnes `date_naissance`, `allergies_affection`).

## Étape 2 — Backend FastAPI (`backend/`)

Transcription de la logique PHP (et de `transcription_python/`, base Flask) vers
FastAPI. Aucune règle métier nouvelle.

| PHP d'origine | Nouveau code | Route |
|---|---|---|
| `config.php`, `db.php` | `app/config.py`, `app/db.py` | — |
| `admin_auth.php`, `admin_auth_check.php` | `app/auth.py`, `routes/admin.py` | `POST /api/admin_auth_check` |
| `student_helpers.php` | `app/student_helpers.py` | — |
| `verifier.php` | `routes/students.py` | `POST /api/verifier` |
| `inscrire.php` | `routes/students.py` | `POST /api/inscrire` |
| `demander_modification.php` | `routes/students.py` | `POST /api/demander_modification` |
| `update_etudiant.php` | `routes/students.py` | `POST /api/update_etudiant` |
| `update_telephone.php` | `routes/students.py` | `POST /api/update_telephone` |
| `get_etudiants.php` | `routes/admin.py` | `GET /api/get_etudiants` |
| `admin_modifications.php` | `routes/admin.py` | `GET`/`POST /api/admin_modifications` |
| `import_etudiants.php` | `routes/admin.py` | `POST /api/import_etudiants` |
| `pointage.php` | `routes/attendance.py` | `GET`/`POST /api/pointage` |

Choix techniques :

- `app/errors.py` : toutes les réponses gardent le contrat `{status, message}` ;
  dates en `AAAA-MM-JJ`, horodatages en `AAAA-MM-JJ HH:MM:SS` (le frontend fait
  `heure_pointage.slice(11, 16)`).
- `app/main.py` : CORS `*` et `Cache-Control: no-store` sur `/api/*`, comme en PHP ;
  le dossier `frontend/` est servi à la racine par FastAPI (même origine, donc
  pas de configuration CORS côté navigateur).
- `app/db.py` : pool `psycopg_pool`, `prepare_threshold=None` (pooler Neon /
  PgBouncer), vérification des connexions (Neon coupe les connexions inactives).
- Configuration : `DATABASE_URL` + `UPB_ADMIN_TOKEN` dans `backend/.env`
  (ignoré par git, modèle dans `.env.example`) ; les anciennes variables
  `UPB_DB_HOST/NAME/USER/PASS/DRIVER` disparaissent, ainsi que le mot de passe de
  repli en clair qui figurait dans `config.php`.

Écarts par rapport à `transcription_python/` (corrections de bugs, PHP pris
comme référence) :

1. **Import CSV** : en cas d'erreur de validation, les lignes valides déjà
   insérées étaient *validées* (COMMIT) alors que le message annonçait « Aucune
   ligne importée ». Désormais la transaction est annulée (comme le
   `rollBack()` PHP). Couvert par `test_failed_import_rolls_back_valid_rows`.
2. **Demande de modification** : `import json` manquant (la route plantait au
   premier appel valide).
3. `update_telephone` / `update_etudiant` : plus de `404` sur 0 ligne modifiée
   (le PHP répondait `success`) ; `sexe` n'est plus forcé en majuscules dans
   `update_etudiant` (le PHP ne le faisait pas).
4. `pointage` : le message de succès échappe le matricule en HTML (`escHtml`
   PHP) et `taux_presence` s'affiche `50 %` et non `50.0 %`, comme PHP.
5. `get_etudiants` : une erreur sur les statistiques n'empêche plus d'afficher
   la liste (comportement PHP).
6. Import CSV : `matricules` renvoyés dans l'ordre d'insertion (PHP
   `array_keys`), au lieu d'un tri alphabétique.

Limitation connue : une méthode HTTP non prévue sur une route existante renvoie
`404` JSON (et non `405`), car le service statique du frontend est monté à la
racine. Le frontend n'est pas concerné.

## Étape 3 — Schéma de base et migrations

- `backend/migrations/00_schema_base.sql` (nouveau) : le schéma initial
  (`etudiant`, `filiere`, `niveau`, `candidat_concours`) n'existait nulle part
  dans le dépôt et la base Neon était vide. Le fichier est **déduit des
  requêtes SQL du code** (colonnes lues/écrites, longueurs de champs validées
  par l'API). Seule contrainte ajoutée : `etudiant.matricule UNIQUE`
  (le code le traite comme identifiant). **À comparer avec le schéma d'origine si
  vous en disposez.**
- `backend/scripts/migrate.py` : applique `migrations/*.sql` dans l'ordre sur
  `DATABASE_URL` (migrations idempotentes). Exécuté sur Neon : tables créées.

## Étape 4 — Frontend (`frontend/`)

Seul changement de contenu : les appels `fetch('xxx.php')` deviennent
`fetch('/api/xxx')` (index.html : 3, admin.html : 10, scanner.html : 2) et le
message « Vérifiez que le serveur PHP… » devient « Vérifiez que le serveur… ».
Logique, styles et scripts inchangés.

## Étape 5 — Suppression de PHP et des fichiers obsolètes

Supprimés (`git rm`, récupérables via l'historique git) :

- les 14 fichiers `*.php` ;
- `transcription_python/` (ébauche Flask : son contenu est repris et corrigé
  dans `backend/`) et `transcription_python.zip` ;
- `openapi.yaml` : décrivait les chemins `.php` ; FastAPI génère désormais le
  contrat à jour sur `/openapi.json` et la documentation sur `/docs`.

`README.md` réécrit pour FastAPI/Neon (installation, configuration, API,
migrations, tests). Ajout d'un `.gitignore` (`.env`, `.venv`, `__pycache__`).

## Étape 6 — Vérification

- `python -m pytest -q` (dans `backend/`) : **24 tests passent**, dont un
  parcours complet sur la base Neon (import, vérification, inscription,
  demandes approuvées/refusées, recalcul de matricule, conflit de matricule,
  pointage et double pointage, rollback d'import).
- Démarrage réel avec `uvicorn` : `/`, `/admin.html`, `/scanner.html`, `/docs`
  répondent `200` ; `POST /api/admin_auth_check` renvoie `401` sans jeton et
  `200` avec ; `GET /api/get_etudiants` et `GET /api/pointage` interrogent
  Neon avec succès.
- Recherche de traces : aucun fichier `.php` ni mention de PHP/Flask/XAMPP/PDO
  dans le code, le frontend ou le README (seul ce journal les cite, pour
  documenter la migration). Un test (`test_no_php_reference_in_frontend`)
  vérifie l'absence de `.php` dans les pages servies.
- Correctif découvert pendant la vérification : la connexion à la base était
  empruntée au pool *avant* la validation du corps de requête, donc une latence
  Neon faisait échouer même une requête invalide (500 au lieu de 400, test
  intermittent). `app/db.py` fournit maintenant une classe `Database` à
  connexion paresseuse : la base n'est sollicitée qu'au premier accès.

## Points à valider de votre côté

1. `00_schema_base.sql` est déduit du code : comparez-le avec le schéma de la
   base d'origine si elle existe encore (types, contraintes, index).
2. Le jeton admin généré est dans `backend/.env` (ignoré par git). L'URL Neon
   (mot de passe inclus) y figure aussi : comme elle a été partagée en clair
   dans la conversation, envisagez de renouveler le mot de passe depuis le
   tableau de bord Neon.
3. Les modifications ne sont pas commitées.

## Étape 7 — Documentation OpenAPI/Swagger et guide d'intégration

- `backend/app/docs.py` (nouveau) : modèles Pydantic **uniquement documentaires**
  (requêtes, réponses, erreurs). Les routes continuent de lire le JSON à la main
  (messages d'erreur d'origine conservés) : aucun changement de comportement.
- Chaque route porte `tags`, `summary`, `description`, corps de requête, réponses
  de succès et d'erreur (`400/401/404/409/422/500/503`) ; `update_telephone`
  est marquée `deprecated`. Le détail de la route `import_etudiants` (CSV) est
  décrit dans sa description.
- `app/auth.py` : `require_admin` s'appuie sur `HTTPBearer` (schéma `AdminBearer`),
  ce qui affiche le cadenas et le bouton **Authorize** dans Swagger. Comportement
  inchangé : `401` jeton absent/invalide, `503` jeton non configuré.
- `app/main.py` : description générale (conventions, authentification) et
  tags ; `/docs`, `/redoc`, `/openapi.json`.
- `scripts/export_openapi.py` : exporte le contrat vers `docs/openapi.json`.
- Nouveau test `test_openapi_documents_every_route` (toutes les routes
  documentées, schéma Bearer, routes publiques sans sécurité).
- `docs/GUIDE_INTEGRATION.md` (nouveau) : appel `POST /api/verifier`, matricule,
  procédure de comparaison côté serveur, exemples Python / Node / curl, codes
  d'erreur, ce que la vérification prouve ou non, données personnelles,
  check-list.
- Connexion Neon sans pooler : ajoutée comme `DATABASE_URL_DIRECT` dans
  `backend/.env` ; `scripts/migrate.py` l'utilise en priorité (DDL), l'API
  continue d'utiliser l'URL poolée. Migrations rejouées avec succès via la
  connexion directe.

## Étape 8 — Parcours « matricule inconnu » et documentation protégée

**Logique candidat (portail `index.html`)**
- Matricule trouvé : affichage du dossier, corrections possibles (`/api/demander_modification`,
  validées par l'admin), puis inscription — comportement existant, conservé.
- Matricule non trouvé : message `Matricule non reconnus dans la base I-UPB.`
  (ancien : « Matricule et date de naissance non reconnus dans la base UPB. »,
  modifié dans `routes/students.py`, le guide et les tests) puis affichage du
  même formulaire, vide, pour renseigner ses informations ; le bouton devient
  « Envoyer mon dossier pour validation » et « Confirmer mon inscription » est masqué
  tant que le dossier n'existe pas.
- `migrations/04_etudiant_creation_demande.sql` : table `etudiant_creation_demande`
  (matricule saisi, données JSON, statut `EN_ATTENTE/APPROUVEE/REFUSEE`, une seule
  demande en attente par matricule). Appliquée sur Neon.
- `POST /api/demander_creation` (public) : mêmes validations que la demande de
  correction (extraites dans `_validated_fields`) ; nom, prénoms et date de naissance
  obligatoires ; `409` si le matricule saisi ou le matricule calculé existe déjà, ou
  si une demande est déjà en attente.
- `GET/POST /api/admin_creations` (admin) : liste et approbation/refus. L'approbation
  crée l'étudiant (filière/niveau créés au besoin) avec le matricule **calculé**
  (3 lettres du nom + initiale du prénom + jjmmaa + 0001, règle du reste de
  l'application), pas celui saisi par le candidat ; le matricule créé est renvoyé et
  affiché à l'admin. Un conflit de matricule renvoie `422` et annule tout.
- `admin.html` : nouveau bloc « Demandes d'ajout de dossier » dans la section des
  demandes (même présentation, boutons Approuver / Refuser).

**Documentation en ligne soumise à autorisation**
- `/docs`, `/redoc` et `/openapi.json` passent par `app/docs_access.py` : HTTP Basic,
  nom d'utilisateur libre, mot de passe = `UPB_DOCS_PASSWORD` (remis aux intégrateurs
  autorisés) ou `UPB_ADMIN_TOKEN`. Le mot de passe de la doc ne donne aucun droit sur
  l'API d'administration (testé). `503` si rien n'est configuré.
- `errors.py` : les en-têtes des erreurs HTTP (ici `WWW-Authenticate`) sont conservés.

**Vérification** : 31 tests passent (parcours complet inconnu → demande → approbation →
vérification → inscription ; refus puis nouvelle demande ; accès à la doc ; libellé).
Syntaxe JavaScript de `index.html` et `admin.html` validée avec `node --check`.
Les écrans n'ont pas été manipulés dans un navigateur.

## Étape 9 — Connexion administrateur par identifiant + mot de passe

Remplace le jeton fixe partagé `UPB_ADMIN_TOKEN` (supprimé partout).

- `migrations/05_administrateur.sql` : table `administrateur` (identifiant unique sans
  distinction de casse, `mot_de_passe_hash`, `actif`, `derniere_connexion`). Appliquée sur Neon.
- `app/security.py` (nouveau, bibliothèque standard uniquement) :
  - mots de passe hachés **scrypt** avec sel aléatoire (jamais stockés en clair) ;
    un identifiant inconnu coûte le même temps de calcul (pas de fuite par le délai) ;
  - jetons de session signés HMAC-SHA256 (`UPB_SECRET_KEY`, 32 caractères minimum),
    expirant (`UPB_SESSION_HOURS`, 8 h par défaut) ;
  - limitation des essais : 5 échecs → blocage 15 min (par adresse IP et par identifiant,
    en mémoire, par processus).
- `POST /api/admin_login` (`routes/session.py`, public) : `{identifiant, password}` →
  `{token, expires_in, identifiant}` ; `401` message unique « Identifiant ou mot de passe
  incorrect. » (compte inconnu, désactivé ou mauvais mot de passe), `429` si bloqué.
- `app/auth.py` : `require_admin` valide le jeton de session ; l'en-tête
  `Authorization: Bearer <jeton>` reste le même pour toutes les routes admin et le
  pointage, donc aucun changement dans leur appel. `503` si `UPB_SECRET_KEY` manque.
- `scripts/create_admin.py` : `python -m scripts.create_admin IDENTIFIANT` crée un compte
  ou change son mot de passe (saisi au clavier, 10 caractères minimum) ;
  `--desactiver` désactive un compte.
- Documentation `/docs` : accessible avec `UPB_DOCS_PASSWORD` ou avec l'identifiant +
  mot de passe d'un administrateur (HTTP Basic).
- Frontend : `admin.html` affiche un formulaire Identifiant / Mot de passe ; le jeton de
  session est conservé dans `sessionStorage` (comme avant, utilisé aussi par
  `scanner.html`) ; une réponse `401` en cours d'usage ramène à l'écran de connexion avec
  « Votre session a expiré ».
- `.env` : `UPB_ADMIN_TOKEN` remplacé par une `UPB_SECRET_KEY` générée.

Limites connues : les sessions sont sans état, donc désactiver un compte n'invalide pas
une session déjà ouverte avant son expiration (changer `UPB_SECRET_KEY` déconnecte tout le
monde) ; le compteur d'essais est local à chaque processus et, derrière un proxy, l'adresse
IP vue est celle du proxy (le blocage par identifiant reste effectif).

## Étape 10 — Fin de l'inscription depuis le portail ; inscription via le site officiel

Les inscriptions au concours se feront sur un site dédié ; l'étudiant vérifié depuis ce
site doit être marqué inscrit dans notre base.

- **Portail (`index.html`)** : bouton « Confirmer mon inscription au concours », fonction
  `inscrireCandidat`, bloc de résultat (numéro + QR code) et script `qrcodejs` supprimés ;
  textes ajustés (« Mon dossier »). Le portail ne sert plus qu'à consulter son dossier, le
  corriger (validation admin) ou soumettre un dossier si le matricule est inconnu.
- **`POST /api/inscrire` supprimée** : publique et sans contrôle, elle aurait permis à
  n'importe qui de marquer n'importe quel étudiant inscrit.
- **`POST /api/partenaire/verifier`** (`routes/partner.py`) : réservée aux partenaires
  (en-tête `X-API-Key`). Retrouve l'étudiant, crée sa ligne `candidat_concours` s'il n'est pas
  encore inscrit (statut `EN_ATTENTE`, comme l'ancienne inscription, donc compatible avec les
  statistiques et le pointage), et renvoie son identité et `inscription`
  (`numero_candidat`, `statut`, `deja_inscrit`). Idempotent. `numero_candidat` facultatif
  (numéro du site officiel), sinon généré. Même message 404 que le portail.
- **Clés d'API** : `migrations/06_partenaire.sql` (table `partenaire`, seule l'empreinte
  SHA-256 est stockée) et `python -m scripts.create_partner NOM` (clé affichée une seule
  fois ; relancer la commande la renouvelle ; `--desactiver` la révoque).
- Guide d'intégration : section dédiée au site officiel ; README, description Swagger et
  contrat `docs/openapi.json` mis à jour (tag « Partenaires », schéma `PartnerApiKey`).

## Étape 11 — « Tout approuver » et espacements de la page admin

**Approuver toutes les demandes d'un coup**
- `POST /api/admin_approve_all` (admin) : approuve toutes les demandes de correction et
  d'ajout de dossier en attente. Chaque demande est traitée indépendamment (sa propre
  transaction) : une demande invalide (ex. conflit de matricule) reste en attente et est
  listée dans `echecs`, sans bloquer les autres. Réponse : `modifications` et `creations`,
  chacun `{approuvees, echecs: [{id, message}]}`.
- `routes/admin.py` : la logique d'approbation/refus de `admin_modifications` et
  `admin_creations` est extraite dans `_decide_modification` / `_decide_creation`,
  réutilisées par les routes unitaires (comportement inchangé) et par le traitement groupé.
- `admin.html` : section « Demandes en attente » avec résumé (« N demandes en attente »),
  boutons **Actualiser** et **Tout approuver (N)** (confirmation avant exécution, bouton
  désactivé s'il n'y a rien à approuver) et bilan affiché sous les boutons, avec la liste des
  demandes restées en attente et la raison.
- Test `test_approve_all_pending_requests` : comme l'endpoint agit sur toute la base, le test
  **s'abstient s'il existe des demandes réelles en attente** (pour ne jamais les approuver).

**Espacements (marges et paddings), `admin.html`**
Bloc CSS « Espacements » en fin de feuille de style, rythme vertical sur base de 4 px et
gouttière horizontale constante (24 px, 16 px sur mobile) :
- en-têtes de bloc : plus d'air au-dessus, sous le surtitre et sous le sous-titre ; largeur
  de ligne limitée pour la lecture ;
- import CSV : champs, boutons et aide séparés (filet pointillé), marge basse ;
- demandes : cartes redessinées (nom et matricule à gauche, date en pastille à droite,
  champs modifiés en grille de petites cartes libellé/valeur, filet puis boutons) ; groupes
  « Corrections » et « Ajouts de dossier » séparés avec compteur ; états vides encadrés.
  Le sous-titre « Demandes d'ajout de dossier » ajouté à l'étape 8 n'avait aucun padding
  (collé au bord) : corrigé ;
- pointage : outils sur une ligne, tuiles de statistiques et état vide plus aérés ;
- mobile : gouttières réduites, boutons des demandes en pleine largeur, grille en une colonne.
Vérifié par captures (Edge headless, API simulée) à 1400 px et à 400 px : aucun débordement.

## Étape 12 — Journal d'activité et onglet « Journal »

Traçabilité de toutes les actions menées sur et à travers le système.

- `migrations/07_journal_activite.sql` : table `journal_activite` (date, type d'acteur, acteur,
  catégorie, action, statut `SUCCES/ECHEC`, cible, message, détails JSONB, code HTTP, IP,
  méthode, chemin, navigateur) et index. Appliquée sur Neon.
- `app/audit.py` (nouveau) : un middleware (`main.py`) ouvre un contexte d'audit par requête et
  journalise **un événement par action** une fois la réponse produite (statut déduit du code
  HTTP). Les routes complètent l'événement (`audit.annotate` : cible, noms de champs, compteurs),
  les gestionnaires d'erreurs y ajoutent le motif, l'authentification y inscrit l'acteur
  (administrateur ou partenaire). Les réponses `401` sur toute route `/api` non listée sont
  journalisées (`ACCES_REFUSE`). Le traitement groupé journalise une ligne par demande + une synthèse.
- Garanties : écriture via un pool **dédié** (`db.audit_connection`), donc indépendante de la
  transaction de la requête et sans risque d'interblocage ; un échec d'écriture n'empêche jamais
  l'action (testé) ; aucun mot de passe, jeton ni clé d'API, et seulement les *noms* des champs
  modifiés (testé : les valeurs ne figurent pas dans le journal).
- API (`routes/journal.py`) : `GET /api/admin_journal` (filtres : recherche libre, catégorie,
  action, type d'acteur, résultat, période ; pagination ; statistiques 24 h ; listes d'actions
  et de catégories), `GET /api/admin_journal/export` (CSV UTF-8 `;`, neutralisation des formules,
  20 000 lignes max, l'export est lui-même tracé), `POST /api/admin_logout` (trace la
  déconnexion) et `POST /api/admin_export` (trace les exports Excel/PDF faits dans le navigateur).
- `admin.html` : nouvel onglet **Journal** (synthèse, filtres, tableau avec détails dépliables,
  pagination, export CSV) ; la déconnexion et les exports Excel/PDF existants sont désormais
  signalés au serveur.
- Tests : `tests/conftest.py` désactive l'audit par défaut (`UPB_AUDIT_ENABLED=0`) pour ne pas
  écrire dans Neon ; `tests/test_journal.py` l'active et nettoie ses lignes (adresse
  « testclient »).
- Choix assumé : les consultations réussies de listes par un administrateur ne sont pas
  journalisées (volume) ; la vérification publique d'un matricule l'est, car elle expose des
  données personnelles.
- Limites : pas de purge automatique ; derrière un proxy, l'IP est celle du proxy ; une
  base injoignable pendant l'écriture fait perdre l'événement (consigné dans les logs).
