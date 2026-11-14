"""Journal d'activité : chaque action est tracée, sans données sensibles (base Neon réelle).

Les événements des tests portent l'adresse « testclient » et sont supprimés avant et après.
"""
import psycopg
import pytest
from fastapi.testclient import TestClient
from test_db_flow import (  # noqa: F401  (client = fixture admin + nettoyage des étudiants de test)
    ADMIN_ID,
    ADMIN_PASSWORD,
    MATRICULE_ALICE,
    MATRICULE_BOB,
    client,
    import_csv,
    make_admin,
    make_partner,
)

from app import audit, config, security
from app.main import create_app


def purge():
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("DELETE FROM journal_activite WHERE ip = 'testclient'")
        conn.commit()


def events(**where):
    sql = "SELECT * FROM journal_activite WHERE ip = 'testclient'"
    params = []
    for key, value in where.items():
        sql += f" AND {key} = %s"
        params.append(value)
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.row_factory = psycopg.rows.dict_row
        return conn.execute(sql + " ORDER BY id", params).fetchall()


@pytest.fixture()
def audited(client, monkeypatch):
    monkeypatch.setenv("UPB_AUDIT_ENABLED", "1")
    security.login_throttle.reset("ip:testclient", f"id:{ADMIN_ID.lower()}")
    purge()
    yield client
    purge()
    security.login_throttle.reset("ip:testclient", f"id:{ADMIN_ID.lower()}")


def only(action, **where):
    found = events(action=action, **where)
    assert len(found) == 1, (action, [(e["action"], e["statut"]) for e in events()])
    return found[0]


def test_every_kind_of_action_is_traced(audited):
    client = audited
    make_admin()
    key = make_partner()

    # --- admin : import CSV ---
    assert import_csv(client).status_code == 201
    e = only("IMPORT_ETUDIANTS_CSV")
    assert e["statut"] == "SUCCES" and e["acteur_type"] == "ADMIN" and e["acteur"] == ADMIN_ID
    assert e["details"]["importes"] == 2 and e["cible"] == "etudiants.csv" and e["code_http"] == 201
    assert e["ip"] == "testclient" and e["chemin"] == "/api/import_etudiants" and e["methode"] == "POST"
    # import en échec : tracé avec le motif
    assert import_csv(client).status_code == 422
    failed = [x for x in events(action="IMPORT_ETUDIANTS_CSV") if x["statut"] == "ECHEC"]
    assert len(failed) == 1 and failed[0]["code_http"] == 422 and failed[0]["message"]
    assert failed[0]["details"]["erreurs"]

    # --- public : vérification (succès + échec) ---
    assert client.post("/api/verifier", json={"matricule": MATRICULE_ALICE}).status_code == 200
    assert client.post("/api/verifier", json={"matricule": "INCONNU"}).status_code == 404
    verifications = events(action="ETUDIANT_VERIFIE")
    assert [(v["statut"], v["cible"], v["acteur_type"]) for v in verifications] == [
        ("SUCCES", MATRICULE_ALICE, "PUBLIC"), ("ECHEC", "INCONNU", "PUBLIC")]
    assert verifications[1]["message"] == "Matricule non reconnus dans la base I-UPB."

    # --- public : demande de correction ; admin : approbation ---
    body = {"matricule": MATRICULE_ALICE, "telephone": "0707070707", "email": "alice.secrete@exemple.ci"}
    assert client.post("/api/demander_modification", json=body).status_code == 201
    e = only("DEMANDE_CORRECTION_SOUMISE")
    assert e["details"]["champs"] == ["email", "telephone"] and e["cible"] == MATRICULE_ALICE
    assert "0707070707" not in str(e["details"]) and "alice.secrete" not in str(e["details"])  # pas de valeurs
    request_id = client.get("/api/admin_modifications").json()["data"][0]["id"]
    assert client.post("/api/admin_modifications", json={"id": request_id, "action": "approve"}).status_code == 200
    e = only("DEMANDE_CORRECTION_APPROUVEE")
    assert e["cible"] == str(request_id) and e["acteur"] == ADMIN_ID and e["statut"] == "SUCCES"
    # approuver deux fois : échec tracé
    assert client.post("/api/admin_modifications", json={"id": request_id, "action": "approve"}).status_code == 404
    assert [x["statut"] for x in events(action="DEMANDE_CORRECTION_APPROUVEE")] == ["SUCCES", "ECHEC"]

    # --- public : demande de création ; admin : refus ---
    new = {"matricule": "ZZTJOURNAL01", "nom": "Zztest", "prenoms": "Eve", "date_naissance": "2001-02-03"}
    assert client.post("/api/demander_creation", json=new).status_code == 201
    e = only("DEMANDE_CREATION_SOUMISE")
    assert e["details"]["matricule_calcule"] == "ZZTE0302010001" and e["cible"] == "ZZTJOURNAL01"
    creation_id = client.get("/api/admin_creations").json()["data"][0]["id"]
    assert client.post("/api/admin_creations", json={"id": creation_id, "action": "reject"}).status_code == 200
    assert only("DEMANDE_CREATION_REFUSEE")["cible"] == str(creation_id)

    # --- admin : modification directe (noms de champs seulement) ---
    alice = client.get("/api/get_etudiants", params={"q": MATRICULE_ALICE}).json()["data"][0]["id"]
    assert client.post("/api/update_etudiant", json={"id": alice, "email": "autre@exemple.ci", "sexe": "F"}).status_code == 200
    e = only("ETUDIANT_MODIFIE")
    assert e["cible"] == str(alice) and e["details"]["champs"] == ["email", "sexe"]
    assert "autre@exemple.ci" not in str(e["details"])
    assert client.post("/api/update_telephone", json={"id": alice, "telephone": "0101010101"}).status_code == 200
    assert only("ETUDIANT_TELEPHONE_MODIFIE")["details"]["champs"] == ["telephone"]

    # --- admin : pointage ---
    assert client.post("/api/pointage", json={"matricule": MATRICULE_BOB, "date": "2026-06-10", "jour_libelle": "Jour 1"}).status_code == 201
    assert client.post("/api/pointage", json={"matricule": MATRICULE_BOB, "date": "2026-06-10"}).status_code == 409
    scans = events(action="POINTAGE_ENREGISTRE")
    assert [s["statut"] for s in scans] == ["SUCCES", "ECHEC"] and scans[0]["details"] == {"date": "2026-06-10", "jour": "Jour 1"}
    assert "Double pointage" in scans[1]["message"]

    # --- partenaire : inscription (succès, clé invalide) ---
    with TestClient(create_app()) as anonymous:
        assert anonymous.post("/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE}, headers={"X-API-Key": key}).status_code == 200
        assert anonymous.post("/api/partenaire/verifier", json={"matricule": MATRICULE_ALICE}, headers={"X-API-Key": "upb_faux"}).status_code == 401
        # --- admin : connexion (échec puis succès) et jamais le mot de passe ---
        assert anonymous.post("/api/admin_login", json={"identifiant": ADMIN_ID, "password": "FAUX-mot-de-passe"}).status_code == 401
        ok = anonymous.post("/api/admin_login", json={"identifiant": ADMIN_ID.upper(), "password": ADMIN_PASSWORD})
        assert ok.status_code == 200
        token = ok.json()["token"]
        assert anonymous.get("/api/get_etudiants", headers={"Authorization": "Bearer jeton-invalide"}).status_code == 401
        auth = {"Authorization": f"Bearer {token}"}
        assert anonymous.post("/api/admin_logout", headers=auth).status_code == 200
        assert anonymous.post("/api/admin_export", headers=auth, json={"format": "excel", "cible": "etudiants", "lignes": 8}).status_code == 200
        assert anonymous.post("/api/admin_export", headers=auth, json={"format": "zip", "cible": "etudiants"}).status_code == 400
        # --- documentation ---
        assert anonymous.get("/docs").status_code == 401
        assert anonymous.get("/docs", auth=(ADMIN_ID, ADMIN_PASSWORD)).status_code == 200
    e = [x for x in events(action="INSCRIPTION_CONCOURS")]
    assert [(x["statut"], x["acteur_type"]) for x in e] == [("SUCCES", "PARTENAIRE"), ("ECHEC", "PARTENAIRE")]
    assert e[0]["acteur"] == "ZZTsite-officiel" and e[0]["details"]["deja_inscrit"] is False and e[0]["details"]["numero_candidat"]
    assert e[1]["acteur"] is None and e[1]["message"] == "Clé d'API partenaire absente ou invalide."
    logins = events(action="ADMIN_CONNEXION")
    assert [(x["statut"], x["code_http"]) for x in logins] == [("ECHEC", 401), ("SUCCES", 200)]
    assert logins[0]["acteur"] == ADMIN_ID and logins[1]["acteur"] == ADMIN_ID  # identifiant canonique
    refused = only("ACCES_REFUSE")
    assert refused["statut"] == "ECHEC" and refused["details"]["chemin"] == "/api/get_etudiants"
    assert only("ADMIN_DECONNEXION")["acteur"] == ADMIN_ID
    exported = only("EXPORT_DONNEES", statut="SUCCES")
    assert exported["cible"] == "etudiants" and exported["details"] == {"format": "excel", "lignes": 8}
    assert only("EXPORT_DONNEES", statut="ECHEC")["code_http"] == 400
    docs = events(action="DOCUMENTATION_CONSULTEE")
    assert [(x["statut"], x["acteur_type"]) for x in docs] == [("ECHEC", "DOCUMENTATION"), ("SUCCES", "DOCUMENTATION")]
    assert docs[1]["acteur"] == ADMIN_ID

    # Jamais de secret dans le journal
    blob = str(events())
    for secret in (ADMIN_PASSWORD, "FAUX-mot-de-passe", token, key, "upb_faux", "jeton-invalide"):
        assert secret not in blob, secret

    # Les simples consultations (lectures admin réussies) ne sont pas journalisées
    before = len(events())
    assert client.get("/api/get_etudiants").status_code == 200
    assert client.get("/api/pointage", params={"date": "2026-06-10"}).status_code == 200
    assert len(events()) == before


def test_bulk_approval_traces_each_request(audited):
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        real = conn.execute(
            "SELECT (SELECT COUNT(*) FROM etudiant_modification m JOIN etudiant e ON e.id = m.etudiant_id "
            "WHERE m.statut = 'EN_ATTENTE' AND e.matricule NOT LIKE 'ZZT%') + "
            "(SELECT COUNT(*) FROM etudiant_creation_demande WHERE statut = 'EN_ATTENTE' "
            "AND matricule_saisi NOT LIKE 'ZZT%')"
        ).fetchone()[0]
    if real:
        pytest.skip("Des demandes réelles sont en attente.")
    client = audited
    assert import_csv(client).status_code == 201
    for matricule in (MATRICULE_ALICE, MATRICULE_BOB):
        assert client.post("/api/demander_modification", json={"matricule": matricule, "telephone": "0100000000"}).status_code == 201
    assert client.post("/api/admin_approve_all").status_code == 200
    each = events(action="DEMANDE_CORRECTION_APPROUVEE")
    assert len(each) == 2 and all(x["details"]["mode"] == "masse" and x["statut"] == "SUCCES" for x in each)
    summary = only("DEMANDES_APPROUVEES_EN_MASSE")
    assert summary["details"]["approuvees"] == 2 and summary["details"]["echecs"] == 0


def test_journal_api_filters_pagination_and_export(audited):
    client = audited
    for matricule in ("INCONNU1", "INCONNU2", "INCONNU3"):
        client.post("/api/verifier", json={"matricule": matricule})
    client.post("/api/verifier", json={"matricule": ""})  # 400 : tracé aussi

    data = client.get("/api/admin_journal", params={"action": "ETUDIANT_VERIFIE", "per_page": 2}).json()
    assert data["status"] == "success" and data["per_page"] == 2 and data["page"] == 1
    assert data["total"] >= 4 and data["total_pages"] == -(-data["total"] // 2) and len(data["data"]) == 2
    assert {"total", "dernieres_24h", "echecs_24h", "acces_refuses_24h"} <= set(data["stats"])
    assert "ADMIN_CONNEXION" in data["actions"] and "AUTH" in data["categories"]
    times = [row["created_at"] for row in data["data"]]
    assert times == sorted(times, reverse=True) and len(times[0]) == 19  # plus récent d'abord, AAAA-MM-JJ HH:MM:SS

    mine = client.get("/api/admin_journal", params={"q": "INCONNU2", "statut": "echec"}).json()
    assert [r["cible"] for r in mine["data"]] == ["INCONNU2"] and mine["data"][0]["statut"] == "ECHEC"
    assert client.get("/api/admin_journal", params={"q": "zz-aucun-resultat-zz"}).json()["total"] == 0
    today = client.get("/api/admin_journal", params={"date_debut": "2000-01-01", "date_fin": "2999-12-31", "acteur_type": "public", "categorie": "etudiant"}).json()
    assert today["total"] >= 4
    assert client.get("/api/admin_journal", params={"date_debut": "2999-01-01"}).json()["total"] == 0

    for bad in ({"date_debut": "31/12/2026"}, {"statut": "PEUT-ETRE"}, {"acteur_type": "ROBOT"}):
        assert client.get("/api/admin_journal", params=bad).status_code == 400, bad
    assert client.get("/api/admin_journal", params={"per_page": 500}).status_code == 400

    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/admin_journal").status_code == 401
        assert anonymous.get("/api/admin_journal/export").status_code == 401

    export = client.get("/api/admin_journal/export", params={"action": "ETUDIANT_VERIFIE"})
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"] and ".csv" in export.headers["content-disposition"]
    assert export.content.startswith(b"\xef\xbb\xbf")  # BOM UTF-8
    lines = export.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("Date;Type d'acteur;Acteur;Catégorie;Action;Statut") and len(lines) >= 5
    # L'export est lui-même tracé
    traced = events(action="JOURNAL_EXPORTE", statut="SUCCES")
    assert len(traced) == 1 and traced[0]["details"]["lignes"] == len(lines) - 1
    assert traced[0]["details"]["filtres"] == {"action": "ETUDIANT_VERIFIE"}
    # La tentative d'export sans authentification est tracée comme un échec
    refused = events(action="JOURNAL_EXPORTE", statut="ECHEC")
    assert len(refused) == 1 and refused[0]["code_http"] == 401


def test_csv_export_neutralises_formulas():
    from app.routes.journal import _safe
    assert _safe("=1+1") == "'=1+1" and _safe("@cmd") == "'@cmd" and _safe("normal") == "normal" and _safe(None) == ""


def test_logging_failure_never_breaks_the_action(audited, monkeypatch):
    def boom(_row):
        raise RuntimeError("base du journal indisponible")
    monkeypatch.setattr(audit, "_write", boom)
    response = audited.post("/api/verifier", json={"matricule": MATRICULE_ALICE})
    assert response.status_code in (200, 404)  # l'action aboutit malgré l'échec d'écriture du journal
    assert events() == []
