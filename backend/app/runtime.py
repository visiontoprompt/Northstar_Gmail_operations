import os
import shutil
import signal
import time
from pathlib import Path

from .config import get_settings

# Browsers resolved from BROWSER_EXECUTABLE first, then any known local
# Chromium-family binary. The observer launches this binary directly with a
# persistent user profile, so Chrome's own command line never carries a
# debugging/automation flag that Google's sign-in may refuse.
_BROWSER_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
    "brave-browser",
    "brave-browser-stable",
)


def resolve_browser_executable() -> str | None:
    """Resolve the Chromium-family binary used for login sessions.

    BROWSER_EXECUTABLE wins when set and found; otherwise the first known
    local browser that exists is used so a bare dev.sh works on any machine.
    """
    settings = get_settings()
    configured = settings.browser_executable or ""
    if configured:
        path = shutil.which(configured)
        if path:
            return path
        logger.warning("configured BROWSER_EXECUTABLE %r not found on PATH; scanning known browsers", configured)
    for name in _BROWSER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def launch_local_browser(profile_id: str, session_id: str) -> tuple[bool, int | None]:
    """Prepare the account's persistent profile for a login session.

    The browser itself is launched by the observer through Playwright's
    persistent-context API (see observer._launch_profile_context), so the
    launched Chrome command line contains no --remote-debugging-port or
    --enable-automation flags that Google's sign-in rejects. This function only
    validates the environment, creates the profile dir, and clears any stale
    browser lock so the re-login opens a fresh dedicated window.
    """
    settings = get_settings()
    if not settings.browser_launch_enabled:
        logger.warning("browser launch skipped for session %s: BROWSER_LAUNCH_ENABLED is false", session_id)
        return False, None
    executable = resolve_browser_executable()
    if not executable:
        logger.warning("browser launch failed for session %s: no Chromium-family executable found (set BROWSER_EXECUTABLE)", session_id)
        return False, None
    profile_path = (Path(settings.profile_root) / profile_id).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    active_port_file = profile_path / "DevToolsActivePort"
    try:
        active_port_file.unlink(missing_ok=True)
    except OSError:
        return False, None
    _terminate_stale_browser(profile_path)
    return True, None


def _terminate_stale_browser(profile_path: Path) -> None:
    """Kill any leftover Chrome bound to this profile dir so the observer's
    launch opens a fresh, dedicated window instead of being swallowed into the
    old instance."""
    stale = _find_chrome_pids_for_profile(profile_path)
    for pid in stale:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    if stale:
        time.sleep(0.5)
    for pid in stale:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _find_chrome_pids_for_profile(profile_path: Path) -> list[int]:
    """Return PIDs of running browser processes whose command line references
    the profile dir. Used so a re-login never piggybacks on an existing window
    and so a durable session row never blocks a retry after its Chrome exited."""
    pids = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except (OSError, IsADirectoryError):
                continue
            if str(profile_path) in cmd:
                try:
                    pids.append(int(entry.name))
                except ValueError:
                    continue
    except (FileNotFoundError, PermissionError):
        pass
    return pids


def local_browser_is_alive(profile_id: str, session_id: str | None = None) -> bool:
    """Return whether the account's dedicated browser is still alive.

    The browser is owned by the observer thread, so liveness is derived from
    the profile-specific command line. This prevents a durable session row from
    blocking a retry after its Chrome process has already exited.
    """
    profile_path = (Path(get_settings().profile_root) / profile_id).resolve()
    return bool(_find_chrome_pids_for_profile(profile_path))


def close_local_browser(session_id: str) -> None:
    """Terminate the session's browser.

    The browser is closed by observer.stop()/context.close(); there is no
    separately spawned process to terminate here, so this stays a no-op that
    guarantees the call site contract is safe.
    """
    return None


from logging import getLogger

logger = getLogger(__name__)