from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import OvhProxyRecoveryJob, Organization
from app.ovh_proxy_recovery import EXPECTED_FIXED_IPS, RecoveryError
import app.ovh_proxy_recovery as recovery


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app):
        pass


def _settings(**overrides):
    values = dict(
        ovh_proxy_recovery_enabled=True,
        ovh_proxy_recovery_project_id="project-test",
        ovh_proxy_recovery_instance_id="instance-test",
        ovh_proxy_recovery_region="US-EAST-VA-1",
        ovh_proxy_recovery_port=18080,
        ovh_proxy_recovery_username="proxyuser",
        ovh_proxy_recovery_password="never-log-this",
        ovh_proxy_recovery_ip_check_url="https://api4.ipify.org",
        ovh_proxy_recovery_probe_attempts=3,
        ovh_proxy_recovery_readiness_timeout_seconds=20,
        ovh_proxy_recovery_network_grace_seconds=0,
        ovh_proxy_recovery_poll_seconds=1,
        openstack_cloud="test-cloud",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeNetwork:
    def __init__(self, fail_first_bind=False):
        self.records = {
            f"fip-{ip}": {"id": f"fip-{ip}", "floating_ip_address": ip, "port_id": f"port-{index}", "fixed_ip_address": fixed, "status": "ACTIVE"}
            for index, (ip, fixed) in enumerate(EXPECTED_FIXED_IPS.items())
        }
        self.updates = []
        self.fail_first_bind = fail_first_bind
        self.failed = False

    def ips(self):
        return list(self.records.values())

    def get_port(self, port_id):
        fixed_ip = next(fixed for index, fixed in enumerate(EXPECTED_FIXED_IPS.values()) if port_id == f"port-{index}")
        return {"id": port_id, "device_id": "instance-test", "fixed_ips": [{"ip_address": fixed_ip}]}

    def update_ip(self, resource_id, **attrs):
        self.updates.append((resource_id, dict(attrs)))
        if attrs.get("port_id") and self.fail_first_bind and not self.failed:
            self.failed = True
            raise RuntimeError("provider response redacted")
        record = self.records[resource_id]
        if "port_id" in attrs:
            record["port_id"] = attrs["port_id"]
            record["fixed_ip_address"] = attrs.get("fixed_ip_address") if attrs["port_id"] else None
        record["status"] = "ACTIVE" if record["port_id"] else "DOWN"
        return record

    def get_ip(self, resource_id):
        return self.records[resource_id]


class FakeConnection:
    def __init__(self, network):
        self.network = network

    def close(self):
        pass


def _make_job():
    with SessionLocal() as db:
        organization = db.get(Organization, "org_demo")
        assert organization
        job = OvhProxyRecoveryJob(
            id="recovery-test", organization_id="org_demo", resource_key="project-test/instance-test",
            lock_key="project-test/instance-test", service_name="project-test", instance_id="instance-test",
            status="QUEUED", stage="QUEUED", message="queued", total_count=15,
            completed_count=0, cancel_requested=False, results=[],
        )
        db.add(job)
        db.commit()


def _run(monkeypatch, statuses, network=None):
    network = network or FakeNetwork()
    attempts = {ip: 0 for ip in EXPECTED_FIXED_IPS}

    def probe(ip):
        attempts[ip] += 1
        outcome = statuses.get(ip, "HEALTHY")
        if isinstance(outcome, list):
            index = min(attempts[ip] - 1, len(outcome) - 1)
            outcome = outcome[index]
        return {"status": outcome, "exit_ip": ip if outcome == "HEALTHY" else None, "error": None if outcome == "HEALTHY" else f"{outcome} test", "attempts": 3, "latency_ms": 4}

    monkeypatch.setattr(recovery, "get_settings", lambda: _settings())
    monkeypatch.setattr(recovery, "_instance_status", lambda *_: "ACTIVE")
    monkeypatch.setattr(recovery, "_open_neutron", lambda: FakeConnection(network))
    monkeypatch.setattr(recovery, "_probe_with_retries", probe)
    monkeypatch.setattr(recovery.time, "sleep", lambda *_: None)
    recovery._run_recovery_job("recovery-test")
    with SessionLocal() as db:
        job = db.get(OvhProxyRecoveryJob, "recovery-test")
        return job.status, job.message, list(job.results or []), network


def test_healthy_start_verifies_all_proxies_without_reassociation(monkeypatch) -> None:
    _make_job()
    status, message, results, network = _run(monkeypatch, {})
    assert status == "PROXY_READY"
    assert "all 15" in message
    assert len(results) == 15
    assert all(result["status"] == "HEALTHY" for result in results)
    assert network.updates == []


def test_persistent_connection_timeout_recovers_only_affected_proxy(monkeypatch) -> None:
    _make_job()
    target = next(iter(EXPECTED_FIXED_IPS))
    status, _message, results, network = _run(monkeypatch, {target: ["TIMEOUT", "HEALTHY"]})
    assert status == "PROXY_READY"
    target_updates = [attrs for resource_id, attrs in network.updates if resource_id == f"fip-{target}"]
    assert target_updates == [{"port_id": None}, {"port_id": "port-0", "fixed_ip_address": EXPECTED_FIXED_IPS[target]}]
    assert len(network.updates) == 2
    assert next(row for row in results if row["floating_ip"] == target)["recovered"] is True


def test_407_authentication_failure_does_not_rebind(monkeypatch) -> None:
    _make_job()
    target = next(iter(EXPECTED_FIXED_IPS))
    status, _message, results, network = _run(monkeypatch, {target: "AUTH_FAILED"})
    assert status == "FAILED"
    assert network.updates == []
    result = next(row for row in results if row["floating_ip"] == target)
    assert result["status"] == "AUTH_FAILED"
    assert result["error"] == "AUTH_FAILED test"


def test_reassociation_failure_retries_saved_original_binding(monkeypatch) -> None:
    _make_job()
    target = next(iter(EXPECTED_FIXED_IPS))
    network = FakeNetwork(fail_first_bind=True)
    status, _message, results, network = _run(monkeypatch, {target: ["TIMEOUT", "HEALTHY"]}, network)
    assert status == "FAILED"  # Restored safely, but final proxy verification is not healthy.
    result = next(row for row in results if row["floating_ip"] == target)
    assert result["status"] == "RESTORED"
    assert network.records[f"fip-{target}"]["port_id"] == "port-0"
    assert network.records[f"fip-{target}"]["fixed_ip_address"] == EXPECTED_FIXED_IPS[target]


def test_invalid_original_fixed_ip_aborts_before_any_detach(monkeypatch) -> None:
    _make_job()
    network = FakeNetwork()
    target = next(iter(EXPECTED_FIXED_IPS))
    network.records[f"fip-{target}"]["fixed_ip_address"] = "10.99.99.99"
    status, message, _results, network = _run(monkeypatch, {}, network)
    assert status == "FAILED"
    assert "expected fixed IP" in message
    assert network.updates == []


def test_duplicate_start_or_recovery_job_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(recovery, "start_recovery_thread", lambda _job_id: None)
    first = recovery.create_recovery_job("org_demo", "project-test", "instance-test", "actor-1")
    assert first["status"] == "QUEUED"
    with pytest.raises(RecoveryError, match="already active"):
        recovery.create_recovery_job("org_demo", "project-test", "instance-test", "actor-2")


def test_external_probe_distinguishes_timeout_and_407(monkeypatch) -> None:
    class Response:
        status_code = 407
        text = "Proxy Authentication Required"
        def raise_for_status(self):
            raise httpx.HTTPStatusError("407", request=MagicMock(), response=self)

    class FakeClient:
        def __init__(self, *, proxy, **_kwargs):
            assert "never-log-this" not in str(proxy)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, _url): return Response()

    monkeypatch.setattr(recovery.httpx, "Client", FakeClient)
    auth_result = recovery.probe_proxy("40.160.98.129", proxy_port=18080, username="proxyuser", password="never-log-this", url="https://api4.ipify.org")
    assert auth_result["status"] == "AUTH_FAILED"

    class TimeoutClient(FakeClient):
        def get(self, _url): raise httpx.ConnectTimeout("timeout")
    monkeypatch.setattr(recovery.httpx, "Client", TimeoutClient)
    timeout_result = recovery.probe_proxy("40.160.98.129", proxy_port=18080, username="proxyuser", password="secret", url="https://api4.ipify.org")
    assert timeout_result["status"] == "TIMEOUT"
