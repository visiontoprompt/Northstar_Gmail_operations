import shutil
import subprocess
import time
from pathlib import Path

from .config import get_settings

_processes: dict[str, subprocess.Popen[bytes]] = {}
_cdp_ports: dict[str, int] = {}


def launch_local_browser(profile_id: str, session_id: str) -> tuple[bool, int | None]:
    """Open a dedicated persistent Chrome process with CDP enabled for the observer.

    The worker never passes credentials: the profile directory is reused and the
    observer only reads the page state so classify_page can drive safe transitions.
    """
    settings = get_settings()
    if not settings.browser_launch_enabled:
        return False, None
    executable = shutil.which(settings.browser_executable)
    if not executable:
        return False, None
    profile_path = (Path(settings.profile_root) / profile_id).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    command = [
        executable,
        f"--user-data-dir={profile_path}",
        "--profile-directory=Default",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "https://accounts.google.com/",
    ]
    if settings.browser_observer_enabled:
        port = settings.browser_cdp_port or 0
        command.insert(1, f"--remote-debugging-port={port}")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False, None
    _processes[session_id] = process
    if settings.browser_observer_enabled:
        cdp_port = _wait_for_cdp_port(profile_path, settings.browser_cdp_port)
        if cdp_port:
            _cdp_ports[session_id] = cdp_port
        else:
            logger.warning("browser started without a reachable CDP endpoint for session %s", session_id)
    return True, process.pid


def _wait_for_cdp_port(profile_path: Path, requested_port: int, timeout: float = 10.0) -> int | None:
    """Chrome writes the chosen debugging port into 'DevToolsActivePort'."""
    if requested_port:
        return requested_port
    active = profile_path / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active.exists():
            try:
                line = active.read_text().strip().splitlines()
                if line:
                    return int(line[0])
            except (OSError, ValueError):
                pass
        time.sleep(0.25)
    return None


def browser_debug_port(session_id: str) -> int | None:
    return _cdp_ports.get(session_id)


def close_local_browser(session_id: str) -> None:
    process = _processes.pop(session_id, None)
    _cdp_ports.pop(session_id, None)
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


from logging import getLogger

logger = getLogger(__name__)