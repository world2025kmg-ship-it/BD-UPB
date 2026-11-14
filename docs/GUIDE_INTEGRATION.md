# Guide d'intégration — vérifier l'identité d'un étudiant UPB

Ce guide s'adresse aux développeurs de sites externes qui veulent confirmer
automatiquement qu'une personne correspond à un étudiant enregistré dans la base
du concours Génie UPB.

- Documentation interactive (Swagger UI) : `{BASE_URL}/docs` — ReDoc : `{BASE_URL}/redoc`
- Contrat machine : `{BASE_URL}/openapi.json` (copie dans [openapi.json](openapi.json)),
  importable dans Postman, Insomnia ou un générateur de client.
- **Accès à la documentation soumis à autorisation** : le navigateur demande un
  identifiant et un mot de passe (HTTP Basic). Le nom d'utilisateur est libre ;
  le mot de passe vous est remis par l'administrateur de l'UPB sur demande.
  Avec `curl` : `curl -u integrateur:MOT_DE_PASSE {BASE_URL}/openapi.json`.
- `{BASE_URL}` est l'URL HTTPS de production communiquée par l'UPB. En local :
  `http://127.0.0.1:8000`.

---

## 1. En bref

La vérification repose sur **un seul appel, sans jeton** :

```
POST {BASE_URL}/api/verifier
Content-Type: application/json

{"matricule": "KOUA1703980001"}
```

Réponse `200` si l'étudiant existe :

```json
{
  "status": "success",
  "message": "Vos informations ont été retrouvées.",
  "data": {
    "matricule": "KOUA1703980001",
    "nom": "Kouadio",
    "prenoms": "Alain",
    "date_naissance": "1998-03-17",
    "telephone": "0701020304",
    "email": "alain@exemple.ci",
    "sexe": "M",
    "allergies_affection": null,
    "filiere": "Génie Informatique",
    "niveau": "Licence 3"
  }
}
```

Réponse `404` si le matricule est inconnu :

```json
{"status": "error", "message": "Matricule non reconnus dans la base I-UPB."}
```

L'API **ne compare pas** la date de naissance : c'est **votre serveur** qui
compare les données renvoyées avec celles que l'utilisateur a saisies (§3).

> Les routes d'administration (`/api/get_etudiants`, `/api/pointage`, etc.)
> exigent une connexion administrateur (identifiant + mot de passe). **Ces
> accès ne sont jamais fournis aux sites externes** et ne servent pas à la
> vérification d'identité.

---

## Cas particulier : le site officiel d'inscription au concours

Le site d'inscription au concours Génie UPB dispose d'une **clé d'API** (remise par
l'administrateur, à ne jamais exposer dans un navigateur). Il appelle :

```
POST {BASE_URL}/api/partenaire/verifier
X-API-Key: upb_xxxxxxxxxxxxxxxx
Content-Type: application/json

{"matricule": "KOUA1703980001", "numero_candidat": "OFFICIEL-0042"}
```

`numero_candidat` est facultatif. Cet appel **vérifie l'étudiant et le marque inscrit au
concours** dans la base UPB ; il doit donc être fait **une fois l'inscription validée côté
site officiel**. Il est idempotent : le répéter ne crée pas de doublon.

```json
{
  "status": "success",
  "message": "Étudiant vérifié et inscrit au concours.",
  "data": { "matricule": "KOUA1703980001", "nom": "Kouadio", "prenoms": "Alain",
            "date_naissance": "1998-03-17", "telephone": "0701020304", "email": "alain@exemple.ci",
            "sexe": "M", "allergies_affection": null, "filiere": "Génie Informatique", "niveau": "Licence 3" },
  "inscription": { "numero_candidat": "OFFICIEL-0042", "statut": "EN_ATTENTE", "deja_inscrit": false }
}
```

- `inscription.numero_candidat` : le numéro fourni à la **première** inscription (un numéro
  `GENIE-2026-XXXXX` est généré s'il est absent). Un numéro fourni ensuite ne remplace jamais
  le numéro déjà enregistré : c'est celui renvoyé qui fait foi.
- `inscription.deja_inscrit` : `true` si l'étudiant était déjà inscrit avant l'appel.
- Erreurs : `400` matricule absent/trop long ou `numero_candidat` > 40 caractères ;
  `401` clé absente, invalide ou désactivée ; `404` `Matricule non reconnus dans la base I-UPB.` ;
  `500` erreur serveur (réessayer, l'appel étant idempotent).

---

## 2. Le matricule

Le matricule est calculé ainsi (sans accents, en majuscules) :

```
3 premières lettres du nom + 1re lettre du prénom + jjmmaa de naissance + 0001
Kouadio Alain, né le 17/03/1998  →  KOUA 1703 98 0001  →  KOUA1703980001
```

- Longueur maximale : 40 caractères. L'API ignore les espaces autour et la casse.
- Un matricule absent, vide, non textuel ou trop long renvoie `400`
  (`"Matricule requis."`).

---

## 3. Procédure recommandée (côté serveur)

1. Votre formulaire demande : **matricule, nom, prénoms, date de naissance**.
2. Votre **serveur** (jamais le navigateur) appelle `POST /api/verifier`.
3. Sur `404` → refusez : « étudiant non reconnu ».
4. Sur `200` → comparez, après normalisation (casse, accents, espaces,
   tirets), `nom`, `prenoms` et `date_naissance` avec la saisie.
5. Ne transmettez au navigateur **que le résultat** (`vérifié` / `non vérifié`),
   éventuellement le nom. Ne relayez pas la réponse brute.
6. Ne conservez que ce dont vous avez besoin (idéalement un booléen horodaté).

Normalisation à appliquer des deux côtés : décomposer en NFD, retirer les
signes diacritiques, passer en majuscules, remplacer tout caractère non
alphanumérique par un espace, réduire les espaces multiples.
La date renvoyée est au format `AAAA-MM-JJ` ; convertissez la saisie dans ce format.

### Exemple Python (`requests`)

```python
import re
import unicodedata
from datetime import datetime

import requests

BASE_URL = "https://api.exemple-upb.ci"  # à remplacer


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def verify_student(matricule: str, nom: str, prenoms: str, date_naissance: str) -> bool:
    """date_naissance saisie au format JJ/MM/AAAA."""
    try:
        response = requests.post(
            f"{BASE_URL}/api/verifier", json={"matricule": matricule}, timeout=10
        )
    except requests.RequestException:
        raise RuntimeError("Service UPB indisponible, réessayez plus tard.")

    if response.status_code == 404:
        return False
    if response.status_code == 400:
        return False  # matricule mal formé
    response.raise_for_status()  # 5xx : ne pas conclure

    student = response.json()["data"]
    declared_date = datetime.strptime(date_naissance, "%d/%m/%Y").date().isoformat()
    return (
        norm(student["nom"]) == norm(nom)
        and norm(student["prenoms"]) == norm(prenoms)
        and student["date_naissance"] == declared_date
    )
```

### Exemple Node.js (≥ 18)

```js
const BASE_URL = "https://api.exemple-upb.ci"; // à remplacer

const norm = (s) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "")
   .toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim();

export async function verifyStudent({ matricule, nom, prenoms, dateNaissance /* AAAA-MM-JJ */ }) {
  const res = await fetch(`${BASE_URL}/api/verifier`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ matricule }),
    signal: AbortSignal.timeout(10_000),
  });
  if (res.status === 404 || res.status === 400) return false;
  if (!res.ok) throw new Error(`Service UPB indisponible (${res.status})`);

  const { data } = await res.json();
  return norm(data.nom) === norm(nom)
      && norm(data.prenoms) === norm(prenoms)
      && data.date_naissance === dateNaissance;
}
```

### Exemple `curl`

```sh
curl -X POST "$BASE_URL/api/verifier" \
  -H "Content-Type: application/json" \
  -d '{"matricule":"KOUA1703980001"}'
```

---

## 4. Codes de réponse

| Code | Signification | Que faire |
|---|---|---|
| `200` | Étudiant trouvé, `data` renseigné | Comparer l'identité (§3) |
| `400` | `matricule` absent, vide, non textuel ou > 40 caractères | Corriger la saisie |
| `404` | Matricule inconnu (`Matricule non reconnus dans la base I-UPB.`) | Refuser la vérification |
| `500` | Erreur serveur ou base de données | Ne pas conclure ; réessayer plus tard |
| `404`/`405` (JSON) | Mauvais chemin ou mauvaise méthode | Vérifier l'URL et `POST` |

Toutes les erreurs ont la forme `{"status": "error", "message": "..."}`.
Appliquez un délai maximal (10 s conseillé) et, pour les `5xx` ou les erreurs
réseau, un nouvel essai avec attente progressive (1 s, 2 s, 4 s) limité à 3 essais.

---

## 5. Ce que la vérification prouve — et ne prouve pas

Lisez ce point avant de fonder une décision (accès, paiement, droit) sur ce
contrôle :

- **Elle prouve** que les informations fournies (matricule, nom, prénoms, date
  de naissance) correspondent à un dossier étudiant existant à l'UPB.
- **Elle ne prouve pas** que la personne devant l'écran est cet étudiant. Le
  matricule est *dérivé* du nom, du prénom et de la date de naissance : quiconque
  connaît ces trois éléments peut le reconstituer. Ce n'est donc pas un secret,
  et il n'y a ni mot de passe, ni code envoyé à l'étudiant dans ce flux.
- Pour un enjeu sensible, ajoutez un second facteur de votre côté (par exemple
  un code envoyé à l'e-mail ou au téléphone renvoyés par l'API et confirmé par
  l'utilisateur, ou une pièce d'identité).

---

## 6. Données personnelles et sécurité

La réponse contient des données personnelles, dont téléphone, e-mail et
`allergies_affection` (donnée de santé potentielle).

- Appelez l'API **depuis votre serveur uniquement** ; n'intégrez pas l'appel dans
  du JavaScript de navigateur ou une application mobile distribuée (la réponse
  complète serait visible par l'utilisateur et par toute personne qui
  intercepte la page). L'API autorise techniquement les appels navigateur
  (CORS ouvert), mais ce n'est pas recommandé.
- Utilisez exclusivement **HTTPS**.
- N'enregistrez pas la réponse brute, ne la journalisez pas, n'utilisez pas
  téléphone, e-mail et allergies à d'autres fins que la vérification, et
  informez les étudiants de ce traitement conformément à la réglementation
  applicable sur la protection des données.
- **Limitez le débit** de votre propre formulaire (par adresse IP et par
  matricule) : l'API ne comporte actuellement pas de limitation de débit, et une
  énumération de matricules permettrait de collecter des données.
- Respectez un usage raisonnable : pas de balayage de matricules, pas de
  vérifications en masse sans accord préalable de l'UPB.

---

## 7. Générer un client à partir du contrat

```sh
# Exemple avec openapi-generator (client Python)
openapi-generator-cli generate -i {BASE_URL}/openapi.json -g python -o client-upb
```

Le contrat décrit toutes les routes ; seules celles du tag **Portail étudiant**
sont publiques, et seule `POST /api/verifier` est utile à la vérification
d'identité. Les routes marquées d'un cadenas (session administrateur) vous répondront `401`.

---

## 8. Check-list de mise en production

- [ ] Appel `POST /api/verifier` effectué côté serveur, en HTTPS, avec délai maximal.
- [ ] Comparaison normalisée du nom, des prénoms et de la date de naissance.
- [ ] Aucun renvoi de la réponse brute au navigateur ; aucun stockage superflu.
- [ ] Limitation de débit sur votre formulaire.
- [ ] Gestion des `400`, `404`, `5xx` et des erreurs réseau distincte (un `5xx` n'est pas un refus).
- [ ] Second facteur si la décision est sensible (§5).

Contact technique et URL de production : à demander à l'équipe du concours Génie UPB.
