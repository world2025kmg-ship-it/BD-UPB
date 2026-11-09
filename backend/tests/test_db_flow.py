"""Parcours complet contre la vraie base Neon (DATABASE_URL).

Toutes les données créées portent le préfixe de test « ZZTEST » et sont supprimées
à la fin ; filières/niveaux « ZZTEST… » aussi.
"""
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import create_app

from app.security import create_session_token, generate_api_key, hash_api_key, hash_password

SECRET = "cle-secrete-de-test-0123456789abcdef0123"
ADMIN_ID = "ZZTadmin"
ADMIN_PASSWORD = "Mot-de-passe-test-1"
CSV = (
    "Nom;Prénom;Date de naissance;Filière;Niveau;Téléphone;Email;Sexe\n"
    "Zztest;Alice;17/03/1998;ZZTEST Génie;ZZTEST L3;0102030405;alice@exemple.ci;f\n"
    "Zztest;Bob;1999-05-02;ZZTEST Génie;ZZTEST L2;;;M\n"
)
MATRICULE_ALICE = "ZZTA1703980001"
MATRICULE_BOB = "ZZTB0205990001"


def cleanup():
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("DELETE FROM etudiant_creation_demande WHERE matricule_saisi LIKE 'ZZT%'")
        conn.execute("DELETE FROM administrateur WHERE identifiant LIKE 'ZZT%'")
        conn.execute("DELETE FROM partenaire WHERE nom LIKE 'ZZT%'")
        conn.execute("DELETE FROM etudiant WHERE matricule LIKE 'ZZT%'")
        conn.execute("DELETE FROM filiere WHERE libelle LIKE 'ZZTEST%'")
        conn.execute("DELETE FROM niveau WHERE libelle LIKE 'ZZTEST%'")
        conn.commit()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("UPB_SECRET_KEY", SECRET)
    cleanup()
    with TestClient(create_app()) as test_client:
        test_client.headers["Authorization"] = f"Bearer {create_session_token(ADMIN_ID)}"
        yield test_client
    cleanup()


def import_csv(client, content=CSV):
    return client.post(
        "/api/import_etudiants", files={"fichier": ("etudiants.csv", content.encode("utf-8"))}
    )


def student_id(client, matricule):
    data = client.get("/api/get_etudiants", params={"q": matricule}).json()
    return data["data"][0]["id"]


def test_full_flow(client):
    # --- Import CSV ---
    response = import_csv(client)
    assert response.status_code == 201, response.text
    assert response.json()["matricules"] == [MATRICULE_ALICE, MATRICULE_BOB]
    # Doublon : refusé
    response = import_csv(client)
    assert response.status_code == 422
    assert "dupliqué" in response.json()["errors"][0]

    # --- Vérification publique ---
    response = client.post("/api/verifier", json={"matricule": MATRICULE_ALICE.lower()})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["nom"] == "Zztest" and data["date_naissance"] == "1998-03-17"
    assert data["filiere"] == "ZZTEST Génie" and data["sexe"] == "F"
    unknown = client.post("/api/verifier", json={"matricule": "INCONNU"})
    assert unknown.status_code == 404
    assert unknown.json()["message"] == "Matricule non reconnus dans la base I-UPB."

    # --- Liste admin ---
    listing = client.get("/api/get_etudiants", params={"q": "ZZTEST G"}).json()
    assert listing["total"] == 2 and listing["page"] == 1
    assert {"total", "inscrits", "telephones", "emails", "filieres", "niveaux"} <= set(listing["stats"])
    assert client.get("/api/get_etudiants", headers={"Authorization": "Bearer x"}).status_code == 401

    # --- Marquage « inscrit » par le site officiel (clé d'API partenaire) ---
    key = make_partner()
    headers = {"X-API-Key": key}
    anonymous = TestClient(create_app())
    with anonymous:
        before = client.get("/api/get_etudiants", params={"q": MATRICULE_ALICE}).json()["data"][0]
        assert before["numero_candidat"] is None
        response = anonymous.post(
            "/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE.lower()}, headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["nom"] == "Zztest" and body["data"]["date_naissance"] == "1998-03-17"
        assert "id" not in body["data"]
        numero = body["inscription"]["numero_candidat"]
        assert numero.startswith("GENIE-2026-") and len(numero) == len("GENIE-2026-") + 5
        assert body["inscription"]["deja_inscrit"] is False
        # Idempotent : même numéro, plus de nouvelle ligne
        response = anonymous.post("/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE}, headers=headers)
        assert response.json()["inscription"] == {"numero_candidat": numero, "statut": "EN_ATTENTE", "deja_inscrit": True}
        # Le numéro fourni ne remplace pas un numéro existant
        response = anonymous.post(
            "/api/partenaire/verifier",
            json={"matricule": MATRICULE_ALICE, "numero_candidat": "AUTRE-1"}, headers=headers,
        )
        assert response.json()["inscription"]["numero_candidat"] == numero
        # Numéro fourni par le site officiel : enregistré à la première inscription
        response = anonymous.post(
            "/api/partenaire/verifier",
            json={"matricule": MATRICULE_BOB, "numero_candidat": "OFFICIEL-0042"}, headers=headers,
        )
        assert response.json()["inscription"] == {"numero_candidat": "OFFICIEL-0042", "statut": "EN_ATTENTE", "deja_inscrit": False}
        # Erreurs
        unknown = anonymous.post("/api/partenaire/verifier", json={"matricule": "INCONNU"}, headers=headers)
        assert unknown.status_code == 404
        assert unknown.json()["message"] == "Matricule non reconnus dans la base I-UPB."
        assert anonymous.post("/api/partenaire/verifier", json={}, headers=headers).status_code == 400
        assert anonymous.post("/api/partenaire/verifier", json={"matricule": "X" * 41}, headers=headers).status_code == 400
        assert anonymous.post(
            "/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE, "numero_candidat": "N" * 41}, headers=headers
        ).status_code == 400
        # Une clé désactivée est refusée
        with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
            conn.execute("UPDATE partenaire SET actif = FALSE WHERE cle_hash = %s", (hash_api_key(key),))
            conn.commit()
        assert anonymous.post(
            "/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE}, headers=headers
        ).status_code == 401
    # L'étudiant apparaît inscrit côté admin, avec les statistiques
    after = client.get("/api/get_etudiants", params={"q": MATRICULE_ALICE}).json()
    assert after["data"][0]["numero_candidat"] == numero
    assert after["stats"]["inscrits"] >= 2
    # Plus de route publique d'inscription
    assert client.post("/api/inscrire", json={"matricule": MATRICULE_ALICE}).status_code in (404, 405)

    # --- Demande de correction + validation admin ---
    body = {"matricule": MATRICULE_ALICE, "telephone": "0707070707", "filiere": "ZZTEST Mines"}
    assert client.post("/api/demander_modification", json=body).status_code == 201
    assert client.post("/api/demander_modification", json=body).status_code == 409
    pending = client.get("/api/admin_modifications").json()["data"]
    mine = [m for m in pending if m["matricule"] == MATRICULE_ALICE]
    assert len(mine) == 1
    assert mine[0]["modifications"] == {"telephone": "0707070707", "filiere": "ZZTEST Mines"}
    assert isinstance(mine[0]["created_at"], str) and mine[0]["date_naissance"] == "1998-03-17"
    response = client.post("/api/admin_modifications", json={"id": mine[0]["id"], "action": "approve"})
    assert response.status_code == 200, response.text
    data = client.post("/api/verifier", json={"matricule": MATRICULE_ALICE}).json()["data"]
    assert data["telephone"] == "0707070707" and data["filiere"] == "ZZTEST Mines"
    assert client.post(
        "/api/admin_modifications", json={"id": mine[0]["id"], "action": "approve"}
    ).status_code == 404

    # Refus d'une demande
    body = {"matricule": MATRICULE_ALICE, "email": "autre@exemple.ci"}
    assert client.post("/api/demander_modification", json=body).status_code == 201
    pending = client.get("/api/admin_modifications").json()["data"]
    request_id = [m for m in pending if m["matricule"] == MATRICULE_ALICE][0]["id"]
    response = client.post("/api/admin_modifications", json={"id": request_id, "action": "reject"})
    assert response.json()["message"] == "Demande refusée."
    assert client.post("/api/demander_modification", json=body).status_code == 201  # plus en attente
    request_id = [m for m in client.get("/api/admin_modifications").json()["data"]
                  if m["matricule"] == MATRICULE_ALICE][0]["id"]
    client.post("/api/admin_modifications", json={"id": request_id, "action": "reject"})

    # Approbation changeant les prénoms : le matricule est recalculé
    body = {"matricule": MATRICULE_ALICE, "prenoms": "Béatrice"}
    assert client.post("/api/demander_modification", json=body).status_code == 201
    request_id = [m for m in client.get("/api/admin_modifications").json()["data"]
                  if m["matricule"] == MATRICULE_ALICE][0]["id"]
    response = client.post("/api/admin_modifications", json={"id": request_id, "action": "approve"})
    assert response.status_code == 200, response.text
    new_matricule = response.json()["matricule"]
    assert new_matricule == "ZZTB1703980001"

    # --- Mise à jour admin ---
    beatrice = student_id(client, new_matricule)
    response = client.post("/api/update_etudiant", json={"id": beatrice, "email": "a@b.ci", "sexe": "M"})
    assert response.status_code == 200
    response = client.post("/api/update_etudiant", json={"id": beatrice, "date_naissance": "1998-03-18"})
    assert response.status_code == 200
    assert response.json()["data"]["matricule"] == "ZZTB1803980001"
    # Même nom/initiale/date que Bob (ZZTB0205990001) : conflit de matricule
    response = client.post("/api/update_etudiant", json={"id": beatrice, "date_naissance": "02/05/1999"})
    assert response.status_code == 409
    response = client.post("/api/update_telephone", json={"id": beatrice, "telephone": "0505050505"})
    assert response.status_code == 200
    data = client.post("/api/verifier", json={"matricule": "ZZTB1803980001"}).json()["data"]
    assert data["email"] == "a@b.ci" and data["sexe"] == "M" and data["telephone"] == "0505050505"

    # --- Pointage ---
    bob = MATRICULE_BOB
    response = client.post(
        "/api/pointage", json={"matricule": bob, "date": "2026-06-10", "jour_libelle": "Jour 1"}
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["data"]["heure_pointage"]) == 19
    response = client.post("/api/pointage", json={"matricule": bob, "date": "2026-06-10"})
    assert response.status_code == 409
    assert client.post("/api/pointage", json={"matricule": "NOPE", "date": "2026-06-10"}).status_code == 404
    report = client.get("/api/pointage", params={"date": "2026-06-10", "jour": "Jour 1"}).json()
    assert report["jour"] == "Jour 1" and report["date"] == "2026-06-10"
    assert [r["matricule"] for r in report["data"]] == [bob]
    assert report["stats"]["total_presents"] == 1
    assert report["stats"]["taux_presence"].endswith(" %")


def test_failed_import_rolls_back_valid_rows(client):
    content = CSV + "Zztest;Chloé;31/02/1998;ZZTEST Génie;ZZTEST L1;;;\n"
    response = import_csv(client, content)
    assert response.status_code == 422
    assert response.json()["message"].startswith("Aucune ligne importée")
    assert client.get("/api/get_etudiants", params={"q": "zztest"}).json()["total"] == 0


def test_unknown_matricule_creation_flow(client):
    saisi = "ZZTNOUVEAU0001"
    # Matricule inconnu
    assert client.post("/api/verifier", json={"matricule": saisi}).status_code == 404
    # Le candidat renseigne ses informations -> demande en attente
    body = {
        "matricule": saisi, "nom": "Zztest", "prenoms": "Carole", "date_naissance": "05/06/2000",
        "filiere": "ZZTEST Génie", "niveau": "ZZTEST L1", "email": "carole@exemple.ci",
        "telephone": "0101010101", "sexe": "f", "allergies_affection": "Arachides",
    }
    response = client.post("/api/demander_creation", json=body)
    assert response.status_code == 201, response.text
    # Rien n'est créé tant que l'admin n'a pas validé
    assert client.post("/api/verifier", json={"matricule": "ZZTC0506000001"}).status_code == 404
    # Doublon de demande refusé
    assert client.post("/api/demander_creation", json=body).status_code == 409
    # L'admin voit la demande
    pending = [r for r in client.get("/api/admin_creations").json()["data"]
               if r["matricule_saisi"] == saisi]
    assert len(pending) == 1
    assert pending[0]["donnees"]["prenoms"] == "Carole"
    assert pending[0]["donnees"]["date_naissance"] == "2000-06-05"
    # Sans jeton : refusé
    assert client.get("/api/admin_creations", headers={"Authorization": "Bearer x"}).status_code == 401
    # Approbation -> dossier créé avec le matricule calculé
    response = client.post("/api/admin_creations", json={"id": pending[0]["id"], "action": "approve"})
    assert response.status_code == 200, response.text
    assert response.json()["matricule"] == "ZZTC0506000001"
    assert client.post(
        "/api/admin_creations", json={"id": pending[0]["id"], "action": "approve"}
    ).status_code == 404
    data = client.post("/api/verifier", json={"matricule": "ZZTC0506000001"}).json()["data"]
    assert data["nom"] == "Zztest" and data["sexe"] == "F" and data["filiere"] == "ZZTEST Génie"
    assert data["allergies_affection"] == "Arachides" and data["date_naissance"] == "2000-06-05"
    # Le dossier existe maintenant : nouvelle demande refusée
    assert client.post("/api/demander_creation", json=body).status_code == 409


def test_creation_request_can_be_rejected(client):
    body = {"matricule": "ZZTREFUS0001", "nom": "Zztest", "prenoms": "Denis", "date_naissance": "1997-01-02"}
    assert client.post("/api/demander_creation", json=body).status_code == 201
    pending = [r for r in client.get("/api/admin_creations").json()["data"]
               if r["matricule_saisi"] == "ZZTREFUS0001"][0]
    response = client.post("/api/admin_creations", json={"id": pending["id"], "action": "reject"})
    assert response.json()["message"] == "Demande refusée."
    assert client.post("/api/verifier", json={"matricule": "ZZTD0201970001"}).status_code == 404
    # Refusée : une nouvelle demande est possible
    assert client.post("/api/demander_creation", json=body).status_code == 201


def make_partner():
    key = generate_api_key()
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("INSERT INTO partenaire (nom, cle_hash) VALUES ('ZZTsite-officiel', %s)", (hash_api_key(key),))
        conn.commit()
    return key


def make_admin(active=True):
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute(
            "INSERT INTO administrateur (identifiant, mot_de_passe_hash, actif) VALUES (%s, %s, %s)",
            (ADMIN_ID, hash_password(ADMIN_PASSWORD), active),
        )
        conn.commit()


def test_admin_login_flow(client):
    from app import security
    make_admin()
    anonymous = TestClient(create_app())
    with anonymous:
        # Mauvais mot de passe / identifiant inconnu : même message
        for body in ({"identifiant": ADMIN_ID, "password": "faux"},
                     {"identifiant": "ZZTinconnu", "password": ADMIN_PASSWORD}):
            response = anonymous.post("/api/admin_login", json=body)
            assert response.status_code == 401
            assert response.json()["message"] == "Identifiant ou mot de passe incorrect."
        security.login_throttle.reset("ip:testclient", f"id:{ADMIN_ID.lower()}", "id:zztinconnu")

        # Connexion réussie (identifiant insensible à la casse)
        response = anonymous.post(
            "/api/admin_login", json={"identifiant": ADMIN_ID.upper(), "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["identifiant"] == ADMIN_ID and data["expires_in"] == 8 * 3600
        assert "password" not in response.text.lower().replace("mot de passe", "")
        headers = {"Authorization": f"Bearer {data['token']}"}
        assert anonymous.post("/api/admin_auth_check", headers=headers).status_code == 200
        assert anonymous.get("/api/get_etudiants", headers=headers).status_code == 200
        assert anonymous.post("/api/admin_auth_check").status_code == 401

        # La documentation accepte l'identifiant + mot de passe administrateur
        assert anonymous.get("/docs", auth=(ADMIN_ID, ADMIN_PASSWORD)).status_code == 200
        assert anonymous.get("/docs", auth=(ADMIN_ID, "faux")).status_code == 401

    # derniere_connexion renseignée
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        row = conn.execute(
            "SELECT derniere_connexion FROM administrateur WHERE identifiant = %s", (ADMIN_ID,)
        ).fetchone()
    assert row[0] is not None


def test_login_blocks_after_repeated_failures(client):
    from app import security
    make_admin()
    keys = ("ip:testclient", f"id:{ADMIN_ID.lower()}")
    security.login_throttle.reset(*keys)
    anonymous = TestClient(create_app())
    with anonymous:
        for _ in range(5):
            assert anonymous.post(
                "/api/admin_login", json={"identifiant": ADMIN_ID, "password": "faux"}
            ).status_code == 401
        # Même le bon mot de passe est refusé pendant le blocage
        response = anonymous.post(
            "/api/admin_login", json={"identifiant": ADMIN_ID, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 429
    security.login_throttle.reset(*keys)


def test_disabled_admin_cannot_login(client):
    from app import security
    make_admin(active=False)
    security.login_throttle.reset("ip:testclient", f"id:{ADMIN_ID.lower()}")
    with TestClient(create_app()) as anonymous:
        response = anonymous.post(
            "/api/admin_login", json={"identifiant": ADMIN_ID, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 401
    security.login_throttle.reset("ip:testclient", f"id:{ADMIN_ID.lower()}")


def test_approve_all_pending_requests(client):
    # /api/admin_approve_all agit sur TOUTES les demandes en attente de la base : on ne
    # lance ce test que s'il n'y a aucune demande réelle (hors données de test ZZT).
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        real = conn.execute(
            "SELECT (SELECT COUNT(*) FROM etudiant_modification m JOIN etudiant e ON e.id = m.etudiant_id "
            "WHERE m.statut = 'EN_ATTENTE' AND e.matricule NOT LIKE 'ZZT%') + "
            "(SELECT COUNT(*) FROM etudiant_creation_demande WHERE statut = 'EN_ATTENTE' "
            "AND matricule_saisi NOT LIKE 'ZZT%')"
        ).fetchone()[0]
    if real:
        pytest.skip("Des demandes réelles sont en attente : test non exécuté pour ne pas les approuver.")

    assert import_csv(client).status_code == 201
    for matricule, phone in ((MATRICULE_ALICE, "0111111111"), (MATRICULE_BOB, "0222222222")):
        assert client.post("/api/demander_modification", json={"matricule": matricule, "telephone": phone}).status_code == 201
    carole = {"nom": "Zztest", "prenoms": "Carole", "date_naissance": "2000-06-05", "filiere": "ZZTEST Génie"}
    assert client.post("/api/demander_creation", json={"matricule": "ZZTBULK0001", **carole}).status_code == 201
    assert client.post("/api/demander_creation", json={"matricule": "ZZTBULK0002", **carole}).status_code == 201  # même dossier calculé

    anonymous = TestClient(create_app())
    with anonymous:
        assert anonymous.post("/api/admin_approve_all").status_code == 401
    response = client.post("/api/admin_approve_all")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["modifications"] == {"approuvees": 2, "echecs": []}
    assert body["creations"]["approuvees"] == 1
    assert len(body["creations"]["echecs"]) == 1
    assert "matricule" in body["creations"]["echecs"][0]["message"].lower()
    assert "1 demande(s) n'ont pas pu" in body["message"]

    # Les changements sont appliqués ; la demande en échec reste en attente
    assert client.post("/api/verifier", json={"matricule": MATRICULE_ALICE}).json()["data"]["telephone"] == "0111111111"
    assert client.post("/api/verifier", json={"matricule": MATRICULE_BOB}).json()["data"]["telephone"] == "0222222222"
    assert client.post("/api/verifier", json={"matricule": "ZZTC0506000001"}).status_code == 200
    pending = [r["matricule_saisi"] for r in client.get("/api/admin_creations").json()["data"]]
    assert len(pending) == 1 and pending[0].startswith("ZZTBULK")
    assert client.get("/api/admin_modifications").json()["data"] == []
    # Plus rien à approuver (sauf la demande en échec, toujours refusée)
    again = client.post("/api/admin_approve_all").json()
    assert again["modifications"]["approuvees"] == 0 and again["creations"]["approuvees"] == 0
