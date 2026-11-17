# Guide de déploiement — Netlify (frontend) + Render (backend) + Neon + UptimeRobot

Ce guide met en ligne l'application avec **uniquement les tableaux de bord** des
plateformes : il n'y a ni `netlify.toml`, ni `render.yaml`, ni autre fichier de
configuration d'hébergement dans le dépôt.

```
Navigateur ──► Netlify  (pages HTML : dossier frontend/)
     │              │  appels /api/…  (URL réglée dans frontend/config.js)
     ▼              ▼
Sites externes ──► Render  (API FastAPI : dossier backend/) ──► Neon (PostgreSQL)
                      ▲
UptimeRobot ──────────┘  GET /api/sante toutes les 5 min (garde Render éveillé)
```

| Élément | Où | Rôle |
|---|---|---|
| Pages (candidats, admin, scanner) | Netlify | Fichiers statiques du dossier `frontend/` |
| API | Render (service web) | `uvicorn app.main:app` depuis `backend/` |
| Base de données | Neon | PostgreSQL (déjà en place pour le développement) |
| Supervision | UptimeRobot | Empêche l'endormissement de Render |

---

## 0. Avant de commencer

**Comptes** : GitHub (ou GitLab/Bitbucket) contenant le dépôt, Netlify, Render, Neon, UptimeRobot.

**Quatre points à régler d'abord :**

1. **Séparez la base de production de celle de développement.** La suite de tests
   (`pytest`) écrit des données `ZZT…` dans la base pointée par `DATABASE_URL`. Ne la
   lancez **jamais** contre la production. Créez dans Neon une **base vide dédiée à la
   production** (nouveau projet, ou nouvelle base/branche vide) ; gardez l'actuelle pour le
   développement et les tests.
2. **Renouvelez le mot de passe Neon** utilisé jusqu'ici : il a été partagé en clair dans
   des conversations. Pour la production, utilisez des identifiants neufs.
3. **Ne commitez jamais `backend/.env`** (déjà ignoré par `.gitignore`). Les secrets de
   production se saisissent dans le tableau de bord Render, jamais dans le dépôt.
4. **Une seule version de Python partout** : l'application a été testée sous Python
   **3.12.10** (installation des dépendances et tests hors base réussis). Utilisez cette
   version sur Render (étape 3).

---

## 1. Neon : base de production

1. Dans Neon, créez la base de production (vide) et notez les **deux** URL de connexion :
   - **poolée** (hôte contenant `-pooler`) → servira à l'API sur Render (`DATABASE_URL`) ;
   - **directe** (sans `-pooler`) → servira aux migrations depuis votre poste (`DATABASE_URL_DIRECT`).
2. Depuis votre poste (PowerShell), appliquez le schéma et créez le premier administrateur.
   Les variables définies dans la session **priment sur** `backend/.env`, ce qui évite de
   toucher à votre configuration de développement :

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL        = "postgresql://…-pooler…/neondb?sslmode=require&channel_binding=require"
$env:DATABASE_URL_DIRECT = "postgresql://…(sans -pooler)…/neondb?sslmode=require&channel_binding=require"
python -m scripts.migrate                  # crée toutes les tables (idempotent)
python -m scripts.create_admin admin       # saisit le mot de passe au clavier (10 caractères minimum)
Remove-Item Env:DATABASE_URL, Env:DATABASE_URL_DIRECT
```

3. Vérifiez dans la console SQL de Neon que les tables existent (`etudiant`, `administrateur`,
   `partenaire`, `journal_activite`…). Les étudiants s'ajoutent ensuite depuis la page admin
   (import CSV).

---

## 2. Dépôt Git

```powershell
git status                         # rien de secret à ajouter
git ls-files | Select-String "\.env"   # doit n'afficher que backend/.env.example
git add -A
git commit -m "Application prête pour le déploiement"
git push origin main
```

Render et Netlify redéploient automatiquement à chaque `git push` sur la branche suivie.

---

## 3. Render : l'API

**New + → Web Service →** connectez le dépôt, puis renseignez :

| Champ | Valeur |
|---|---|
| Name | `upb-api` (devient `https://upb-api.onrender.com`) |
| Region | **Ohio (US East)** : proche de Neon `us-east-2`, donc moins de latence |
| Branch | `main` |
| **Root Directory** | `backend` |
| Runtime / Language | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |
| Advanced → **Health Check Path** | `/api/sante` |
| Advanced → Auto-Deploy | Yes |

**Variables d'environnement** (Environment → Add Environment Variable) :

| Variable | Valeur | Remarque |
|---|---|---|
| `PYTHON_VERSION` | `3.12.10` | Version complète exigée par Render |
| `DATABASE_URL` | URL **poolée** Neon de production | Obligatoire |
| `UPB_SECRET_KEY` | 64 caractères aléatoires | `python -c "import secrets; print(secrets.token_hex(32))"` ; la changer déconnecte tous les admins |
| `UPB_DOCS_PASSWORD` | mot de passe long | Accès à `/docs`, à remettre aux intégrateurs |
| `UPB_AUDIT_ENABLED` | `1` | Journal d'activité |
| `UPB_TRUSTED_PROXY_HOPS` | `1` | Nombre de proxys devant l'API ; à contrôler à l'étape 5 |
| `UPB_CORS_ORIGINS` | *(étape 5)* | URL du site Netlify |
| `UPB_SESSION_HOURS` | `8` | Facultatif |

À **ne pas** définir sur Render : `DATABASE_URL_DIRECT` (migrations seulement, depuis votre
poste) et `UPB_ADMIN_TOKEN` (obsolète).

Lancez le déploiement, puis vérifiez dans le navigateur :

- `https://upb-api.onrender.com/api/sante` → `{"status":"ok"}`
- `https://upb-api.onrender.com/api/sante?base=1` → `{"status":"ok"}` si la base est joignable
  (`503` sinon : vérifiez `DATABASE_URL`)
- `https://upb-api.onrender.com/docs` → demande un mot de passe (`UPB_DOCS_PASSWORD`)

> L'API sert aussi les pages à sa racine (`https://upb-api.onrender.com/`). Vous pouvez donc
> tester sans Netlify. Netlify reste le point d'entrée public recommandé pour les pages.

**Offre gratuite Render, à connaître** ([documentation](https://render.com/docs/free)) : un
service gratuit **s'endort après 15 minutes sans trafic** et met jusqu'à une minute à se
réveiller ; le quota est de **750 heures gratuites par mois et par espace de travail** ; le
système de fichiers est éphémère (l'application n'y stocke rien) ; Render peut redémarrer
un service gratuit à tout moment.

---

## 4. Netlify : les pages

### 4.1 Indiquer l'adresse de l'API

Les pages appellent `/api/…`. Hébergées sur Netlify, elles doivent viser Render. Éditez
**`frontend/config.js`** (fichier public : jamais de secret dedans) :

```js
window.UPB_API_URL = "https://upb-api.onrender.com";   // sans barre finale
```

puis `git add frontend/config.js && git commit -m "URL de l'API" && git push`.

### 4.2 Créer le site

**Add new site (ou « Add new project ») → Import an existing project →** choisissez le dépôt, puis (les intitulés exacts peuvent varier légèrement) :

| Champ | Valeur |
|---|---|
| Branch to deploy | `main` |
| Base directory | *(vide)* |
| Build command | *(vide)* : aucun build |
| **Publish directory** | `frontend` |

**Deploy.** Netlify publie à `https://<nom>.netlify.app` (renommable dans *Site configuration →
Change site name*). Pages : `/` (candidats), `/admin.html`, `/scanner.html`.

*Alternative sans Git* : *Add new site → Deploy manually*, puis glissez le dossier `frontend/`
(à refaire à chaque modification).

Domaine personnalisé et HTTPS : *Domain management*. Netlify fournit le certificat.

---

## 5. Relier Netlify à Render

### 5.1 Autoriser le site dans l'API (CORS)

Dans Render → Environment, renseignez `UPB_CORS_ORIGINS` avec l'adresse **exacte** du site
(sans barre finale ; plusieurs adresses séparées par des virgules) :

```
https://upb.netlify.app,https://inscription.mondomaine.ci
```

Enregistrez : Render redéploie. Sans cette étape, ou avec une adresse fausse, les pages
s'affichent mais toute action échoue (« Erreur de connexion avec le serveur » ; la console
du navigateur indique un blocage CORS).

### 5.2 Contrôler l'adresse IP vue par l'API

Derrière les proxys de Render, l'API ne voit d'abord que l'adresse du proxy. Elle sert pourtant
au **journal d'activité**, à la **limitation des essais de connexion** (5 échecs = blocage de
15 min : avec la mauvaise adresse, quelques échecs *de n'importe qui* bloqueraient *tout le
monde*) et à la **liste d'IP autorisées** des intégrations.

1. Connectez-vous à `https://<nom>.netlify.app/admin.html`.
2. Ouvrez l'onglet **Journal** : la ligne `Connexion administrateur` affiche l'adresse IP.
3. Comparez-la à votre IP publique (cherchez « what is my IP »).
4. Si elle est différente (adresse interne de Render ou d'un relais), changez
   `UPB_TRUSTED_PROXY_HOPS` (essayez `1`, puis `2`) jusqu'à voir **votre** adresse.

> **Limite de sécurité** : Render ajoute l'adresse de connexion à `X-Forwarded-For` sans effacer
> ce que le client y met. L'application lit l'en-tête **depuis la droite** pour ignorer toute
> valeur falsifiée en début d'en-tête, mais la fiabilité dépend du bon nombre de sauts. Pour les
> intégrations, traitez la liste d'IP autorisées comme une protection **complémentaire** de la
> clé d'API, jamais comme la seule.

---

## 6. UptimeRobot : garder Render éveillé

1. **Add New Monitor** :

| Champ | Valeur |
|---|---|
| Monitor Type | HTTP(s) |
| Friendly Name | `UPB API` |
| URL | `https://upb-api.onrender.com/api/sante` |
| Monitoring Interval | **5 minutes** (minimum du plan gratuit) |
| Alert contacts | votre e-mail |

2. Facultatif : un second moniteur de type **Keyword** sur la même URL, mot-clé `ok`, pour
   détecter une réponse anormale et pas seulement un code HTTP.

Pourquoi ça fonctionne : un appel toutes les 5 minutes est bien plus fréquent que le seuil de
15 minutes d'endormissement de Render. `/api/sante` répond aux méthodes **GET et HEAD**
(UptimeRobot peut utiliser l'une ou l'autre) et **ne touche pas la base** : Neon peut donc se
mettre en veille entre deux vraies requêtes (économie d'heures de calcul) ; la première requête
réelle après une période calme prend simplement environ une seconde de plus.

**Contrepartie** : un service éveillé en permanence consomme ≈ 744 h par mois, soit presque
tout le quota gratuit de **750 h par espace de travail**. Gardez **un seul** service Render
gratuit dans cet espace de travail ; avec deux services toujours éveillés, Render
suspendrait les services gratuits avant la fin du mois.

---

## 7. Mise en service : vérifications

Dans l'ordre, sur les adresses de production :

- [ ] `/api/sante?base=1` répond `ok` (Render ↔ Neon).
- [ ] Connexion à `/admin.html` avec l'identifiant créé à l'étape 1.
- [ ] Import d'un petit fichier CSV d'étudiants ; la liste et les statistiques s'affichent.
- [ ] Portail candidat (`/`) : un matricule connu affiche le dossier ; un inconnu affiche
      « Matricule non reconnus dans la base I-UPB. » et le formulaire.
- [ ] Onglet **Intégrations** : créer l'intégration du site d'inscription, copier la clé,
      puis tester :

```powershell
curl.exe -X POST https://upb-api.onrender.com/api/partenaire/verifier `
  -H "X-API-Key: upb_…" -H "Content-Type: application/json" `
  -d '{\"matricule\":\"KOUA1703980001\"}'
```

- [ ] Onglet **Journal** : les actions ci-dessus y figurent avec la bonne adresse IP (§5.2).
- [ ] Moniteur UptimeRobot au vert.

Aux sites externes, communiquez : l'**URL de l'API** (`https://upb-api.onrender.com`), leur
**clé** (canal sûr), le mot de passe de `/docs` et le guide
[GUIDE_INTEGRATION.md](GUIDE_INTEGRATION.md).

---

## 8. Exploitation

| Besoin | Marche à suivre |
|---|---|
| Mettre à jour le code | `git push` : Netlify (pages) et Render (API) redéploient seuls |
| Changer le schéma de la base | Lancer `python -m scripts.migrate` **depuis votre poste, avant** de pousser le code qui en dépend (l'offre gratuite de Render n'a pas de commande de pré-déploiement) |
| Lire les logs de l'API | Render → *Logs* |
| Nouvel administrateur / mot de passe oublié | `python -m scripts.create_admin IDENTIFIANT` (étape 1) ; la même commande réinitialise un mot de passe |
| Déconnecter tous les admins | Changer `UPB_SECRET_KEY` sur Render |
| Clé d'intégration compromise | Onglet Intégrations → *Révoquer* (effet immédiat), ou *Désactiver* l'intégration |
| Changer le mot de passe de `/docs` | Modifier `UPB_DOCS_PASSWORD` sur Render |
| Sauvegardes | Neon conserve un historique de restauration dont la durée dépend de votre offre : vérifiez-la, et faites au besoin un export `pg_dump` régulier |
| Taille du journal | Aucune purge automatique : surveillez l'espace utilisé dans Neon et fixez une durée de conservation adaptée à vos obligations de protection des données |

---

## 9. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| Les pages s'ouvrent mais toute action échoue (« Erreur de connexion… ») | CORS : `UPB_CORS_ORIGINS` absent ou différent de l'adresse Netlify (barre finale, `http` au lieu de `https`) | Corriger la variable, attendre le redéploiement |
| Idem, avec une erreur 404 sur `/api/…` côté Netlify | `frontend/config.js` encore vide | Renseigner `UPB_API_URL`, pousser |
| Première requête très lente (30 s à 1 min) | Service Render endormi ou redémarré | Vérifier le moniteur UptimeRobot (adresse, intervalle) |
| Build Render en échec | Mauvais *Root Directory* ou version de Python | Root Directory = `backend` ; `PYTHON_VERSION=3.12.10` |
| « Port scan timeout » / aucun port détecté | Commande de démarrage incorrecte | Utiliser exactement `--host 0.0.0.0 --port $PORT` |
| `/api/sante?base=1` renvoie 503 | `DATABASE_URL` erronée, ou base Neon en réveil | Vérifier l'URL (poolée, `sslmode=require`) ; réessayer |
| « Trop de tentatives échouées » pour tout le monde | Adresse IP mal détectée (§5.2) | Ajuster `UPB_TRUSTED_PROXY_HOPS` ; le blocage expire après 15 min |
| Connexion admin impossible (`503 Accès administrateur non configuré`) | `UPB_SECRET_KEY` absente ou trop courte (32 caractères minimum) | La définir sur Render |
| Connexion admin refusée alors que le mot de passe est bon | Administrateur créé dans une autre base | Refaire `create_admin` contre la base de **production** (étape 1) |
| `/docs` demande un mot de passe qui est refusé | `UPB_DOCS_PASSWORD` absent (10 caractères minimum) | La définir ; nom d'utilisateur libre |
| Un site externe reçoit 403 | Son IP n'est pas dans la liste de l'intégration, ou le nombre de sauts est faux | Corriger la liste (onglet Intégrations) ou le §5.2 |

---

## 10. Limites connues de cette architecture gratuite

- **Un seul processus, sans état partagé** : les compteurs de limitation de connexion et de
  quota des intégrations sont en mémoire et repartent à zéro à chaque redémarrage de Render.
- **Redémarrages possibles à tout moment** : une requête en cours peut échouer ; les sites
  externes doivent réessayer (voir leur guide). L'appel d'inscription est idempotent.
- **Latence** : l'API est aux États-Unis ; prévoyez une centaine de millisecondes de plus
  depuis l'Afrique de l'Ouest. Passer à une offre payante (service toujours actif, région plus
  proche) supprimerait le besoin d'UptimeRobot.
- **Pages publiques** : le site Netlify est accessible à tous ; seul l'accès administrateur
  est protégé (identifiant, mot de passe, limitation des essais, sessions de 8 h).

---

## Sources (offres et comportements des plateformes)

- Render, offre gratuite : <https://render.com/docs/free>
- Render, déploiement FastAPI : <https://render.com/docs/deploy-fastapi>
- UptimeRobot, offres : <https://uptimerobot.com/pricing>
- FastAPI derrière un proxy : <https://fastapi.tiangolo.com/advanced/behind-a-proxy/>
- Render et `X-Forwarded-For` (échange de la communauté) : <https://feedback.render.com/features/p/send-the-correct-xforwardedfor>

Les quotas et délais des offres gratuites évoluent : vérifiez-les sur ces pages avant de
vous engager.
