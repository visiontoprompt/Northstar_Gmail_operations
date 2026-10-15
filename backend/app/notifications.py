"""Workspace notification transports and password-reset delivery."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AccountGroup, BrowserSession, Employee, EmployeePasswordResetToken, GmailAccount, WorkspaceNotificationSettings, utcnow
from .security import SecretCipher

logger = logging.getLogger(__name__)


class NotificationError(ValueError):
    pass


def _secret_value(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return SecretCipher().decrypt(value).get("value")
    except Exception:
        logger.warning("Unable to decrypt workspace notification secret")
        return None


def _encrypted(value: str) -> str:
    return SecretCipher().encrypt({"value": value})


def notification_settings(db: Session, organization_id: str) -> WorkspaceNotificationSettings:
    row = db.scalar(select(WorkspaceNotificationSettings).where(WorkspaceNotificationSettings.organization_id == organization_id))
    if row is None:
        row = WorkspaceNotificationSettings(id=f"notification_{secrets.token_hex(9)}", organization_id=organization_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return f"{value[:3]}***{value[-2:]}" if len(value) > 5 else "***"


def safe_notification_settings(row: WorkspaceNotificationSettings) -> dict:
    return {
        "telegram_enabled": row.telegram_enabled,
        "telegram_configured": bool(_secret_value(row.telegram_bot_token_encrypted) and row.telegram_chat_id),
        "telegram_chat_id": _mask(row.telegram_chat_id),
        "email_enabled": row.email_enabled,
        "email_provider": row.email_provider,
        "email_configured": bool(row.email_recipient_email and ((row.email_provider == "RESEND" and _secret_value(row.resend_api_key_encrypted)) or (row.email_provider == "SMTP" and row.smtp_host and _secret_value(row.smtp_password_encrypted)))),
        "email_recipient_email": _mask(row.email_recipient_email),
        "email_from_address": row.email_from_address,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_username": _mask(row.smtp_username),
        "smtp_use_tls": row.smtp_use_tls,
        "notify_employee_logins": row.notify_employee_logins,
        "notify_group_reports": row.notify_group_reports,
        "notify_account_status": row.notify_account_status,
        "notify_operation_failures": row.notify_operation_failures,
        "updated_at": row.updated_at,
    }


def update_notification_settings(db: Session, organization_id: str, actor_id: str, values: dict) -> dict:
    row = notification_settings(db, organization_id)
    secret_fields = {"telegram_bot_token": "telegram_bot_token_encrypted", "resend_api_key": "resend_api_key_encrypted", "smtp_password": "smtp_password_encrypted"}
    changed: dict[str, object] = {}
    for key, value in values.items():
        if key in secret_fields:
            if value is not None:
                setattr(row, secret_fields[key], _encrypted(value) if value else None)
                changed[key] = "configured" if value else "cleared"
        elif hasattr(row, key) and value is not None and getattr(row, key) != value:
            setattr(row, key, value)
            changed[key] = "updated" if "password" not in key else "configured"
    if changed:
        from .models import AuditEvent
        db.add(AuditEvent(id=f"audit_{secrets.token_hex(9)}", organization_id=organization_id, actor_id=actor_id, action="notifications.settings.updated", target_type="workspace_notification_settings", target_id=row.id, result="success", metadata_json=changed))
        db.commit()
        db.refresh(row)
    return safe_notification_settings(row)


def _request_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urlopen(request, timeout=12) as response:
            body = response.read().decode()
            return json.loads(body) if body else {}
    except (HTTPError, URLError, TimeoutError) as exc:
        raise NotificationError(f"Notification provider rejected the request: {exc}") from exc


def _send_telegram(row: WorkspaceNotificationSettings, subject: str, body: str) -> None:
    token, chat_id = _secret_value(row.telegram_bot_token_encrypted), row.telegram_chat_id
    if not token or not chat_id:
        raise NotificationError("Telegram is enabled but the bot token or chat ID is missing")
    _request_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": f"{subject}\n\n{body}"})


def _send_email(row: WorkspaceNotificationSettings, subject: str, body: str, *, recipient_override: str | None = None) -> None:
    recipient = recipient_override or row.email_recipient_email
    if not recipient:
        raise NotificationError("Email notifications require a recipient address")
    is_gmail_smtp = row.email_provider == "SMTP" and (row.smtp_host or "").strip().lower() in {"smtp.gmail.com", "smtp.googlemail.com"}
    # Gmail only permits the authenticated mailbox or a verified send-as alias.
    sender = (row.smtp_username if is_gmail_smtp else None) or row.email_from_address or row.smtp_username or "notifications@northstar.local"
    if row.email_provider == "RESEND":
        key = _secret_value(row.resend_api_key_encrypted)
        if not key:
            raise NotificationError("Resend is enabled but its API key is missing")
        _request_json("https://api.resend.com/emails", {"from": sender, "to": [recipient], "subject": subject, "html": f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>"}, {"Authorization": f"Bearer {key}"})
    elif row.email_provider == "SMTP":
        password = _secret_value(row.smtp_password_encrypted)
        if not row.smtp_host or not password:
            raise NotificationError("SMTP is enabled but the host or password is missing")
        username = (row.smtp_username or "").strip()
        if is_gmail_smtp:
            password = "".join(password.split())
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, recipient, subject
        message.set_content(body)
        try:
            # Gmail and most hosted SMTP providers use implicit TLS on 465 and
            # STARTTLS on 587. `smtp_use_tls` means encrypt the connection; the
            # selected port determines which TLS handshake to use.
            if row.smtp_use_tls and row.smtp_port == 465:
                with smtplib.SMTP_SSL(row.smtp_host, row.smtp_port, timeout=12, context=ssl.create_default_context()) as client:
                    if username:
                        client.login(username, password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(row.smtp_host, row.smtp_port, timeout=12) as client:
                    client.ehlo()
                    if row.smtp_use_tls:
                        client.starttls(context=ssl.create_default_context())
                        client.ehlo()
                    if username:
                        client.login(username, password)
                    client.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError, TimeoutError) as exc:
            logger.warning("SMTP delivery failed host=%s port=%s tls=%s error=%s", row.smtp_host, row.smtp_port, row.smtp_use_tls, exc)
            raise NotificationError(
                f"SMTP delivery failed: {exc}. For Gmail use smtp.gmail.com, port 587 with TLS or port 465 with TLS, and a Google app password."
            ) from exc
    else:
        raise NotificationError("Select Resend or SMTP before sending email")


def send_test(db: Session, organization_id: str, channel: str) -> None:
    row = notification_settings(db, organization_id)
    if channel == "telegram":
        _send_telegram(row, "Northstar test notification", "Telegram delivery is configured correctly.")
    else:
        _send_email(row, "Northstar test notification", "Email delivery is configured correctly.")


def publish(db: Session, organization_id: str, event: str, subject: str, body: str) -> None:
    row = notification_settings(db, organization_id)
    enabled = {"employee_login": row.notify_employee_logins, "group_report": row.notify_group_reports, "account_status": row.notify_account_status, "operation_failure": row.notify_operation_failures}.get(event, True)
    if not enabled:
        return
    for channel, configured in (("telegram", row.telegram_enabled), ("email", row.email_enabled)):
        if not configured:
            continue
        try:
            _send_telegram(row, subject, body) if channel == "telegram" else _send_email(row, subject, body)
        except Exception as exc:
            logger.warning("Notification delivery failed channel=%s event=%s: %s", channel, event, exc)


def send_group_status_report(db: Session, organization_id: str, group_id: str | None = None) -> None:
    groups = db.scalars(select(AccountGroup).where(AccountGroup.organization_id == organization_id, *( [AccountGroup.id == group_id] if group_id else [] ))).all()
    lines = []
    for group in groups:
        accounts = db.scalars(select(GmailAccount).where(GmailAccount.organization_id == organization_id, GmailAccount.group_id == group.id, GmailAccount.lifecycle_status != "ARCHIVED")).all()
        active_sessions = db.scalar(select(BrowserSession.id).where(BrowserSession.organization_id == organization_id, BrowserSession.account_id.in_([a.id for a in accounts]), BrowserSession.state.in_(("OPEN", "RUNNING", "NEEDS_ATTENTION"))).limit(1)) if accounts else None
        lines.append(f"{group.name}: {len(accounts)} accounts · active sessions {'yes' if active_sessions else 'no'}")
    publish(db, organization_id, "group_report", "Northstar group status report", "\n".join(lines) or "No account groups found.")


def create_password_reset(db: Session, organization_id: str, employee: Employee, frontend_origin: str) -> str:
    raw = secrets.token_urlsafe(32)
    reset_token = EmployeePasswordResetToken(id=f"reset_{secrets.token_hex(9)}", organization_id=organization_id, employee_id=employee.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=utcnow() + timedelta(hours=1))
    db.add(reset_token)
    db.commit()
    row = notification_settings(db, organization_id)
    try:
        _send_email(row, "Reset your Northstar password", f"Use this link within one hour to reset your password:\n{frontend_origin}/?reset_token={raw}", recipient_override=employee.email)
    except Exception as exc:
        logger.warning("Password reset email delivery failed: %s", exc)
        # Do not leave a usable but undelivered reset credential behind.
        reset_token.used_at = utcnow()
        db.commit()
    return raw


def consume_password_reset(db: Session, token: str, password: str) -> bool:
    row = db.scalar(select(EmployeePasswordResetToken).where(EmployeePasswordResetToken.token_hash == hashlib.sha256(token.encode()).hexdigest(), EmployeePasswordResetToken.used_at.is_(None), EmployeePasswordResetToken.expires_at > utcnow()))
    if not row:
        return False
    employee = db.get(Employee, row.employee_id)
    if not employee:
        return False
    from .auth import set_employee_password
    set_employee_password(db, employee, password)
    row.used_at = utcnow()
    db.commit()
    return True
