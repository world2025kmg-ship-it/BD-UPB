from fastapi.testclient import TestClient

from generate_admin_token import generate_token
import main


client = TestClient(main.app)


def test_student_matricule_normalizes_accents_and_birth_date() -> None:
    assert main.student_matricule("Kouassi", "Élodie", "1998-03-17") == "KOUE1703980001"


def test_student_matricule_rejects_future_birth_date() -> None:
    try:
        main.student_matricule("Kouassi", "Élodie", "2098-03-17")
    except main.HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("A future birth date must be rejected.")


def test_portal_and_assets_are_served_without_exposing_server_config() -> None:
    portal = client.get("/")
    assert portal.status_code == 200
    assert "Confirmer mon inscription au" not in portal.text
    assert "Espace administration" in portal.text
    assert client.get("/genie.css").status_code == 200
    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/png"
    assert client.get("/.env.example").status_code == 404


def test_health_check() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openai_models_endpoint_returns_valid_empty_catalog() -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


def test_admin_auth_reports_missing_server_configuration(monkeypatch) -> None:
    monkeypatch.delenv("UPB_ADMIN_TOKEN", raising=False)
    response = client.post("/api/admin/auth/login")
    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_admin_token_generator_returns_a_long_url_safe_secret() -> None:
    token = generate_token()
    assert len(token) >= main.ADMIN_TOKEN_MIN_LENGTH
    assert all(character.isalnum() or character in "-_" for character in token)


def test_admin_login_exchanges_master_secret_for_temporary_session(monkeypatch) -> None:
    secret = "test-secret-" + ("x" * 40)
    monkeypatch.setenv("UPB_ADMIN_TOKEN", secret)

    login = client.post(
        "/api/admin/auth/login",
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-store"
    session = login.json()["access_token"]
    assert session != secret
    assert login.json()["expires_in"] == main.ADMIN_SESSION_TTL_SECONDS
    assert client.post(
        "/api/admin/auth/verify",
        headers={"Authorization": f"Bearer {session}"},
    ).status_code == 200
    assert client.post(
        "/api/admin/auth/verify",
        headers={"Authorization": f"Bearer {secret}"},
    ).status_code == 401
    assert client.post(
        "/api/admin/auth/login",
        headers={"Authorization": "Bearer " + ("z" * len(secret))},
    ).status_code == 401


def test_admin_session_expires(monkeypatch) -> None:
    from types import SimpleNamespace

    secret = "test-secret-" + ("y" * 40)
    monkeypatch.setenv("UPB_ADMIN_TOKEN", secret)
    session = main.create_admin_session(secret)
    now = main.time.time()
    monkeypatch.setattr(
        main,
        "time",
        SimpleNamespace(time=lambda: now + main.ADMIN_SESSION_TTL_SECONDS + 1),
    )

    response = client.post(
        "/api/admin/auth/verify",
        headers={"Authorization": f"Bearer {session}"},
    )

    assert response.status_code == 401


def test_public_registration_validates_required_fields_before_database(monkeypatch) -> None:
    for key in ("UPB_DB_HOST", "UPB_DB_NAME", "UPB_DB_USER", "UPB_DB_PASS"):
        monkeypatch.delenv(key, raising=False)
    response = client.post("/api/student-registration-requests", json={"nom": ""})
    assert response.status_code == 422
    assert "données" in response.json()["message"].lower() or "nom" in response.json()["message"].lower()


def test_openapi_contains_legacy_routes_and_new_registration_workflow() -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/admin/auth/login" in schema["paths"]
    assert "/api/students/verify" in schema["paths"]
    assert "/api/admin/student-requests" in schema["paths"]
    assert "/api/student-registration-requests" in schema["paths"]
    assert "/api/contest-registrations" not in schema["paths"]
    assert "/v1/models" in schema["paths"]
    assert client.post("/api/contest-registrations", json={"matricule": "TEST"}).status_code == 404
