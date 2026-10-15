import os

import httpx
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_gmail_ops.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-without-credential-material")

from app.db import Base, SessionLocal, engine
from app.godaddy_service import GoDaddyClient, GoDaddyError, create_client, list_accounts, save_account
from app.models import GoDaddyAccount, Organization


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add(Organization(id="test-org", name="Test"))
        db.commit()


def test_godaddy_account_secrets_are_encrypted_and_not_returned() -> None:
    with SessionLocal() as db:
        saved = save_account(db, "test-org", "admin", label="Primary", environment="production", api_key="api-key", api_secret="api-secret")
        row = db.query(GoDaddyAccount).one()
        assert "api-key" not in row.encrypted_credentials
        assert "api-secret" not in row.encrypted_credentials
        assert saved["credentials_configured"] is True
        assert "api_key" not in saved and "api_secret" not in saved
        assert list_accounts(db, "test-org")[0]["label"] == "Primary"
        updated = save_account(db, "test-org", "admin", label="Primary renamed", environment="ote", api_key=None, api_secret=None, account_id=saved["id"])
        assert updated["environment"] == "ote"
        client = create_client(db, "test-org", saved["id"])
        client.close()


def test_godaddy_pat_reads_full_zone_and_uses_record_ids_for_dns_updates() -> None:
    seen: list[tuple[str, str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read() if request.method != "GET" else None))
        assert request.headers["Authorization"] == "Bearer pat-token"
        if request.url.path == "/v1/domains":
            return httpx.Response(200, json=[{"domain": "example.com", "authCode": "must-not-be-returned-by-app"}])
        if request.url.path == "/v3/domains/zones/example.com/dns-records":
            return httpx.Response(200, json={"items": [{"recordId": "r1", "name": "@", "type": "A", "data": "192.0.2.1", "ttl": 3600}]})
        return httpx.Response(204)

    with GoDaddyClient("production", "pat-token", "", httpx.MockTransport(respond)) as client:
        assert client.list_domains()[0]["domain"] == "example.com"
        assert client.list_records("Example.com.")[0]["data"] == "192.0.2.1"
        client.add_record("example.com", {"name": "www", "type": "CNAME", "data": "example.com", "ttl": 3600})
        client.replace_group("example.com", "A", "@", [{"name": "@", "type": "A", "data": "192.0.2.2", "ttl": 3600}])
        client.delete_group("example.com", "A", "@")
    assert any(method == "POST" and path.endswith("/dns-records") for method, path, _ in seen)
    assert any(method == "PUT" and path.endswith("/dns-records/r1") for method, path, _ in seen)
    assert any(method == "DELETE" and path.endswith("/dns-records/r1") for method, path, _ in seen)


def test_classic_key_explains_pat_requirement_for_full_dns_zone() -> None:
    with GoDaddyClient("production", "key", "secret", httpx.MockTransport(lambda request: httpx.Response(200, json=[]))) as client:
        with pytest.raises(GoDaddyError, match="Personal Access Token"):
            client.list_records("example.com")


def test_upstream_dns_status_is_preserved_for_diagnostics() -> None:
    with GoDaddyClient("production", "pat-token", "", httpx.MockTransport(lambda request: httpx.Response(404, json={"code": "NOT_FOUND"}))) as client:
        with pytest.raises(GoDaddyError) as error:
            client.list_records("example.com")
        assert error.value.status_code == 404


def test_godaddy_conflict_preserves_safe_provider_error_details() -> None:
    with GoDaddyClient("production", "pat-token", "", httpx.MockTransport(lambda request: httpx.Response(409, json={"code": "ZONE_CONFLICT", "message": "Zone is not ready"}))) as client:
        with pytest.raises(GoDaddyError) as error:
            client.list_records("example.com")
        assert error.value.status_code == 409
        assert "ZONE_CONFLICT" in str(error.value)
        assert "Zone is not ready" in str(error.value)


def test_cancelled_domain_is_rejected_before_dns_zone_request() -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/domains":
            return httpx.Response(200, json=[{"domain": "beabio.com", "status": "CANCELLED", "expires": "2023-02-27T13:45:14.000Z"}])
        pytest.fail("A cancelled domain must not trigger a DNS-zone request")

    with GoDaddyClient("production", "pat-token", "", httpx.MockTransport(respond)) as client:
        with pytest.raises(GoDaddyError, match="CANCELLED.*2023-02-27.*registered again") as error:
            client.list_records("beabio.com")
        assert error.value.status_code == 409
    assert seen == ["/v1/domains"]


def test_godaddy_dns_prevents_readonly_records_and_invalid_ttl() -> None:
    with GoDaddyClient("production", "key", "secret", httpx.MockTransport(lambda request: httpx.Response(204))) as client:
        with pytest.raises(GoDaddyError, match="read-only"):
            client.delete_group("example.com", "NS", "@")
        with pytest.raises(GoDaddyError, match="TTL"):
            client.add_record("example.com", {"name": "@", "type": "A", "data": "192.0.2.1", "ttl": 30})
