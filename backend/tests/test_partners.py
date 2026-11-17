"""Gestion des intégrations (sites externes) : création, permissions, clés, IP, quota, journal.

Tout est créé avec le préfixe « ZZT » et supprimé avant et après (base Neon réelle).
"""
import psycopg
import pytest
from fastapi.testclient import TestClient
from test_db_flow import (  # noqa: F401  (client = fixture admin + nettoyage des données de test)
    MATRICULE_ALICE,
    MATRICULE_BOB,
    client,
    import_csv,
)

from app import config, security
from app.main import create_app

ANON_IP = "203.0.113.5"  # adresse de test (documentation RFC 5737)


def purge():
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("DELETE FROM partenaire WHERE nom LIKE 'ZZT%'")
        conn.execute(
            "DELETE FROM journal_activite WHERE ip IN ('testclient', %s, '203.0.113.9') OR acteur LIKE 'ZZT%%'",
            (ANON_IP,),
        )
        conn.commit()


_ANONYMOUS: dict[str, TestClient] = {}  # un client (donc un pool de connexions) par adresse IP et par test


@pytest.fixture()
def admin(client):
    purge()
    security.partner_rate.reset()
    assert import_csv(client).status_code == 201  # Alice (ZZTA…) et Bob (ZZTB…)
    yield client
    for anonymous in _ANONYMOUS.values():
        anonymous.__exit__(None, None, None)
    _ANONYMOUS.clear()
    purge()
    security.partner_rate.reset()


def create(admin, **overrides):
    body = {"nom": "ZZTsite-externe", "permission": "VERIFICATION", **overrides}
    response = admin.post("/api/admin_partenaires", json=body)
    return response


def call(key, matricule=MATRICULE_ALICE, *, ip=ANON_IP, **extra):
    if ip not in _ANONYMOUS:
        _ANONYMOUS[ip] = TestClient(create_app(), client=(ip, 50000)).__enter__()
    return _ANONYMOUS[ip].post(
        "/api/partenaire/verifier", json={"matricule": matricule, **extra}, headers={"X-API-Key": key}
    )


def test_admin_routes_require_authentication(admin):
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/admin_partenaires").status_code == 401
        for path in ("", "/modifier", "/cles", "/supprimer"):
            assert anonymous.post("/api/admin_partenaires" + path, json={}).status_code == 401, path


def test_create_partner_returns_key_once_and_stores_only_hash(admin):
    response = create(admin, description="Site de test", site_url="https://exemple.ci", contact="it@exemple.ci")
    assert response.status_code == 201, response.text
    body = response.json()
    key = body["cle"]["valeur"]
    assert key.startswith("upb_") and body["cle"]["prefixe"] == key[:12]
    partner = body["partenaire"]
    assert partner["nom"] == "ZZTsite-externe" and partner["permission"] == "VERIFICATION" and partner["actif"] is True
    assert partner["champs"] == ["matricule", "nom", "prenoms", "date_naissance", "filiere", "niveau"]  # minimum par défaut
    assert partner["ips_autorisees"] == [] and partner["limite_par_minute"] is None
    assert partner["cree_par"] == "ZZTadmin" and len(partner["cles"]) == 1
    # La liste ne renvoie jamais la clé ; la base ne contient que son empreinte
    listing = admin.get("/api/admin_partenaires")
    assert listing.status_code == 200 and key not in listing.text
    assert "valeur" not in listing.text and "cle_hash" not in listing.text
    listed = [p for p in listing.json()["data"] if p["nom"] == "ZZTsite-externe"][0]
    assert listed["cles"][0]["statut"] == "ACTIVE" and listed["cles"][0]["prefixe"] == key[:12]
    assert listed["stats"] == {"appels_total": 0, "appels_24h": 0, "echecs_24h": 0, "inscrits_marques": 0, "derniere_activite": None}
    assert "sexe" in listing.json()["champs_disponibles"]
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        stored = conn.execute("SELECT cle_hash FROM partenaire_cle WHERE prefixe = %s", (key[:12],)).fetchone()[0]
    assert stored == security.hash_api_key(key) and key not in stored


@pytest.mark.parametrize("body,message", [
    ({"nom": "ab"}, "Le nom de l'intégration est obligatoire (3 à 80 caractères)."),
    ({"nom": "ZZTx", "permission": "TOUT"}, "Permission invalide (VERIFICATION ou INSCRIPTION)."),
    ({"nom": "ZZTx", "champs": ["mot_de_passe"]}, "Champs invalides."),
    ({"nom": "ZZTx", "site_url": "ftp://exemple.ci"}, "L'adresse du site doit commencer par http:// ou https://."),
    ({"nom": "ZZTx", "ips_autorisees": ["pas-une-ip"]}, "Adresse IP ou réseau invalide : pas-une-ip"),
    ({"nom": "ZZTx", "limite_par_minute": 0}, "Le quota par minute doit être un entier entre 1 et 100 000 (ou vide)."),
    ({"nom": "ZZTx", "expire_le": "2000-01-01"}, "La date d'expiration ne peut pas être dans le passé."),
    ({"nom": "ZZTx", "expire_le": "demain"}, "Date d'expiration invalide. Format attendu : AAAA-MM-JJ."),
])
def test_create_validation(admin, body, message):
    response = admin.post("/api/admin_partenaires", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["message"] == message
    assert admin.get("/api/admin_partenaires").json()["data"] == [] or all(
        not p["nom"].startswith("ZZTx") for p in admin.get("/api/admin_partenaires").json()["data"])


def test_duplicate_name_is_refused(admin):
    assert create(admin).status_code == 201
    response = create(admin, nom="zztSITE-EXTERNE")
    assert response.status_code == 409 and response.json()["message"] == "Une intégration porte déjà ce nom."


def test_verification_permission_is_read_only_and_filters_fields(admin):
    key = create(admin).json()["cle"]["valeur"]
    # Lecture seule : l'étudiant n'est PAS marqué inscrit ; seuls les champs autorisés sont renvoyés
    response = call(key)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Étudiant vérifié." and body["inscription"] is None
    assert set(body["data"]) == {"matricule", "nom", "prenoms", "date_naissance", "filiere", "niveau"}
    assert body["data"]["nom"] == "Zztest" and body["data"]["date_naissance"] == "1998-03-17"
    assert admin.get("/api/get_etudiants", params={"q": MATRICULE_ALICE}).json()["data"][0]["numero_candidat"] is None
    # Un numéro de candidat sans permission d'inscription : refusé
    refused = call(key, numero_candidat="X-1")
    assert refused.status_code == 403 and "pas autorisée à inscrire" in refused.json()["message"]
    assert call(key, "INCONNU").status_code == 404
    assert call(key, "").status_code == 400
    # Un étudiant déjà inscrit par ailleurs est signalé, sans rien modifier
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute(
            "INSERT INTO candidat_concours (etudiant_id, numero_candidat) "
            "SELECT id, 'OFFICIEL-1' FROM etudiant WHERE matricule = %s", (MATRICULE_BOB,))
        conn.commit()
    registered = call(key, MATRICULE_BOB).json()["inscription"]
    assert registered == {"numero_candidat": "OFFICIEL-1", "statut": "EN_ATTENTE", "deja_inscrit": True}


def test_inscription_permission_marks_registered_with_extra_fields(admin):
    created = create(admin, permission="INSCRIPTION", champs=["nom", "sexe", "email", "telephone"])
    assert created.json()["partenaire"]["champs"][0] == "matricule"  # le matricule est toujours inclus
    key = created.json()["cle"]["valeur"]
    body = call(key, numero_candidat="OFFICIEL-0042").json()
    assert body["message"] == "Étudiant vérifié et inscrit au concours."
    assert body["inscription"] == {"numero_candidat": "OFFICIEL-0042", "statut": "EN_ATTENTE", "deja_inscrit": False}
    assert set(body["data"]) == {"matricule", "nom", "sexe", "email", "telephone"}
    assert "date_naissance" not in body["data"] and "allergies_affection" not in body["data"]
    assert admin.get("/api/get_etudiants", params={"q": MATRICULE_ALICE}).json()["data"][0]["numero_candidat"] == "OFFICIEL-0042"
    again = call(key, numero_candidat="AUTRE").json()["inscription"]
    assert again["numero_candidat"] == "OFFICIEL-0042" and again["deja_inscrit"] is True


def test_update_permissions_and_activation(admin):
    created = create(admin).json()
    key, pid = created["cle"]["valeur"], created["partenaire"]["id"]
    assert call(key).json()["inscription"] is None

    response = admin.post("/api/admin_partenaires/modifier", json={
        "id": pid, "permission": "INSCRIPTION", "champs": ["email"], "limite_par_minute": 500,
        "description": "Mis à jour", "site_url": "https://nouveau.exemple.ci",
    })
    assert response.status_code == 200, response.text
    updated = response.json()["partenaire"]
    assert updated["permission"] == "INSCRIPTION" and updated["champs"] == ["matricule", "email"]
    assert updated["limite_par_minute"] == 500 and updated["updated_at"] and updated["nom"] == "ZZTsite-externe"
    body = call(key).json()
    assert set(body["data"]) == {"matricule", "email"} and body["inscription"]["deja_inscrit"] is False

    # Désactivation immédiate puis réactivation
    assert admin.post("/api/admin_partenaires/modifier", json={"id": pid, "actif": False}).status_code == 200
    assert call(key).status_code == 401
    assert admin.post("/api/admin_partenaires/modifier", json={"id": pid, "actif": True}).status_code == 200
    assert call(key).status_code == 200

    for bad in ({"id": pid}, {"id": pid, "actif": "oui"}, {"id": pid, "permission": "ROOT"}, {"id": 0, "actif": True}):
        assert admin.post("/api/admin_partenaires/modifier", json=bad).status_code == 422, bad
    assert admin.post("/api/admin_partenaires/modifier", json={"id": 999999, "actif": True}).status_code == 404


def test_key_rotation_expiry_and_revocation(admin):
    created = create(admin, libelle_cle="Production").json()
    first, pid = created["cle"], created["partenaire"]["id"]
    assert created["partenaire"]["cles"][0]["libelle"] == "Production"
    assert call(first["valeur"]).status_code == 200

    # Rotation : une seconde clé fonctionne pendant que la première reste valide
    response = admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "generer", "libelle": "Rotation"})
    assert response.status_code == 200, response.text
    second = response.json()["cle"]
    assert second["valeur"] != first["valeur"] and response.json()["message"].startswith("Nouvelle clé générée")
    assert call(second["valeur"]).status_code == 200 and call(first["valeur"]).status_code == 200
    assert len(response.json()["partenaire"]["cles"]) == 2 and second["valeur"] not in str(response.json()["partenaire"])

    # Révocation de la première : plus aucun accès avec elle, la seconde continue
    revoked = admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "revoquer", "cle_id": first["id"]})
    assert revoked.status_code == 200 and revoked.json()["message"] == "Clé révoquée : elle ne fonctionne plus."
    statuses = {k["id"]: k["statut"] for k in revoked.json()["partenaire"]["cles"]}
    assert statuses == {first["id"]: "REVOQUEE", second["id"]: "ACTIVE"}
    assert call(first["valeur"]).status_code == 401 and call(second["valeur"]).status_code == 200
    assert admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "revoquer", "cle_id": first["id"]}).status_code == 404

    # Expiration : une clé dont la date est passée est refusée
    third = admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "generer", "expire_le": "2999-12-31"}).json()["cle"]
    assert third["expire_le"] == "2999-12-31" and call(third["valeur"]).status_code == 200
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("UPDATE partenaire_cle SET expire_le = CURRENT_DATE - 1 WHERE id = %s", (third["id"],))
        conn.commit()
    assert call(third["valeur"]).status_code == 401
    listed = [p for p in admin.get("/api/admin_partenaires").json()["data"] if p["id"] == pid][0]
    assert {k["id"]: k["statut"] for k in listed["cles"]}[third["id"]] == "EXPIREE"
    assert listed["cles"][0]["derniere_utilisation"] or any(k["derniere_utilisation"] for k in listed["cles"])
    assert sum(k["nb_utilisations"] for k in listed["cles"]) >= 3

    bad = {"id": pid, "action": "generer", "expire_le": "2000-01-01"}
    assert admin.post("/api/admin_partenaires/cles", json=bad).status_code == 422
    assert admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "autre"}).status_code == 422
    assert admin.post("/api/admin_partenaires/cles", json={"id": 999999, "action": "generer"}).status_code == 404
    assert admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "revoquer"}).status_code == 422


def test_ip_allowlist(admin):
    created = create(admin, ips_autorisees=["203.0.113.0/24", "198.51.100.7"]).json()
    key, pid = created["cle"]["valeur"], created["partenaire"]["id"]
    assert created["partenaire"]["ips_autorisees"] == ["203.0.113.0/24", "198.51.100.7/32"]
    assert call(key, ip="203.0.113.77").status_code == 200  # dans le réseau
    assert call(key, ip="198.51.100.7").status_code == 200  # adresse exacte
    refused = call(key, ip="192.0.2.99")
    assert refused.status_code == 403 and refused.json()["message"] == "Adresse IP non autorisée pour cette clé."
    # La liste vide lève la restriction ; une chaîne séparée par des espaces est acceptée
    assert admin.post("/api/admin_partenaires/modifier", json={"id": pid, "ips_autorisees": "192.0.2.99, 2001:db8::/32"}).status_code == 200
    assert call(key, ip="192.0.2.99").status_code == 200 and call(key, ip="203.0.113.77").status_code == 403
    assert admin.post("/api/admin_partenaires/modifier", json={"id": pid, "ips_autorisees": []}).status_code == 200
    assert call(key, ip="192.0.2.99").status_code == 200


def test_rate_limit_per_minute(admin):
    key = create(admin, limite_par_minute=2).json()["cle"]["valeur"]
    assert call(key).status_code == 200 and call(key).status_code == 200
    third = call(key)
    assert third.status_code == 429 and "Quota dépassé (2 appels par minute)" in third.json()["message"]
    security.partner_rate.reset()
    assert call(key).status_code == 200


def test_invalid_keys_are_refused(admin):
    for key in ("", "upb_inconnue", "n'importe quoi"):
        response = call(key)
        assert response.status_code == 401 and response.json()["message"] == "Clé d'API partenaire absente ou invalide."


def test_delete_partner_cuts_access(admin):
    created = create(admin).json()
    key, pid = created["cle"]["valeur"], created["partenaire"]["id"]
    assert call(key).status_code == 200
    assert admin.post("/api/admin_partenaires/supprimer", json={"id": pid}).json()["message"] == "Intégration supprimée."
    assert call(key).status_code == 401
    assert all(p["id"] != pid for p in admin.get("/api/admin_partenaires").json()["data"])
    assert admin.post("/api/admin_partenaires/supprimer", json={"id": pid}).status_code == 404
    assert admin.post("/api/admin_partenaires/supprimer", json={}).status_code == 422


def test_management_and_usage_are_traced_without_secrets(admin, monkeypatch):
    monkeypatch.setenv("UPB_AUDIT_ENABLED", "1")
    created = create(admin, permission="INSCRIPTION").json()
    key, pid = created["cle"]["valeur"], created["partenaire"]["id"]
    ok = call(key, numero_candidat="OFFICIEL-7")
    assert ok.status_code == 200
    call("upb_pas_la_bonne")
    assert call(key, ip="203.0.113.9").status_code == 200
    admin.post("/api/admin_partenaires/modifier", json={"id": pid, "actif": False})
    assert call(key).status_code == 401
    admin.post("/api/admin_partenaires/modifier", json={"id": pid, "actif": True, "description": "x"})
    extra = admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "generer"}).json()["cle"]
    admin.post("/api/admin_partenaires/cles", json={"id": pid, "action": "revoquer", "cle_id": extra["id"]})

    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.row_factory = psycopg.rows.dict_row
        rows = conn.execute("SELECT * FROM journal_activite WHERE ip IN ('testclient', %s) ORDER BY id", (ANON_IP,)).fetchall()
    actions = [(r["action"], r["statut"]) for r in rows]
    for expected in (("PARTENAIRE_CREE", "SUCCES"), ("PARTENAIRE_DESACTIVE", "SUCCES"), ("PARTENAIRE_ACTIVE", "SUCCES"),
                     ("PARTENAIRE_CLE_GENEREE", "SUCCES"), ("PARTENAIRE_CLE_REVOQUEE", "SUCCES"),
                     ("INSCRIPTION_CONCOURS", "SUCCES"), ("INSCRIPTION_CONCOURS", "ECHEC")):
        assert expected in actions, (expected, actions)
    reasons = [r["details"].get("raison") for r in rows if r["statut"] == "ECHEC" and r["details"]]
    assert "cle_inconnue" in reasons and "partenaire_desactive" in reasons  # motif précis réservé au journal
    used = [r for r in rows if r["action"] == "INSCRIPTION_CONCOURS" and r["statut"] == "SUCCES"]
    assert used[0]["acteur"] == "ZZTsite-externe" and used[0]["acteur_type"] == "PARTENAIRE"
    created_row = [r for r in rows if r["action"] == "PARTENAIRE_CREE"][0]
    assert created_row["acteur"] == "ZZTadmin" and created_row["cible"] == "ZZTsite-externe"
    blob = str(rows)
    for secret in (key, extra["valeur"], "upb_pas_la_bonne", created["cle"]["valeur"]):
        assert secret not in blob, "une clé d'API figure dans le journal"

    # Activité visible dans la fiche : appels, échecs, inscriptions marquées
    stats = [p for p in admin.get("/api/admin_partenaires").json()["data"] if p["id"] == pid][0]["stats"]
    assert stats["appels_total"] >= 3 and stats["inscrits_marques"] == 1 and stats["derniere_activite"]
    assert stats["echecs_24h"] >= 1


def test_matricule_alone_is_enough_and_can_be_the_only_data_shared(admin):
    """Un site peut vérifier un étudiant avec le seul matricule, sans rien recevoir d'autre."""
    created = create(admin, champs=["matricule"])  # accès limité au seul matricule
    assert created.json()["partenaire"]["champs"] == ["matricule"]
    key = created.json()["cle"]["valeur"]
    response = call(key)  # corps : {"matricule": ...} uniquement
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verifie"] is True and body["correspondance"] is None and body["inscription"] is None
    assert body["data"] == {"matricule": MATRICULE_ALICE}  # aucune donnée personnelle transmise
    assert "Zztest" not in response.text and "1998" not in response.text
    # Matricule inconnu : 404 ; casse et espaces sans importance
    assert call(key, "INCONNU").status_code == 404
    assert call(key, f"  {MATRICULE_ALICE.lower()}  ").json()["data"] == {"matricule": MATRICULE_ALICE}


def test_optional_fields_are_compared_without_revealing_data(admin):
    key = create(admin, champs=["matricule"]).json()["cle"]["valeur"]
    ok = call(key, nom="  zztest ", prenoms="ALICE", date_naissance="17/03/1998").json()
    assert ok["correspondance"] == {"nom": True, "prenoms": True, "date_naissance": True}
    assert ok["data"] == {"matricule": MATRICULE_ALICE}  # toujours aucune donnée du dossier
    mixed = call(key, nom="Autre", date_naissance="1998-03-18").json()
    assert mixed["verifie"] is True and mixed["correspondance"] == {"nom": False, "date_naissance": False}
    # Seuls les champs envoyés sont comparés ; formats de date acceptés ; date invalide = pas de correspondance
    assert call(key, prenoms="Alice").json()["correspondance"] == {"prenoms": True}
    assert call(key, date_naissance="1998-03-17").json()["correspondance"] == {"date_naissance": True}
    assert call(key, date_naissance="31/02/1998").json()["correspondance"] == {"date_naissance": False}
    assert call(key, nom="1234").json()["correspondance"] == {"nom": False}
    # Champ vide = ignoré ; type invalide = 400
    assert call(key, nom="   ").json()["correspondance"] is None
    assert call(key, nom=123).status_code == 400
    # Le même contrôle ne révèle rien pour un matricule inconnu
    assert call(key, "INCONNU", nom="Zztest").status_code == 404
