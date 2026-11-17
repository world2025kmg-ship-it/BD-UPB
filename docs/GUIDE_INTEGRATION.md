# Guide d'intégration — vérifier un étudiant UPB depuis votre site

Ce guide s'adresse aux développeurs de sites externes qui veulent confirmer
automatiquement qu'un matricule correspond à un étudiant enregistré dans la base
de données des étudiants de l'UPB (portée par l'incubateur).

**Le matricule suffit.** Votre site n'a pas besoin de demander à l'utilisateur son
nom, ses prénoms ou sa date de naissance : un seul appel avec le matricule indique
si l'étudiant existe.

- Documentation interactive (Swagger UI) : `{BASE_URL}/docs` — ReDoc : `{BASE_URL}/redoc`
- Contrat machine : `{BASE_URL}/openapi.json` (copie dans [openapi.json](openapi.json)),
  importable dans Postman, Insomnia ou un générateur de client.
- **Accès à la documentation soumis à autorisation** : le navigateur demande un
  identifiant et un mot de passe (HTTP Basic). Le nom d'utilisateur est libre ;
  le mot de passe vous est remis par l'administrateur sur demande.
  Avec `curl` : `curl -u integrateur:MOT_DE_PASSE {BASE_URL}/openapi.json`.
- `{BASE_URL}` est l'URL HTTPS de production communiquée par l'Incubateur UPB. En local :
  `http://127.0.0.1:8000`.

---

## 1. Obtenir un accès

Les sites externes sont gérés par l'administrateur de la base, depuis l'onglet
**Intégrations** de la page d'administration. Il crée une *intégration* à votre nom et
vous remet une **clé d'API** (préfixe `upb_`), affichée une seule fois. Il choisit pour
votre site :

| Réglage | Effet |
|---|---|
| **Permission** | *Vérification seule* : lit l'identité, ne modifie rien. *Vérification et inscription* : marque aussi l'étudiant inscrit au concours (réservé au site officiel d'inscription). |
| **Données transmises** | Les champs de l'étudiant que vous recevez. Un accès peut être limité au **seul matricule** : vous savez alors seulement si l'étudiant existe. |
| **Adresses IP autorisées** | Si renseignées, seuls vos serveurs (adresses ou réseaux CIDR listés) peuvent utiliser la clé. |
| **Quota** | Nombre d'appels par minute. |
| **Expiration** | Une clé peut avoir une date de fin. Vous pouvez avoir plusieurs clés (rotation, §9). |

Transmettez la clé à votre serveur uniquement. **Elle ne doit jamais apparaître dans
une page web, une application mobile ou un dépôt de code.**

---

## 2. Vérifier un étudiant avec son seul matricule

```
POST {BASE_URL}/api/partenaire/verifier
X-API-Key: upb_xxxxxxxxxxxxxxxx
Content-Type: application/json

{"matricule": "KOUA1703980001"}
```

Réponse `200` : le matricule existe.

```json
{
  "status": "success",
  "message": "Étudiant vérifié.",
  "verifie": true,
  "correspondance": null,
  "data": { "matricule": "KOUA1703980001" },
  "inscription": null
}
```

- `verifie: true` : **c'est la réponse à votre question** (le matricule existe).
- `data` : seulement les champs autorisés pour votre intégration. Avec un accès limité
  au matricule, vous ne recevez rien d'autre. Selon l'autorisation, vous pouvez aussi
  recevoir `nom`, `prenoms`, `date_naissance`, `filiere`, `niveau`, et, pour les
  données sensibles, `sexe`, `email`, `telephone`, `allergies_affection`.
- `inscription` : `null` si l'étudiant n'est pas inscrit au concours ; sinon
  `{numero_candidat, statut, deja_inscrit}`.

Réponse `404` : le matricule n'existe pas.

```json
{"status": "error", "message": "Matricule non reconnus dans la base I-UPB."}
```

C'est tout ce qu'il faut pour un contrôle d'existence. Les sections suivantes sont
**facultatives**.

---

## 3. Confirmer l'identité sans rien demander de plus (facultatif)

Si votre formulaire a de toute façon collecté le nom, les prénoms ou la date de naissance,
vous pouvez les envoyer **en plus du matricule**. L'API répond, champ par champ, s'ils
correspondent au dossier, **sans révéler les valeurs du dossier** :

```json
{"matricule": "KOUA1703980001", "nom": "kouadio", "date_naissance": "17/03/1998"}
```

```json
{ "status": "success", "message": "Étudiant vérifié.", "verifie": true,
  "correspondance": { "nom": true, "date_naissance": true },
  "data": { "matricule": "KOUA1703980001" }, "inscription": null }
```

- Seuls les champs envoyés sont comparés ; aucun n'est obligatoire.
- La comparaison ignore la casse, les accents, les espaces et les tirets. La date est
  acceptée en `AAAA-MM-JJ`, `JJ/MM/AAAA` ou `JJ-MM-AAAA`.
- C'est le moyen de contrôler une identité **sans recevoir de données personnelles** :
  un accès limité au matricule suffit.

---

## 4. Inscription au concours (site officiel uniquement)

Avec la permission **Vérification et inscription**, le même appel **marque l'étudiant
inscrit au concours** s'il ne l'est pas encore. Il doit donc être fait **une fois
l'inscription validée côté site officiel**. L'appel est idempotent : le répéter ne
crée pas de doublon. `numero_candidat` est facultatif.

```json
{"matricule": "KOUA1703980001", "numero_candidat": "OFFICIEL-0042"}
```

```json
{ "status": "success", "message": "Étudiant vérifié et inscrit au concours.", "verifie": true,
  "correspondance": null, "data": { "matricule": "KOUA1703980001" },
  "inscription": { "numero_candidat": "OFFICIEL-0042", "statut": "EN_ATTENTE", "deja_inscrit": false } }
```

- Le numéro fourni est enregistré à la **première** inscription (sinon `GENIE-2026-XXXXX`
  est généré). Un numéro fourni ensuite ne remplace jamais celui déjà enregistré :
  c'est celui renvoyé qui fait foi.
- `deja_inscrit: true` : l'étudiant était déjà inscrit avant l'appel.
- Envoyer `numero_candidat` sans cette permission renvoie `403`.

---

## 5. Le matricule

Le matricule est calculé ainsi (sans accents, en majuscules) :

```
3 premières lettres du nom + 1re lettre du prénom + jjmmaa de naissance + 0001
Kouadio Alain, né le 17/03/1998  →  KOUA 1703 98 0001  →  KOUA1703980001
```

L'API ignore les espaces autour et la casse. Longueur maximale : 40 caractères.

---

## 6. Codes de réponse

| Code | Signification | Que faire |
|---|---|---|
| `200` | Matricule trouvé (`verifie: true`) | Poursuivre |
| `400` | `matricule` absent, vide, non textuel ou > 40 caractères ; champ facultatif mal typé | Corriger la requête |
| `401` | Clé absente, inconnue, révoquée, expirée, ou intégration désactivée | Vérifier la clé ; contacter l'administrateur |
| `403` | Adresse IP non autorisée pour cette clé, ou `numero_candidat` sans permission d'inscription | Appeler depuis un serveur autorisé |
| `404` | Matricule inconnu (`Matricule non reconnus dans la base I-UPB.`) | Refuser la vérification |
| `429` | Quota d'appels par minute dépassé | Attendre le délai indiqué dans le message |
| `500` | Erreur serveur ou base de données | Ne pas conclure ; réessayer plus tard |

Toutes les erreurs ont la forme `{"status": "error", "message": "..."}`. Appliquez un
délai maximal (10 s conseillé) et, pour les `5xx`, `429` ou erreurs réseau, un nouvel
essai avec attente progressive (1 s, 2 s, 4 s), limité à 3 essais.
Une réponse `5xx` n'est **pas** un refus : ne concluez pas que l'étudiant n'existe pas.

---

## 7. Exemples

### Python (`requests`)

```python
import requests

BASE_URL = "https://api.exemple-upb.ci"   # à remplacer
API_KEY = "upb_..."                        # variable d'environnement, jamais dans le code


def student_exists(matricule: str) -> bool:
    try:
        response = requests.post(
            f"{BASE_URL}/api/partenaire/verifier",
            json={"matricule": matricule},
            headers={"X-API-Key": API_KEY},
            timeout=10,
        )
    except requests.RequestException:
        raise RuntimeError("Service UPB indisponible, réessayez plus tard.")
    if response.status_code in (400, 404):
        return False                       # matricule mal formé ou inconnu
    response.raise_for_status()            # 401/403/429/5xx : ne pas conclure
    return response.json()["verifie"]
```

Avec confirmation facultative du nom et de la date (sans recevoir ces données) :

```python
body = {"matricule": matricule, "nom": nom, "date_naissance": "17/03/1998"}
result = requests.post(url, json=body, headers=headers, timeout=10).json()
identity_confirmed = result["verifie"] and all(result["correspondance"].values())
```

### Node.js (≥ 18)

```js
const BASE_URL = "https://api.exemple-upb.ci"; // à remplacer
const API_KEY = process.env.UPB_API_KEY;       // jamais dans le code

export async function studentExists(matricule) {
  const res = await fetch(`${BASE_URL}/api/partenaire/verifier`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ matricule }),
    signal: AbortSignal.timeout(10_000),
  });
  if (res.status === 400 || res.status === 404) return false;
  if (!res.ok) throw new Error(`Service UPB indisponible (${res.status})`);
  return (await res.json()).verifie;
}
```

### `curl`

```sh
curl -X POST "$BASE_URL/api/partenaire/verifier" \
  -H "X-API-Key: $UPB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"matricule":"KOUA1703980001"}'
```

---

## 8. Ce que la vérification prouve — et ne prouve pas

- **Elle prouve** que le matricule (et, si vous les envoyez, le nom, les prénoms ou la date
  de naissance) correspond à un dossier étudiant existant à l'UPB.
- **Elle ne prouve pas** que la personne devant l'écran est cet étudiant. Le matricule est
  *dérivé* du nom, du prénom et de la date de naissance : quiconque connaît ces éléments
  peut le reconstituer. Ce n'est donc pas un secret, et il n'y a ni mot de passe, ni code
  envoyé à l'étudiant dans ce flux.
- Pour un enjeu sensible (accès, paiement, droit), ajoutez un second facteur de votre côté :
  par exemple un code envoyé à l'e-mail ou au téléphone de l'étudiant (si l'administrateur
  vous a autorisé ces champs) et confirmé par l'utilisateur, ou une pièce d'identité.

---

## 9. Sécurité et données personnelles

- Appelez l'API **depuis votre serveur uniquement**, en **HTTPS**. Ne placez jamais la clé
  dans du JavaScript de navigateur, une application mobile ou un dépôt de code.
- **Demandez le minimum** : si le matricule suffit, faites limiter votre accès au seul
  matricule. Les données sensibles (e-mail, téléphone, sexe, allergies) ne doivent être
  demandées que si votre service en a réellement besoin.
- N'enregistrez pas la réponse brute, ne la journalisez pas, et informez les étudiants
  de ce traitement conformément à la réglementation applicable sur la protection des
  données. Idéalement, ne conservez qu'un résultat horodaté (« vérifié » / « non vérifié »).
- **Limitez le débit** de votre propre formulaire (par adresse IP et par matricule) : une
  énumération de matricules permettrait de collecter des données. Pas de balayage de
  matricules ni de vérifications en masse sans accord préalable de l'UPB.
- **Rotation des clés** : l'administrateur peut vous remettre une seconde clé tant que la
  première est valide ; basculez votre serveur, puis il révoque l'ancienne, sans coupure.
  En cas de fuite, demandez la révocation immédiate : la clé cesse de fonctionner aussitôt.
- Toutes vos requêtes sont enregistrées dans le journal d'activité de l'UPB (date, adresse
  IP, matricule vérifié, résultat). La clé elle-même n'y figure jamais.

---

## 10. Générer un client à partir du contrat

```sh
# Exemple avec openapi-generator (client Python)
openapi-generator-cli generate -i {BASE_URL}/openapi.json -g python -o client-upb
```

Le contrat décrit toutes les routes ; seule `POST /api/partenaire/verifier` (tag
**Partenaires**) vous concerne. Les routes marquées d'un cadenas sont réservées aux
administrateurs.

> La route publique `POST /api/verifier` sert au **portail des candidats** (un étudiant qui
> consulte son propre dossier). Elle renvoie le dossier complet sans clé : **ne l'utilisez
> pas pour une intégration**, utilisez la route à clé ci-dessus, qui limite les données
> transmises, applique un quota et trace l'usage.

---

## 11. Check-list de mise en production

- [ ] Clé d'API stockée côté serveur (variable d'environnement ou coffre), jamais dans le code ni le navigateur.
- [ ] Appel `POST /api/partenaire/verifier` effectué depuis un serveur, en HTTPS, avec délai maximal.
- [ ] Accès limité au minimum de données nécessaire (idéalement le seul matricule).
- [ ] Gestion distincte de `404` (inconnu), `401/403` (accès), `429` (quota) et `5xx` (jamais un refus).
- [ ] Aucun renvoi de la réponse brute au navigateur ; aucun stockage superflu.
- [ ] Limitation de débit sur votre propre formulaire.
- [ ] Second facteur si la décision est sensible (§8).

Contact technique et URL de production : à demander à l'équipe de l'Incubateur UPB.
