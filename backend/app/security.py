import base64
import hashlib
import json

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


def mask_recovery(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        visible = local[:1] if local else ""
        return f"{visible}{'•' * max(3, min(8, len(local) - 1))}@{domain}"
    return f"{'•' * max(4, min(12, len(value) - 2))}{value[-2:]}"
