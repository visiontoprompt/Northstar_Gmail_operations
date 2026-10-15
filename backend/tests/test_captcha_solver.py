import threading
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import captcha_solver
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import WorkspaceSettings


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_2captcha_key_is_encrypted_and_only_configuration_flag_is_returned():
    with TestClient(app) as client:
        saved = client.put("/api/v1/settings", json={"two_captcha_enabled": True, "two_captcha_api_key": "secret-2captcha-key"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["two_captcha_enabled"] is True
        assert saved.json()["two_captcha_api_key_configured"] is True
        assert "secret-2captcha-key" not in saved.text
        with SessionLocal() as db:
            row = db.scalar(select(WorkspaceSettings))
            assert row is not None
            assert "secret-2captcha-key" not in row.two_captcha_api_key_encrypted


def test_2captcha_recaptcha_task_polls_for_a_token_without_exposing_the_key(monkeypatch):
    calls = []
    replies = [{"taskId": 123}, {"status": "processing"}, {"status": "ready", "solution": {"gRecaptchaResponse": "solved-token"}}]

    def post(path, payload):
        calls.append((path, payload))
        return replies.pop(0)

    monkeypatch.setattr(captcha_solver, "_post", post)
    cancel = MagicMock(spec=threading.Event)
    cancel.wait.return_value = False
    token = captcha_solver.solve_widget("private-key", "recaptcha_v2", "https://example.com/signup?private=1", "site-key", cancel)

    assert token == "solved-token"
    assert calls[0][0] == "createTask"
    assert calls[0][1]["task"]["type"] == "RecaptchaV2TaskProxyless"
    assert calls[0][1]["task"]["websiteURL"] == "https://example.com/signup"
    assert [path for path, _ in calls[1:]] == ["getTaskResult", "getTaskResult"]


def test_unsupported_captcha_never_creates_a_paid_task(monkeypatch):
    calls = []
    monkeypatch.setattr(captcha_solver, "_post", lambda *args: calls.append(args))
    try:
        captcha_solver.solve_widget("private-key", "phone", "https://example.com", "site-key", threading.Event())
    except captcha_solver.CaptchaSolveError:
        pass
    else:
        assert False, "Unsupported verification must remain manual"
    assert calls == []
