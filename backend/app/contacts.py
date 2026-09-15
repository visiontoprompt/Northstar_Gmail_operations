"""Contact-file validation and Google Contacts import assets."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from .config import get_settings


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_HEADER = "Name,Given Name,Family Name,E-mail 1 - Value"
_SUPPORTED_SUFFIXES = {".csv", ".txt", ".vcf"}
MAX_CONTACT_FILE_BYTES = 10 * 1024 * 1024


def parse_emails(raw_text: str) -> list[str]:
    """Parse, validate, and deduplicate emails from newline-separated text.

    Returns a list of lowercase, stripped emails. Raises ValueError on
    invalid input.
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("No emails provided")
    if len(lines) > 500:
        raise ValueError(f"Too many emails (max 500, got {len(lines)})")
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        email = line.lower()
        if not _EMAIL_RE.match(email):
            raise ValueError(f"Invalid email address: {line}")
        if email not in seen:
            seen.add(email)
            result.append(email)
    return result


def build_google_contacts_csv(emails: list[str]) -> str:
    """Generate a Google Contacts-compatible CSV from a list of emails.

    The CSV uses the header row that Google Contacts expects for import.
    Each email gets a row where Name and Given Name are derived from the
    local part of the address.
    """
    rows = [_HEADER]
    for email in emails:
        local = email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ").title()
        rows.append(f'"{local}","{local}","","{email}"')
    return "\n".join(rows) + "\n"


def extract_contact_emails(content: bytes) -> list[str]:
    """Extract and normalize email addresses from CSV/TXT/VCF content."""
    text = content.decode("utf-8-sig", errors="strict")
    emails = [match.lower() for match in re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, flags=re.IGNORECASE)]
    return list(dict.fromkeys(emails))


def validate_contact_file(filename: str, content: bytes) -> tuple[str, int, str]:
    """Validate a reusable contact asset without returning its contents."""
    safe_name = Path(filename or "contacts.csv").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError("Contact files must be CSV, TXT, or VCF format")
    if not content:
        raise ValueError("The contact file is empty")
    if len(content) > MAX_CONTACT_FILE_BYTES:
        raise ValueError("Contact files must be smaller than 10 MB")
    try:
        emails = extract_contact_emails(content)
    except UnicodeDecodeError as exc:
        raise ValueError("The contact file must use UTF-8 encoding") from exc
    if not emails:
        raise ValueError("No email addresses were found in the contact file")
    media_type = {".csv": "text/csv", ".txt": "text/plain", ".vcf": "text/vcard"}[suffix]
    return safe_name, len(emails), media_type


def contact_file_path(organization_id: str, file_id: str, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    return Path(get_settings().profile_root) / "contact-library" / organization_id / f"{file_id}{suffix}"


def write_contact_file(organization_id: str, file_id: str, filename: str, content: bytes) -> Path:
    path = contact_file_path(organization_id, file_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def contact_file_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def new_contact_file_id() -> str:
    return f"contact_file_{uuid.uuid4().hex[:20]}"


def write_contacts_upload(account_id: str, csv_content: str) -> Path:
    """Write CSV to a temp file under profile_root/contacts-uploads/.

    Returns the path to the written file.
    """
    settings = get_settings()
    upload_dir = Path(settings.profile_root) / "contacts-uploads" / account_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    csv_path = upload_dir / "upload.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return csv_path
