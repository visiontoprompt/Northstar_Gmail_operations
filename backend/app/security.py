import base64
import hashlib
import hmac
import json
import struct
import time

from cryptography.fernet import Fernet

from .config import get_settings


class SecretCipher:
    """Application boundary for encrypted account credential material."""

    def __init__(self) -> None:
        raw = get_settings().secret_key.encode("utf-8")
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
        self._fernet = Fernet(derived)

    def encrypt(self, payload: dict[str, str]) -> str:
        return self._fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()

    def decrypt(self, value: str) -> dict[str, str]:
        return json.loads(self._fernet.decrypt(value.encode()).decode())


def normalize_totp_secret(value: str) -> str:
    """Normalize a Google Authenticator base32 secret into its canonical form:
    uppercase, no spaces or dashes, RFC 4648 padding applied."""
    compact = "".join(ch for ch in value.strip() if ch not in " -")
    return compact.upper() + "=" * ((8 - len(compact) % 8) % 8)


def generate_totp(secret: str, at: float | None = None) -> str:
    """RFC 6238 time-based one-time password (HMAC-SHA1, 6 digits, 30s period).

    ``secret`` may be a bare or space/dash-padded base32 Authenticator key as
    stored by the app. No external dependency is required.
    """
    key = base64.b32decode(normalize_totp_secret(secret).encode("ascii"))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def mask_recovery(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        visible = local[:1] if local else ""
        return f"{visible}{'•' * max(3, min(8, len(local) - 1))}@{domain}"
    return f"{'•' * max(4, min(12, len(value) - 2))}{value[-2:]}"
