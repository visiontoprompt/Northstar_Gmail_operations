from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import notifications


def smtp_settings(*, port: int, tls: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        email_provider="SMTP",
        email_recipient_email="alerts@example.com",
        email_from_address=None,
        smtp_host="smtp.gmail.com",
        smtp_port=port,
        smtp_username="owner@gmail.com",
        smtp_password_encrypted="encrypted",
        smtp_use_tls=tls,
    )


def test_gmail_smtp_587_uses_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(notifications, "_secret_value", lambda _: "abcd efgh ijkl mnop")
    smtp = MagicMock(return_value=nullcontext(client))
    monkeypatch.setattr(notifications.smtplib, "SMTP", smtp)

    notifications._send_email(smtp_settings(port=587), "Subject", "Body")

    smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=12)
    client.ehlo.assert_any_call()
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("owner@gmail.com", "abcdefghijklmnop")
    client.send_message.assert_called_once()


def test_gmail_smtp_465_uses_implicit_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(notifications, "_secret_value", lambda _: "app-password")
    smtp_ssl = MagicMock(return_value=nullcontext(client))
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", smtp_ssl)

    notifications._send_email(smtp_settings(port=465), "Subject", "Body")

    assert smtp_ssl.call_args.args[:2] == ("smtp.gmail.com", 465)
    client.starttls.assert_not_called()
    client.login.assert_called_once_with("owner@gmail.com", "app-password")
    client.send_message.assert_called_once()


def test_smtp_tls_disabled_uses_plain_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(notifications, "_secret_value", lambda _: "password")
    smtp = MagicMock(return_value=nullcontext(client))
    smtp_ssl = MagicMock()
    monkeypatch.setattr(notifications.smtplib, "SMTP", smtp)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", smtp_ssl)

    notifications._send_email(smtp_settings(port=2525, tls=False), "Subject", "Body")

    smtp.assert_called_once_with("smtp.gmail.com", 2525, timeout=12)
    smtp_ssl.assert_not_called()
    client.starttls.assert_not_called()
    client.send_message.assert_called_once()


def test_smtp_disconnect_returns_actionable_notification_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.login.side_effect = notifications.smtplib.SMTPServerDisconnected("connection closed")
    monkeypatch.setattr(notifications, "_secret_value", lambda _: "password")
    monkeypatch.setattr(notifications.smtplib, "SMTP", MagicMock(return_value=nullcontext(client)))

    with pytest.raises(notifications.NotificationError, match="Google app password"):
        notifications._send_email(smtp_settings(port=587), "Subject", "Body")


def test_password_reset_is_sent_to_employee_not_notification_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.added = []
            self.commits = 0

        def add(self, value: object) -> None:
            self.added.append(value)

        def commit(self) -> None:
            self.commits += 1

    db = FakeDb()
    monkeypatch.setattr(notifications, "notification_settings", lambda *_: smtp_settings(port=587))
    sent: dict[str, object] = {}

    def capture_email(_row: object, subject: str, body: str, *, recipient_override: str | None = None) -> None:
        sent.update(subject=subject, body=body, recipient=recipient_override)

    monkeypatch.setattr(notifications, "_send_email", capture_email)
    employee = SimpleNamespace(id="employee-1", email="person@example.com")

    notifications.create_password_reset(db, "org-1", employee, "https://northstar.example")  # type: ignore[arg-type]

    assert sent["recipient"] == "person@example.com"
    assert sent["subject"] == "Reset your Northstar password"
    assert "reset_token=" in str(sent["body"])
    assert db.commits == 1


def test_failed_password_reset_delivery_invalidates_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.added = []
            self.commits = 0

        def add(self, value: object) -> None:
            self.added.append(value)

        def commit(self) -> None:
            self.commits += 1

    db = FakeDb()
    monkeypatch.setattr(notifications, "notification_settings", lambda *_: smtp_settings(port=587))

    def fail_email(*_args: object, **_kwargs: object) -> None:
        raise notifications.NotificationError("SMTP failed")

    monkeypatch.setattr(notifications, "_send_email", fail_email)
    employee = SimpleNamespace(id="employee-1", email="person@example.com")

    notifications.create_password_reset(db, "org-1", employee, "https://northstar.example")  # type: ignore[arg-type]

    token = db.added[0]
    assert token.used_at is not None
    assert db.commits == 2
