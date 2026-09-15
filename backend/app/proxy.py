"""Validation and runtime normalization for account-scoped proxies."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


class ProxyValidationError(ValueError):
    """Raised when an account proxy does not match a supported format."""


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


@dataclass(frozen=True)
class ProxyConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def server(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def proxy_id(self) -> str:
        return f"proxy_{hashlib.sha256(f'{self.host}:{self.port}'.encode()).hexdigest()[:20]}"

    @property
    def display_endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def authenticated(self) -> bool:
        return self.username is not None

    def playwright_options(self) -> dict[str, str]:
        options: dict[str, str] = {"server": self.server}
        if self.username is not None and self.password is not None:
            options.update({"username": self.username, "password": self.password})
        return options

    def encrypted_payload(self) -> dict[str, str | int | None]:
        return {"host": self.host, "port": self.port, "username": self.username, "password": self.password}


def parse_proxy(value: str | None) -> ProxyConfig | None:
    """Parse ``ip:port`` or ``username:password:ip:port``.

    The input deliberately does not accept schemes or arbitrary URLs. This
    keeps the supported surface explicit and prevents a malformed value from
    being silently interpreted as a different network target.
    """
    if value is None or not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) == 2:
        username = password = None
        host, port_text = parts
    elif len(parts) == 4:
        username, password, host, port_text = parts
        if not username or not password:
            raise ProxyValidationError("Proxy username and password are required")
    else:
        raise ProxyValidationError("Use proxy format ip:port or username:password:ip:port")
    host = host.strip()
    if not host or not _HOST_RE.fullmatch(host) or host.startswith(".") or host.endswith("."):
        raise ProxyValidationError("Proxy host must be an IP address or hostname")
    try:
        port = int(port_text)
    except (TypeError, ValueError) as exc:
        raise ProxyValidationError("Proxy port must be a number between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ProxyValidationError("Proxy port must be a number between 1 and 65535")
    return ProxyConfig(host=host, port=port, username=username, password=password)


def proxy_from_payload(value: object) -> ProxyConfig | None:
    """Rehydrate only the internal encrypted proxy object."""
    if not isinstance(value, dict):
        return None
    host = value.get("host")
    port = value.get("port")
    username = value.get("username")
    password = value.get("password")
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    return parse_proxy(
        f"{username}:{password}:{host}:{port}" if username is not None and password is not None else f"{host}:{port}"
    )
