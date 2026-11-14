"""Tests sans base de données : service du frontend, authentification, validations."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

import os

from app.security import create_session_token

SECRET = "cle-secrete-de-test-0123456789abcdef0123"
DOCS_PASSWORD = "mot-de-passe-docs-1"
os.environ["UPB_SECRET_KEY"] = SECRET
TOKEN = create_session_token("testeur")  # jeton de session valide


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("UPB_SECRET_KEY", SECRET)
    monkeypatch.setenv("UPB_DOCS_PASSWORD", DOCS_PASSWORD)
    with TestClient(create_app()) as test_client:
        yield test_client


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Concours" in response.text


def test_frontend_pages_and_assets_are_served(client):
    for path in ("/admin.html", "/scanner.html", "/genie.css", "/genie-fx.js", "/logo-upb.png"):
        assert client.get(path).status_code == 200, path


def test_no_php_reference_in_frontend(client):
    for path in ("/", "/admin.html", "/scanner.html"):
        assert ".php" not in client.get(path).text.lower(), path


def test_admin_route_rejects_unconfigured_secret(client, monkeypatch):
    monkeypatch.delenv("UPB_SECRET_KEY")
    response = client.post("/api/admin_auth_check")
    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_admin_route_rejects_missing_or_wrong_token(client):
    assert client.post("/api/admin_auth_check").status_code == 401
    assert client.post("/api/admin_auth_check", headers=auth("mauvais")).status_code == 401


def test_admin_route_accepts_configured_token(client):
    response = client.post("/api/admin_auth_check", headers=auth())
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Accès administrateur autorisé."}


def test_preflight_does_not_require_token(client):
    response = client.options(
        "/api/pointage",
        headers={"Origin": "http://exemple.ci", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_wrong_method_or_unknown_route_returns_json_error(client):
    for response in (client.get("/api/verifier"), client.get("/api/inexistante")):
        assert response.status_code in (404, 405)
        body = response.json()
        assert body["status"] == "error" and body["message"]


def test_api_responses_are_not_cached(client):
    assert client.post("/api/admin_auth_check", headers=auth()).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/verifier"])
def test_matricule_required(client, path):
    for body in ({}, {"matricule": ""}, {"matricule": "X" * 41}, {"matricule": 5}):
        response = client.post(path, json=body)
        assert response.status_code == 400
        assert response.json()["message"] == "Matricule requis."
    assert client.post(path, content="pas du json").status_code == 400


def test_request_modification_validation(client):
    base = {"matricule": "KOUA1703980001"}
    assert client.post("/api/demander_modification", json={}).status_code == 400
    assert client.post("/api/demander_modification", json=base).json()["message"] == (
        "Aucune modification proposée."
    )
    for field, value, message in [
        ("email", "pas-un-email", "Adresse e-mail invalide."),
        ("sexe", "X", "Le sexe doit être M ou F."),
        ("date_naissance", "2999-01-01", "Date de naissance invalide."),
        ("nom", "", "Le nom et les prénoms sont obligatoires (150 caractères maximum)."),
        ("telephone", "1" * 41, "Le champ telephone est trop long."),
    ]:
        response = client.post("/api/demander_modification", json={**base, field: value})
        assert response.status_code == 422, field
        assert response.json()["message"] == message
    response = client.post("/api/demander_modification", json={**base, "nom": 5})
    assert response.json()["message"] == "Valeur invalide pour nom."


def test_update_etudiant_validation(client):
    assert client.post("/api/update_etudiant", json={"id": 1}).status_code == 401
    assert client.post("/api/update_etudiant", headers=auth(), content="x").status_code == 400
    assert client.post("/api/update_etudiant", headers=auth(), json={"id": 1}).status_code == 400
    response = client.post("/api/update_etudiant", headers=auth(), json={"id": 1, "sexe": "X"})
    assert response.status_code == 422


def test_pointage_validation(client):
    assert client.post("/api/pointage", json={}).status_code == 401
    assert client.post("/api/pointage", headers=auth(), json={}).status_code == 400
    response = client.post(
        "/api/pointage", headers=auth(), json={"matricule": "A", "date": "10/06/2026"}
    )
    assert response.status_code == 400
    assert client.get("/api/pointage?date=abc", headers=auth()).status_code == 400


def test_import_requires_csv_file(client):
    assert client.post("/api/import_etudiants", headers=auth()).status_code == 400
    response = client.post(
        "/api/import_etudiants", headers=auth(), files={"fichier": ("x.txt", b"nom")}
    )
    assert response.status_code == 422
    response = client.post(
        "/api/import_etudiants", headers=auth(), files={"fichier": ("x.csv", b"nom;prenoms\nA;B")}
    )
    assert response.status_code == 422
    assert response.json()["message"].startswith("Colonnes obligatoires")


def test_openapi_documents_every_route(client):
    spec = client.get("/openapi.json", auth=("integrateur", DOCS_PASSWORD)).json()
    assert spec["openapi"].startswith("3.")
    expected = {
        ("post", "/api/verifier"), ("post", "/api/partenaire/verifier"), ("post", "/api/demander_modification"),
        ("post", "/api/admin_auth_check"), ("get", "/api/get_etudiants"),
        ("post", "/api/update_etudiant"), ("post", "/api/update_telephone"),
        ("get", "/api/admin_modifications"), ("post", "/api/admin_modifications"),
        ("post", "/api/import_etudiants"), ("get", "/api/pointage"), ("post", "/api/pointage"),
        ("post", "/api/demander_creation"), ("get", "/api/admin_creations"),
        ("post", "/api/admin_creations"),
    }
    documented = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert expected <= documented
    for method, path in expected:
        op = spec["paths"][path][method]
        assert op["summary"] and op["tags"], (method, path)
        if method == "post" and path != "/api/admin_auth_check" and "import" not in path:
            assert "requestBody" in op, (method, path)
    assert spec["components"]["securitySchemes"]["AdminBearer"]["scheme"] == "bearer"
    assert spec["paths"]["/api/get_etudiants"]["get"]["security"]
    assert "security" not in spec["paths"]["/api/verifier"]["post"]
    assert client.get("/docs", auth=("x", DOCS_PASSWORD)).status_code == 200
    assert client.get("/redoc", auth=("x", DOCS_PASSWORD)).status_code == 200


def test_documentation_requires_authorization(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert "Basic" in response.headers["www-authenticate"]
        assert client.get(path, auth=("x", "mauvais")).status_code == 401
        assert client.get(path, auth=("x", DOCS_PASSWORD)).status_code == 200
    # Le mot de passe de la doc ne donne PAS accès à l'API d'administration
    assert client.post(
        "/api/admin_auth_check", headers={"Authorization": f"Bearer {DOCS_PASSWORD}"}
    ).status_code == 401


def test_session_token_rules(client, monkeypatch):
    from app import security
    assert security.read_session_token(TOKEN) == "testeur"
    assert security.read_session_token(TOKEN + "x") is None
    assert security.read_session_token("v1.abc.def") is None
    assert security.read_session_token("n'importe quoi") is None
    expired = security.create_session_token("testeur", ttl=-1)
    assert security.read_session_token(expired) is None
    assert client.post("/api/admin_auth_check", headers=auth(expired)).status_code == 401
    # Un jeton signé avec une autre clé est refusé
    monkeypatch.setenv("UPB_SECRET_KEY", "une-autre-cle-secrete-0123456789abcdef")
    assert client.post("/api/admin_auth_check", headers=auth()).status_code == 401


def test_password_hashing():
    from app import security
    stored = security.hash_password("MotDePasse-Solide-1")
    assert stored.startswith("scrypt$") and "MotDePasse" not in stored
    assert security.verify_password("MotDePasse-Solide-1", stored)
    assert not security.verify_password("autre", stored)
    assert not security.verify_password("x", "format-invalide")
    assert security.hash_password("MotDePasse-Solide-1") != stored  # sel aléatoire


def test_login_throttle():
    from app.security import LoginThrottle
    t = LoginThrottle(max_failures=3, window=60)
    assert t.retry_after("k") == 0
    for _ in range(3):
        t.fail("k")
    assert t.retry_after("k") > 0
    t.reset("k")
    assert t.retry_after("k") == 0


def test_login_input_validation(client):
    for body in ({}, {"identifiant": "a"}, {"password": "x"}, {"identifiant": "", "password": "x"},
                 {"identifiant": 5, "password": "x"}):
        response = client.post("/api/admin_login", json=body)
        assert response.status_code == 400, body
        assert response.json()["message"] == "Identifiant et mot de passe requis."
    assert client.post("/api/admin_login", content="x").status_code == 400


def test_login_unconfigured(client, monkeypatch):
    monkeypatch.delenv("UPB_SECRET_KEY")
    assert client.post("/api/admin_login", json={"identifiant": "a", "password": "b"}).status_code == 503


def test_unknown_matricule_message_is_i_upb():
    # Le message est défini dans le code ; test de non-régression du libellé demandé.
    import pathlib
    source = pathlib.Path("app/routes/students.py").read_text(encoding="utf-8")
    assert "Matricule non reconnus dans la base I-UPB." in source
    assert "date de naissance non reconnus" not in source


def test_creation_request_validation(client):
    url = "/api/demander_creation"
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, json={"matricule": "ZZTX0101000001"}).status_code == 422
    body = {"matricule": "ZZTX0101000001", "nom": "Zztest", "prenoms": "X"}
    assert client.post(url, json=body).json()["message"] == (
        "Le nom, les prénoms et la date de naissance sont obligatoires."
    )
    assert client.post(url, json={**body, "date_naissance": "2999-01-01"}).status_code == 422
    assert client.post(url, json={**body, "date_naissance": "1999-01-01", "email": "x"}).status_code == 422


def test_public_enrollment_route_is_removed(client):
    # L'inscription ne se fait plus depuis le portail : plus aucune route publique pour la créer.
    assert client.post("/api/inscrire", json={"matricule": "KOUA1703980001"}).status_code in (404, 405)
    assert "inscrire" not in client.get("/openapi.json", auth=("x", DOCS_PASSWORD)).text


def test_portal_has_no_enrollment_button(client):
    page = client.get("/").text
    assert "inscrireCandidat" not in page and "btn-insc" not in page and "/api/inscrire" not in page


def test_partner_route_requires_api_key(client):
    for headers in ({}, {"X-API-Key": ""}, {"X-API-Key": "upb_inconnue"}, {"Authorization": f"Bearer {TOKEN}"}):
        response = client.post("/api/partenaire/verifier", json={"matricule": "A"}, headers=headers)
        assert response.status_code == 401, headers
        assert response.json()["message"] == "Clé d'API partenaire absente ou invalide."


def test_api_key_hashing():
    from app import security
    key = security.generate_api_key()
    assert key.startswith("upb_") and len(key) > 40
    assert security.generate_api_key() != key
    assert len(security.hash_api_key(key)) == 64 and key not in security.hash_api_key(key)


def test_admin_page_has_bulk_approve_button(client):
    page = client.get("/admin.html").text
    assert 'id="btn-approve-all"' in page and "/api/admin_approve_all" in page
    assert "/api/admin_approve_all" in client.get("/openapi.json", auth=("x", DOCS_PASSWORD)).text


def test_admin_page_has_journal_tab(client):
    page = client.get("/admin.html").text
    assert 'href="#journal"' in page and 'id="journal"' in page and "/api/admin_journal" in page
    assert "/api/admin_logout" in page and "journaliserExport" in page
    spec = client.get("/openapi.json", auth=("x", DOCS_PASSWORD)).text
    for route in ("/api/admin_journal", "/api/admin_journal/export", "/api/admin_logout", "/api/admin_export"):
        assert route in spec, route


def test_audit_is_disabled_in_basic_tests(client):
    from app import audit
    assert audit.enabled() is False
