import shutil
import subprocess
from pathlib import Path

from .config import get_settings

_processes: dict[str, subprocess.Popen[bytes]] = {}


def launch_local_browser(profile_id: str, session_id: str) -> tuple[bool, int | None]:
    """Open a dedicated persistent Chrome process without passing credentials."""
    settings = get_settings()
    if not settings.browser_launch_enabled:
        return False, None
    executable = shutil.which(settings.browser_executable)
    if not executable:
        return False, None
    profile_path = (Path(settings.profile_root) / profile_id).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            executable,
            f"--user-data-dir={profile_path}",
            "--profile-directory=Default",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "https://accounts.google.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _processes[session_id] = process
    return True, process.pid


def close_local_browser(session_id: str) -> None:
    process = _processes.pop(session_id, None)
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
