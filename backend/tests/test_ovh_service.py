import hashlib
import os

import httpx
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_gmail_ops.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-without-credential-material")
os.environ.setdefault("REDIS_URL", "fakeredis://")

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import ProxyInventory, WorkspaceSettings
from app.ovh_service import OvhClient, OvhError
from app.security import SecretCipher


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app):
        pass


def test_signed_ovh_inventory_and_power_routes(monkeypatch) -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/1.0/auth/time":
            return httpx.Response(200, json=1234567890)
        timestamp = request.headers["X-Ovh-Timestamp"]
        signed = "+".join(("secret", "consumer", request.method, str(request.url), "", timestamp))
        assert request.headers["X-Ovh-Signature"] == "$1$" + hashlib.sha1(signed.encode()).hexdigest()
        seen.append((request.method, request.url.path))
        paths = {
            "/1.0/vps": ["vps-123"],
            "/1.0/vps/vps-123": {"displayName": "Proxy server", "state": "running", "ip": "203.0.113.10"},
            "/1.0/cloud/project": ["project-1"],
            "/1.0/cloud/project/project-1/instance": [{"id": "instance-1", "name": "Cloud node", "status": "SHUTOFF", "ipAddresses": []}],
        }
        if request.method == "POST":
            return httpx.Response(200, json={"id": 123, "state": "todo"})
        return httpx.Response(200, json=paths[request.url.path])

    with OvhClient("ovh-eu", {"ovh_application_key": "app", "ovh_application_secret": "secret", "ovh_consumer_key": "consumer"}, httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(client, "_timestamp", lambda: 1234567890)
        inventory = client.list_resources()
        assert [item["resource_type"] for item in inventory["items"]] == ["vps", "instance"]
        assert inventory["items"][0]["status"] == "running"
        assert inventory["items"][1]["status"] == "SHUTOFF"
        client.power("vps", "vps-123", None, "stop")
        client.power("instance", "project-1", "instance-1", "start")
        assert ("POST", "/1.0/vps/vps-123/stop") in seen
        assert ("POST", "/1.0/cloud/project/project-1/instance/instance-1/start") in seen
        with pytest.raises(OvhError):
            client.power("vps", "../other", None, "stop")


def test_ovh_auth_errors_distinguish_invalid_credentials_from_missing_acl() -> None:
    def denied(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            return httpx.Response(200, json=1234567890)
        return httpx.Response(403, json={"message": "This call has not been granted"})

    with OvhClient("ovh-eu", {"ovh_application_key": "app", "ovh_application_secret": "secret", "ovh_consumer_key": "consumer"}, httpx.MockTransport(denied)) as client:
        client._timestamp = lambda: 1234567890
        with pytest.raises(OvhError, match="HTTP 403.*This call has not been granted"):
            client.call("GET", "/vps")


def test_ovh_settings_encrypt_credentials_and_never_return_them() -> None:
    with TestClient(app) as client:
        saved = client.put("/api/v1/settings", json={"ovh_endpoint": "ovh-eu", "ovh_application_key": "app-key", "ovh_application_secret": "app-secret", "ovh_consumer_key": "consumer-key"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["ovh_credentials_configured"] is True
        assert "app-secret" not in saved.text
        with SessionLocal() as db:
            row = db.query(WorkspaceSettings).one()
            assert "app-secret" not in row.ovh_credentials_encrypted
            assert SecretCipher().decrypt(row.ovh_credentials_encrypted)["ovh_application_secret"] == "app-secret"
        partial = client.put("/api/v1/settings", json={"ovh_application_key": "incomplete"})
        assert partial.status_code == 422
        cleared = client.put("/api/v1/settings", json={"ovh_clear_credentials": True})
        assert cleared.status_code == 200
        assert cleared.json()["ovh_credentials_configured"] is False


def test_ovh_api_lists_resources_and_audits_accepted_power_request(monkeypatch) -> None:
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_resources(self):
            return {"items": [{"resource_type": "vps", "service_name": "vps-123", "instance_id": None, "name": "Proxy server", "status": "running", "ip": None, "project_name": None}], "errors": []}

        def power(self, resource_type, service_name, instance_id, action):
            assert (resource_type, service_name, instance_id, action) == ("vps", "vps-123", None, "stop")
            return {"id": 1}

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    with TestClient(app) as client:
        inventory = client.get("/api/v1/infrastructure/ovh")
        assert inventory.status_code == 200
        assert inventory.json()["items"][0]["name"] == "Proxy server"
        action = client.post("/api/v1/infrastructure/ovh/power", json={"resource_type": "vps", "service_name": "vps-123", "action": "stop"})
        assert action.status_code == 202, action.text
        assert action.json()["status"] == "accepted"
        with SessionLocal() as db:
            from app.models import AuditEvent
            audit = db.query(AuditEvent).filter_by(action="ovh.vps.stop").one()
            assert audit.target_id == "vps-123"


def test_ovh_start_succeeds_with_clear_unverified_proxy_result_when_recovery_is_unconfigured(monkeypatch) -> None:
    from app.main import settings

    calls = []
    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def power(self, resource_type, service_name, instance_id, action):
            calls.append((resource_type, service_name, instance_id, action))

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    monkeypatch.setattr("app.main.recovery_configured", lambda: (False, "Set OVH_PROXY_RECOVERY_USERNAME and OVH_PROXY_RECOVERY_PASSWORD in the server environment."))
    monkeypatch.setattr("app.main.start_recovery_thread", lambda *_args: pytest.fail("Unconfigured recovery must not start"))
    with TestClient(app) as client:
        result = client.post("/api/v1/infrastructure/ovh/power", json={"resource_type": "instance", "service_name": settings.ovh_proxy_recovery_project_id, "instance_id": settings.ovh_proxy_recovery_instance_id, "action": "start"})
        assert result.status_code == 202, result.text
        assert calls == [("instance", settings.ovh_proxy_recovery_project_id, settings.ovh_proxy_recovery_instance_id, "start")]
        assert "OVH_PROXY_RECOVERY_USERNAME" in result.json()["recovery_warning"]
        assert "skipped" in result.json()["recovery_warning"]
        assert result.json()["recovery_job"] is None
        assert client.get(f"/api/v1/infrastructure/ovh/recovery/latest?service_name={settings.ovh_proxy_recovery_project_id}&instance_id={settings.ovh_proxy_recovery_instance_id}").status_code == 404
        assert client.post("/api/v1/infrastructure/ovh/recovery/start").status_code == 422


def test_running_ovh_instance_can_start_manual_proxy_verification_without_power_cycle(monkeypatch) -> None:
    from app.main import settings

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def list_resources(self):
            return {"items": [{"resource_type": "instance", "service_name": settings.ovh_proxy_recovery_project_id, "instance_id": settings.ovh_proxy_recovery_instance_id, "status": "ACTIVE"}]}

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    monkeypatch.setattr("app.main.recovery_configured", lambda: (True, None))
    monkeypatch.setattr("app.main.validate_openstack_config", lambda: None)
    monkeypatch.setattr("app.ovh_proxy_recovery.start_recovery_thread", lambda *_args: None)
    with TestClient(app) as client:
        started = client.post("/api/v1/infrastructure/ovh/recovery/start")
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "QUEUED"
        assert client.post("/api/v1/infrastructure/ovh/recovery/start").status_code == 409


def test_configured_ovh_start_keeps_automatic_proxy_recovery(monkeypatch) -> None:
    from app.main import settings

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def power(self, *_args): return {"accepted": True}

    started = []
    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    monkeypatch.setattr("app.main.recovery_configured", lambda: (True, None))
    monkeypatch.setattr("app.main.validate_openstack_config", lambda: None)
    monkeypatch.setattr("app.main.start_recovery_thread", started.append)
    with TestClient(app) as client:
        result = client.post("/api/v1/infrastructure/ovh/power", json={"resource_type": "instance", "service_name": settings.ovh_proxy_recovery_project_id, "instance_id": settings.ovh_proxy_recovery_instance_id, "action": "start"})
        assert result.status_code == 202, result.text
        assert result.json()["recovery_warning"] is None
        assert result.json()["recovery_job"]["status"] == "QUEUED"
        assert started == [result.json()["recovery_job"]["id"]]


def test_ovh_start_still_works_when_openstack_validation_fails(monkeypatch) -> None:
    from app.main import settings
    from app.ovh_proxy_recovery import RecoveryError

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def power(self, *_args): return {"accepted": True}

    def invalid_openstack() -> None:
        raise RecoveryError("OpenStack cloud configuration is unavailable.")

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    monkeypatch.setattr("app.main.recovery_configured", lambda: (True, None))
    monkeypatch.setattr("app.main.validate_openstack_config", invalid_openstack)
    with TestClient(app) as client:
        result = client.post("/api/v1/infrastructure/ovh/power", json={"resource_type": "instance", "service_name": settings.ovh_proxy_recovery_project_id, "instance_id": settings.ovh_proxy_recovery_instance_id, "action": "start"})
        assert result.status_code == 202, result.text
        assert result.json()["recovery_job"] is None
        assert "OpenStack cloud configuration is unavailable" in result.json()["recovery_warning"]


def test_ovh_stop_never_includes_proxy_setup_warning(monkeypatch) -> None:
    from app.main import settings

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def power(self, *_args): return {"accepted": True}

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    monkeypatch.setattr("app.main.request_recovery_cancel", lambda *_args: True)
    monkeypatch.setattr("app.main.recovery_configured", lambda: pytest.fail("Stop must not check recovery configuration"))
    with TestClient(app) as client:
        result = client.post("/api/v1/infrastructure/ovh/power", json={"resource_type": "instance", "service_name": settings.ovh_proxy_recovery_project_id, "instance_id": settings.ovh_proxy_recovery_instance_id, "action": "stop"})
        assert result.status_code == 202, result.text
        assert result.json()["action"] == "stop"
        assert result.json()["recovery_warning"] is None
        assert result.json()["recovery_job"] is None


def test_ovh_resource_can_be_linked_to_proxy_group_and_shows_live_status(monkeypatch) -> None:
    with SessionLocal() as db:
        db.add(ProxyInventory(id="proxy-1", organization_id="org_demo", proxy_group="Proxy pool A", encrypted_payload=None))
        db.commit()

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def list_resources(self):
            return {"items": [{"resource_type": "vps", "service_name": "vps-123", "instance_id": None, "name": "Edge proxy node", "status": "running", "ip": "203.0.113.12", "project_name": None}], "errors": []}

    monkeypatch.setattr("app.main.configured_client", lambda *_args: FakeClient())
    with TestClient(app) as client:
        linked = client.post("/api/v1/infrastructure/ovh/proxy-groups", json={"proxy_group": "Proxy pool A", "resource_type": "vps", "service_name": "vps-123"})
        assert linked.status_code == 201, linked.text
        inventory = client.get("/api/v1/infrastructure/ovh/proxy-groups")
        assert inventory.status_code == 200
        assert inventory.json()["items"] == [{"id": linked.json()["id"], "proxy_group": "Proxy pool A", "resource_type": "vps", "service_name": "vps-123", "instance_id": None, "server_name": "Edge proxy node", "status": "running", "ip": "203.0.113.12"}]
        removed = client.delete(f"/api/v1/infrastructure/ovh/proxy-groups/{linked.json()['id']}")
        assert removed.status_code == 200
