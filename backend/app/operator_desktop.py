"""Per-operator virtual X desktops for private headed browser sessions.

The browser desktops are local-only services. Nginx selects the authenticated
operator's websockify port after an application auth_request; ports are never
bound to a public interface.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("gmail_ops.operator_desktop")
_LOCK = threading.RLock()
_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}
_SLOT_COUNT = 10_000


def _mapping_path() -> Path:
    return Path(get_settings().profile_root).resolve().parent / ".operator-desktops.json"


def _load_mapping() -> dict[str, int]:
    path = _mapping_path()
    try:
        value = json.loads(path.read_text())
        return {str(key): int(slot) for key, slot in value.items() if 0 <= int(slot) < _SLOT_COUNT}
    except (OSError, ValueError, TypeError):
        return {}


def _slot_for(employee_id: str) -> int:
    path = _mapping_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping = _load_mapping()
    if employee_id in mapping:
        return mapping[employee_id]
    used = set(mapping.values())
    # Deterministic starting point plus persisted collision resolution.
    import hashlib
    start = int.from_bytes(hashlib.sha256(employee_id.encode()).digest()[:4], "big") % _SLOT_COUNT
    slot = next(((start + offset) % _SLOT_COUNT for offset in range(_SLOT_COUNT) if (start + offset) % _SLOT_COUNT not in used), None)
    if slot is None:
        raise RuntimeError("No private browser desktop slots remain")
    mapping[employee_id] = slot
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(mapping, sort_keys=True))
    temporary.chmod(0o600)
    temporary.replace(path)
    return slot


def _port_open(port: int) -> bool:
    # A TCP connect to x11vnc is not a harmless probe: each probe is treated
    # as a VNC client and consumes a slot while its handshake times out. The
    # desktop auth endpoint may be called concurrently, so inspect the local
    # listener table without creating connections.
    try:
        with Path("/proc/net/tcp").open() as listeners:
            next(listeners, None)
            for line in listeners:
                fields = line.split()
                address, hex_port = fields[1].split(":")
                if address == "0100007F" and int(hex_port, 16) == port and fields[3] == "0A":
                    return True
        return False
    except (OSError, ValueError, IndexError):
        # Development environments without Linux procfs can use a TCP probe.
        pass
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _websocket_ready(port: int) -> bool:
    """Confirm websockify completes an RFC 6455 upgrade, not just TCP listen."""
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        "GET /websockify HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.settimeout(1)
            connection.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response and len(response) < 8192:
                chunk = connection.recv(1024)
                if not chunk:
                    break
                response.extend(chunk)
        return bytes(response).startswith(b"HTTP/1.1 101 ") or bytes(response).startswith(b"HTTP/1.0 101 ")
    except OSError:
        return False


def _process_matches(pattern: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-f", pattern], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def _process_is_alive(key: tuple[str, str], pattern: str) -> bool:
    """Prefer the owned Popen handle; use pgrep only for pre-existing children.

    The pgrep fallback protects ports/displays left by a process outside this
    API process. For children launched here, Popen.poll() is an exact identity
    check and avoids brittle matching of platform-specific command lines.
    """
    process = _PROCESSES.get(key)
    if process is not None:
        return process.poll() is None
    return _process_matches(pattern)


def _spawn(key: tuple[str, str], argv: list[str], env: dict[str, str] | None = None) -> None:
    process = _PROCESSES.get(key)
    if process is not None and process.poll() is None:
        return
    # Keep stderr attached to the systemd journal. In particular, x11vnc's
    # startup errors (bad display, unavailable X extensions, bind failures)
    # must be visible instead of collapsing into an unexplained 503.
    _PROCESSES[key] = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def ensure_operator_desktop(employee_id: str) -> dict[str, int | str]:
    """Start (or reuse) an operator's private X/VNC stack and return its route."""
    if not employee_id or employee_id in {"admin_demo", "admin_token"}:
        # Synthetic development/API principals retain the install-time display.
        return {"display": ":99", "websocket_port": 6080, "slot": -1}
    with _LOCK:
        slot = _slot_for(employee_id)
        display_number = 100 + slot
        display = f":{display_number}"
        vnc_port = 20000 + slot
        websocket_port = 30000 + slot
        env = os.environ.copy()
        env["DISPLAY"] = display
        x_socket = Path(f"/tmp/.X11-unix/X{display_number}")
        if x_socket.exists() and not _process_is_alive((employee_id, "xvfb"), f"[X]vfb {display} "):
            raise RuntimeError("The allocated private display is occupied by an unknown process")
        if not x_socket.exists():
            _spawn((employee_id, "xvfb"), ["Xvfb", display, "-screen", "0", "1600x900x24", "-nolisten", "tcp", "-ac"], env)
        deadline = time.monotonic() + 8
        while not Path(f"/tmp/.X11-unix/X{display_number}").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not Path(f"/tmp/.X11-unix/X{display_number}").exists():
            raise RuntimeError("Private virtual display could not be started")
        try:
            if not _process_matches(f"[o]penbox --sm-disable --display {display}$"):
                _spawn((employee_id, "openbox"), ["openbox", "--sm-disable", "--display", display], env)
        except FileNotFoundError:
            _spawn((employee_id, "openbox"), ["openbox", "--sm-disable", "--display", display], env)
        if _port_open(vnc_port) and not _process_is_alive((employee_id, "x11vnc"), f"[x]11vnc .* -rfbport {vnc_port}($| )"):
            raise RuntimeError("The allocated private VNC port is occupied by an unknown process")
        if not _port_open(vnc_port):
            _spawn((employee_id, "x11vnc"), ["x11vnc", "-display", display, "-localhost", "-nopw", "-forever", "-shared", "-rfbport", str(vnc_port), "-noxdamage", "-repeat", "-xkb"], env)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            vnc_process = _PROCESSES.get((employee_id, "x11vnc"))
            if vnc_process is not None and vnc_process.poll() is not None:
                break
            if _port_open(vnc_port):
                break
            time.sleep(0.1)
        vnc_listening = _port_open(vnc_port)
        vnc_process = _PROCESSES.get((employee_id, "x11vnc"))
        vnc_exit_code = vnc_process.poll() if vnc_process is not None else None
        vnc_process_alive = _process_is_alive((employee_id, "x11vnc"), f"[x]11vnc .* -rfbport {vnc_port}($| )")
        if not vnc_listening or not vnc_process_alive:
            logger.error(
                "Private VNC startup failed slot=%s display=%s port=%s listening=%s managed_exit_code=%s managed_process_alive=%s",
                slot,
                display,
                vnc_port,
                vnc_listening,
                vnc_exit_code,
                vnc_process_alive,
            )
            raise RuntimeError(
                "Private VNC server failed startup "
                f"(port_listening={vnc_listening}, process_exit_code={vnc_exit_code})"
            )
        viewer_listening = _port_open(websocket_port)
        if viewer_listening and not _process_is_alive((employee_id, "websockify"), f"[w]ebsockify 127.0.0.1:{websocket_port}($| )"):
            raise RuntimeError("The allocated private viewer port is occupied by an unknown process")
        viewer_ready = viewer_listening
        if not viewer_listening:
            _spawn((employee_id, "websockify"), ["websockify", f"127.0.0.1:{websocket_port}", f"127.0.0.1:{vnc_port}"], env)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if _port_open(websocket_port) and _websocket_ready(websocket_port):
                    viewer_ready = True
                    break
                time.sleep(0.1)
        if not viewer_ready or not _port_open(websocket_port) or not _process_is_alive((employee_id, "websockify"), f"[w]ebsockify 127.0.0.1:{websocket_port}($| )"):
            raise RuntimeError("Private browser viewer did not complete its WebSocket handshake")
        return {"display": display, "websocket_port": websocket_port, "slot": slot}
