# Concours Génie UPB

Application web de gestion du concours Génie UPB : recherche et inscription des
étudiants, demandes de correction de dossier, administration, import CSV et
pointage des présences par QR code.

- **Backend** : FastAPI (Python) — dossier [backend/](backend/)
- **Frontend** : HTML / CSS / JavaScript — dossier [frontend/](frontend/)
- **Base de données** : PostgreSQL hébergé sur [Neon](https://neon.tech)

```
bd-incub/
├── backend/
│   ├── app/
│   │   ├── main.py             # application FastAPI (+ service du frontend)
│   │   ├── config.py           # variables d'environnement
│   │   ├── db.py               # pool de connexions PostgreSQL
│   │   ├── auth.py             # authentification Bearer admin
│   │   ├── errors.py           # réponses JSON {status, message}
│   │   ├── student_helpers.py  # matricules, dates, références
│   │   └── routes/             # students.py · admin.py · attendance.py
│   ├── migrations/             # schéma SQL (00 → 03)
│   ├── scripts/migrate.py      # applique les migrations sur Neon
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── frontend/                   # index.html, admin.html, scanner.html, genie.css, genie-fx.js
└── docs/RESTRUCTURATION.md     # journal détaillé de la restructuration
```

## Installation et lancement (PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # puis renseigner DATABASE_URL et UPB_SECRET_KEY
python -m scripts.migrate        # crée/complète le schéma sur Neon (idempotent)
python -m scripts.create_admin admin   # crée un administrateur (mot de passe demandé au clavier)
python -m scripts.create_partner "Site officiel concours"   # crée la clé d'API du site d'inscription (affichée une fois)
uvicorn app.main:app --reload
```

L'application écoute sur <http://127.0.0.1:8000> :

| URL | Rôle |
|---|---|
| `/` | Portail étudiant (`index.html`) |
| `/admin.html` | Espace administrateur |
| `/scanner.html` | Scanner de pointage |
| `/docs` | Documentation interactive (Swagger UI), **accès soumis à autorisation** (voir ci-dessous) |
| `/redoc` | Documentation de référence (ReDoc), idem |
| `/openapi.json` | Contrat OpenAPI généré, idem (copie versionnée : [docs/openapi.json](docs/openapi.json)) |

## Documentation de l'API

- Swagger/OpenAPI : `/docs`, `/redoc`, `/openapi.json` (générés depuis le code, modèles dans `backend/app/docs.py`).
- **Accès protégé** (HTTP Basic, nom d'utilisateur libre) : mot de passe = `UPB_DOCS_PASSWORD` (à communiquer aux intégrateurs que l'administrateur autorise) ou l'identifiant et le mot de passe d'un administrateur. Le mot de passe de la doc ne donne aucun accès aux routes d'administration.
- Régénérer la copie versionnée : `python -m scripts.export_openapi` (depuis `backend/`).
- **Guide pour les sites externes** (vérification d'identité d'un étudiant) : [docs/GUIDE_INTEGRATION.md](docs/GUIDE_INTEGRATION.md).

## Configuration (`backend/.env`, non versionné)

| Variable | Rôle |
|---|---|
| `DATABASE_URL` | URL de connexion Neon (obligatoire) |
| `DATABASE_URL_DIRECT` | Connexion Neon sans pooler, utilisée par `scripts.migrate` (facultatif) |
| `UPB_DOCS_PASSWORD` | Mot de passe d'accès à `/docs`, `/redoc`, `/openapi.json` (10 caractères min.), à remettre aux intégrateurs autorisés |
| `UPB_SECRET_KEY` | Clé de signature des sessions administrateur, 32 caractères minimum (obligatoire) |
| `UPB_SESSION_HOURS` | Durée d'une session administrateur (8 h) |
| `UPB_DEFAULT_PER_PAGE` | Pagination par défaut (20) |
| `UPB_MAX_PER_PAGE` | Taille de page maximale (100) |
| `UPB_DB_CONNECT_TIMEOUT` | Délai de connexion en secondes (10) |

Générer une clé : `python -c "import secrets; print(secrets.token_hex(32))"`.
Ne jamais placer `.env` dans un dépôt. Changer `UPB_SECRET_KEY` déconnecte tous les administrateurs.

## API

Corps JSON en UTF-8 (`Content-Type: application/json`), sauf l'import CSV
(`multipart/form-data`, champ `fichier`). Les réponses ont la forme
`{"status": "success" | "error", "message": ...}`. CORS ouvert à toutes les
origines. Les routes admin exigent `Authorization: Bearer <jeton de session>`, obtenu par
`POST /api/admin_login` (identifiant + mot de passe). `401` si le jeton est absent,
invalide ou expiré ; `503` si `UPB_SECRET_KEY` n'est pas configurée.

| Méthode et chemin | Auth | Fonction |
|---|---|---|
| `POST /api/verifier` | — | Retrouver un dossier par `matricule` |
| `POST /api/demander_modification` | — | Proposer une correction à valider (`409` si déjà en attente) |
| `POST /api/partenaire/verifier` | clé d'API | **Site officiel d'inscription** : vérifie l'étudiant et le marque inscrit au concours (idempotent) |
| `POST /api/demander_creation` | — | Matricule inconnu : soumettre son dossier, à valider par un admin |
| `POST /api/admin_login` | — | Connexion `{"identifiant", "password"}` → jeton de session (5 échecs = blocage 15 min) |
| `POST /api/admin_auth_check` | admin | Vérifier la session |
| `GET /api/get_etudiants` | admin | Liste paginée (`page`, `per_page`, `q`) + statistiques |
| `POST /api/update_etudiant` | admin | Modifier téléphone, e-mail, sexe, allergies, date de naissance (recalcule le matricule) |
| `POST /api/update_telephone` | admin | Ancienne route : préférer `update_etudiant` |
| `GET /api/admin_modifications` | admin | Lister les demandes en attente |
| `POST /api/admin_modifications` | admin | `{"id": 1, "action": "approve" \| "reject"}` |
| `GET /api/admin_creations` | admin | Lister les demandes d'ajout de dossier en attente |
| `POST /api/admin_creations` | admin | `{"id": 1, "action": "approve" \| "reject"}` (approve crée l'étudiant) |
| `POST /api/admin_approve_all` | admin | Approuve d'un coup toutes les demandes en attente (corrections et ajouts de dossier) ; chaque demande est traitée indépendamment, les échecs sont listés |
| `GET /api/admin_journal` | admin | Journal d'activité : consultation filtrable (recherche, catégorie, action, acteur, résultat, période) et paginée |
| `GET /api/admin_journal/export` | admin | Export CSV du journal (mêmes filtres, 20 000 lignes max) |
| `POST /api/admin_logout` | admin | Enregistre la déconnexion dans le journal |
| `POST /api/admin_export` | admin | Enregistre un export Excel/PDF réalisé dans le navigateur |
| `POST /api/import_etudiants` | admin | Import CSV (5 Mo / 5 000 lignes max, tout ou rien) |
| `GET /api/pointage` | admin | Présences d'une date (`date=AAAA-MM-JJ`, `jour`) |
| `POST /api/pointage` | admin | Enregistrer une présence (`matricule`, `date`, `jour_libelle`) ; `409` si double pointage |

Règles métier :

- **Parcours candidat** : le candidat saisit son matricule. Trouvé → son dossier s'affiche et il peut proposer des corrections (validées par un admin). Il n'y a **pas d'inscription au concours depuis ce portail** : elle se fait sur le site officiel d'inscription, qui appelle `/api/partenaire/verifier` ; l'étudiant est alors marqué inscrit ici. Non trouvé → message « Matricule non reconnus dans la base I-UPB. » et formulaire de saisie de ses informations (`/api/demander_creation`), le dossier n'étant créé qu'après validation admin, avec le matricule calculé.
- **Matricule** = 3 premières lettres du nom + 1re lettre du prénom (sans accents,
  majuscules) + `jjmmaa` de naissance + `0001`. Ex. `KOUA1703980001`.
- **Dates** acceptées : `AAAA-MM-JJ`, `JJ/MM/AAAA`, `JJ-MM-AAAA` (pas de date future).
- **Import CSV** : colonnes obligatoires `nom`, `prenoms`, `date_naissance`,
  `filiere`, `niveau` ; facultatives `telephone`, `email`, `sexe`,
  `allergies_affection`. Séparateurs `,` et `;` reconnus. Une erreur annule
  tout l'import (`422`). Filières et niveaux inconnus sont créés.

Exemples :

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/partenaire/verifier -H "X-API-Key: CLE_PARTENAIRE" -H "Content-Type: application/json" -d '{\"matricule\":\"KOUA1703980001\"}'
curl.exe -X POST http://127.0.0.1:8000/api/admin_login -H "Content-Type: application/json" -d '{\"identifiant\":\"admin\",\"password\":\"VOTRE_MOT_DE_PASSE\"}'
curl.exe "http://127.0.0.1:8000/api/get_etudiants?q=KOUA" -H "Authorization: Bearer JETON_DE_SESSION"
```

## Journal d'activité

Toutes les actions menées sur et à travers le système sont tracées dans la table
`journal_activite` et consultables dans l'onglet **Journal** de la page admin (recherche,
filtres, pagination, export CSV). Chaque événement conserve : date et heure, acteur
(administrateur, partenaire, public, documentation), catégorie, action, résultat (succès ou
échec avec le motif), cible, détails, code HTTP, adresse IP, méthode, chemin et navigateur.

| Catégorie | Actions tracées |
|---|---|
| Authentification | connexion (réussie ou échouée), déconnexion, accès refusé (jeton absent/invalide) sur toute route `/api` |
| Étudiants | vérification d'un matricule (trouvé ou non), modification d'une fiche, modification du téléphone |
| Demandes | correction ou ajout de dossier soumis par un candidat ; approbation ou refus par un admin ; approbation en masse (une ligne par demande + une synthèse) |
| Inscriptions | vérification + marquage « inscrit » par le site officiel, ou clé d'API refusée |
| Pointage | présence enregistrée, double pointage refusé |
| Import / Export | import CSV (avec les erreurs), exports Excel/PDF/CSV |
| Documentation | consultation de `/docs` et `/redoc`, tentatives refusées |

**Ce qui n'est pas enregistré** : les mots de passe, jetons de session et clés d'API ; les
valeurs personnelles modifiées (seuls les *noms* des champs concernés le sont) ; les simples
consultations de listes par un administrateur (liste des étudiants, pointage, demandes),
trop nombreuses, sauf si elles sont refusées.

**Garanties** : le journal est en ajout seul (aucune route ne le modifie ni ne le supprime) ;
l'écriture passe par un pool de connexions dédié, indépendant de la transaction de la requête ;
un échec d'écriture est consigné dans les logs applicatifs mais ne fait jamais échouer
l'action. Variable `UPB_AUDIT_ENABLED=0` pour désactiver (tests). Pas de purge automatique :
définissez une durée de conservation adaptée à vos obligations de protection des données.
Derrière un proxy, l'adresse IP enregistrée est celle du proxy.

## Base de données

Les migrations de `backend/migrations/` sont idempotentes et appliquées dans
l'ordre par `python -m scripts.migrate`. `00_schema_base.sql` est **déduit des
requêtes du code** (le schéma initial n'était pas versionné) : si une base
existante possède déjà ces tables avec un schéma plus riche, elle n'est pas
modifiée (`IF NOT EXISTS`).

## Tests

```powershell
cd backend
python -m pytest -q
```

`test_api_basic.py` et `test_student_helpers.py` n'utilisent pas la base.
`test_db_flow.py` exécute un parcours complet sur la base de `DATABASE_URL`
avec des données préfixées `ZZTEST`/`ZZT`, supprimées avant et après le test.

## Sécurité et données personnelles

`/api/verifier` renvoie des données personnelles (dont `allergies_affection`)
sans authentification, et les demandes de correction sont ouvertes : comportement
hérité de l'application d'origine. Utilisez HTTPS et limitez l'accès réseau en
production. Les comptes administrateurs (mots de passe hachés scrypt, sessions signées et
expirantes) protègent les routes d'administration mais n'authentifient pas les étudiants.
