# API Concours Génie UPB

L'application est entièrement exécutée par FastAPI et utilise PostgreSQL.
L'interface candidat, le panneau administrateur et le scanner QR sont servis
par le même serveur. La documentation interactive est disponible sur
`/docs` et le schéma OpenAPI sur `/openapi.json`.
La route `GET /v1/models` répond au format OpenAI avec un catalogue vide ;
aucun modèle d'IA ni endpoint de génération de texte n'est configuré.

## Démarrage local sous Windows

Prérequis : Python 3.10+ et une base PostgreSQL existante avec les tables
principales `etudiant`, `filiere`, `niveau` et `candidat_concours`.

Si les migrations SQL ne sont pas encore appliquées, exécuter une fois, dans
cet ordre, depuis PowerShell :

```powershell
psql -h localhost -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\01_pointage.sql
psql -h localhost -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\02_etudiant_contact.sql
psql -h localhost -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\03_inscription_migrations.sql
psql -h localhost -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\04_fastapi_migration.sql
```

Les scripts ne créent pas le schéma principal. Sauvegarder la base avant toute
migration et demander le schéma initial à son administrateur si nécessaire.

Installer et lancer l'application :

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:UPB_DB_HOST = "localhost"
$env:UPB_DB_PORT = "5432"
$env:UPB_DB_NAME = "upb_concours"
$env:UPB_DB_USER = "upb_api"
$env:UPB_DB_PASS = "mot-de-passe-de-la-base"
$env:UPB_ADMIN_TOKEN = (python generate_admin_token.py)
$env:UPB_CORS_ORIGINS = "http://127.0.0.1:8000"
uvicorn main:app --reload
```

Ouvrir `http://127.0.0.1:8000` pour l'application ou
`http://127.0.0.1:8000/docs` pour la documentation API. Ne pas mettre les
secrets dans le dépôt ; `.env.example` ne contient que des valeurs fictives.

## Routes API

| Méthode | Chemin | Accès | Fonction |
|---|---|---|---|
| POST | `/api/admin/auth/login` | Public | Échanger le secret administrateur contre une session temporaire |
| POST | `/api/admin/auth/verify` | Admin | Vérifier une session Bearer temporaire |
| POST | `/api/students/verify` | Public | Retrouver un dossier par matricule |
| POST | `/api/student-registration-requests` | Public | Soumettre un nouveau dossier à approbation |
| POST | `/api/student-change-requests` | Public | Proposer des corrections à approuver |
| GET | `/api/admin/students` | Admin | Rechercher et paginer les dossiers |
| PATCH | `/api/admin/students/{id}` | Admin | Modifier directement les coordonnées |
| POST | `/api/admin/students/import` | Admin | Importer des étudiants par CSV |
| GET, POST | `/api/admin/student-requests` | Admin | Lister, approuver ou refuser les demandes |
| GET, POST | `/api/admin/attendance` | Admin | Consulter ou enregistrer un pointage |
| GET | `/v1/models` | Public | Retourner un catalogue OpenAI vide (aucun modèle fourni) |

Les endpoints d'administration nécessitent
`Authorization: Bearer <UPB_ADMIN_TOKEN>`. Le jeton doit contenir au moins
Au moins 43 caractères ; générez-le avec le script ci-dessous et gardez-le
uniquement dans la configuration privée du serveur.

Générez le secret administrateur localement avec
`python generate_admin_token.py`. Configurez sa valeur dans l'environnement
privé du serveur sous `UPB_ADMIN_TOKEN` et saisissez-la à la connexion
administrateur. Le serveur l'échange contre une session signée qui expire après
huit heures ; seul ce jeton temporaire est conservé dans `sessionStorage`.
Les routes d'administration refusent le secret maître lui-même. Ce mécanisme
utilise un seul secret partagé, pas encore des comptes nominatifs par
administrateur.

## Règles métier

- La recherche par nom est uniquement disponible dans l'espace administrateur ;
  le portail public recherche par matricule.
- Lorsqu'aucun dossier n'existe, le candidat remplit le formulaire de création.
  La demande n'ajoute pas immédiatement de ligne à `etudiant` : un
  administrateur doit l'approuver. Le matricule proposé est calculé à partir du
  nom, des prénoms et de la date de naissance.
- Les corrections soumises par un candidat restent en attente et ne changent
  pas la fiche tant qu'un administrateur ne les a pas approuvées.
- Le niveau `Master` est ajouté à la table `niveau` par
  `04_fastapi_migration.sql`.
- L'import CSV accepte jusqu'à 5 Mo et 5 000 lignes. Une validation échouée
  annule l'import entier.
- Un candidat ne peut être pointé qu'une fois par date.
- Le portail public ne permet plus l'inscription au concours. Les anciens
  enregistrements de candidats restent dans PostgreSQL pour conserver
  l'historique et permettre le suivi des présences.

## Configuration et déploiement

Configurer les variables d'environnement sur l'hôte FastAPI :

| Variable | Rôle |
|---|---|
| `UPB_DB_HOST` | Hôte PostgreSQL |
| `UPB_DB_PORT` | Port PostgreSQL, par défaut `5432` |
| `UPB_DB_NAME` | Nom de la base |
| `UPB_DB_USER` | Utilisateur PostgreSQL dédié |
| `UPB_DB_PASS` | Mot de passe PostgreSQL |
| `UPB_ADMIN_TOKEN` | Jeton administrateur |
| `UPB_CORS_ORIGINS` | Origines autorisées séparées par des virgules |

En production, ne pas exposer Uvicorn directement sur Internet. Placer l'API
derrière un proxy HTTPS configuré pour le domaine de déploiement, rediriger
HTTP vers HTTPS et ne rendre public que le port HTTPS. Restreindre les origines
CORS au domaine réel et l'accès réseau à PostgreSQL. Le certificat HTTPS ne
peut être configuré qu'une fois le domaine et l'hébergement choisis.
Ne pas exposer les exports ou les données étudiantes sans autorisation.
La génération intégrée à la commande ci-dessus convient au lancement local.
En production, générez le secret une seule fois et conservez cette même valeur
dans la configuration persistante et privée du service ; ne lancez pas le
générateur à chaque redémarrage.

Les réponses publiques contiennent des données personnelles et des informations
sensibles comme les allergies ; utiliser HTTPS et limiter les informations
affichées au besoin métier.

La limitation de tentatives de connexion doit aussi être configurée au niveau
du proxy ou de la plateforme d'hébergement. La restriction actuelle de
l'administration est l'exigence d'un secret fort et d'une session temporaire ;
elle n'est pas une restriction par adresse IP.
