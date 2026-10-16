import os
import shutil
import signal
import subprocess
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
    # Do not create the profile dir ahead of a real login: a profile is only
    # persisted once the account is fully connected. Chrome itself creates the
    # user-data dir the first time the observer launches the browser.
    active_port_file = profile_path / "DevToolsActivePort"
    try:
        if profile_path.exists():
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


def _descendant_process_pids(root_pids: set[int], proc_root: Path = Path("/proc")) -> set[int]:
    """Include Chrome's child processes when resolving its X11 window owner.

    On some Chromium builds the X11 window's _NET_WM_PID belongs to a Chrome
    child instead of the browser process that owns --user-data-dir. Match only
    descendants of the profile-specific browser process so one account cannot
    focus another account's window.
    """
    if not root_pids:
        return set()
    children: dict[int, list[int]] = {}
    try:
        entries = proc_root.iterdir()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                # The comm field is parenthesized and may itself contain
                # spaces or ')' characters; fields after its final ')' start
                # with state, then ppid.
                fields = stat[stat.rfind(")") + 1 :].split()
                if len(fields) < 2:
                    continue
                parent_pid = int(fields[1])
                pid = int(entry.name)
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(parent_pid, []).append(pid)
    except (OSError, PermissionError):
        return set()
    descendants = set(root_pids)
    pending = list(root_pids)
    while pending:
        for child_pid in children.get(pending.pop(), []):
            if child_pid not in descendants:
                descendants.add(child_pid)
                pending.append(child_pid)
    return descendants


def _window_pid_uses_profile(pid: int, profile_path: Path, root_pids: set[int], proc_root: Path = Path("/proc")) -> bool:
    """Match a window owner to its profile-bearing Chrome ancestor."""
    target = str(profile_path)
    seen: set[int] = set()
    current = pid
    for _ in range(64):
        if current <= 1 or current in seen:
            break
        if current in root_pids:
            return True
        seen.add(current)
        proc = proc_root / str(current)
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            if target in command:
                return True
            fields = (proc / "stat").read_text().rsplit(")", 1)[-1].split()
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return False


def local_browser_is_alive(profile_id: str, session_id: str | None = None) -> bool:
    """Return whether the account's dedicated browser is still alive.

    The browser is owned by the observer thread, so liveness is derived from
    the profile-specific command line. This prevents a durable session row from
    blocking a retry after its Chrome process has already exited.
    """
    profile_path = (Path(get_settings().profile_root) / profile_id).resolve()
    return bool(_find_chrome_pids_for_profile(profile_path))


def focus_local_browser_profile(profile_id: str, display: str | None = None) -> bool:
    """Raise the visible Chrome window belonging to one saved account profile.

    Matching the window's owning PID to the profile-specific Chrome command
    line lets the desktop session switcher focus the requested account without
    launching a duplicate. Authenticated operators pass their private X display.
    """
    profile_path = (Path(get_settings().profile_root) / profile_id).resolve()
    browser_roots = set(_find_chrome_pids_for_profile(profile_path))
    if not browser_roots:
        return False
    browser_pids = _descendant_process_pids(browser_roots)
    if not browser_pids:
        browser_pids = browser_roots
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        logger.warning("cannot focus browser profile %s: wmctrl is not installed", profile_id)
        return False
    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display
    try:
        windows = subprocess.run(
            [wmctrl, "-lp"], capture_output=True, text=True, check=False, timeout=3, env=env
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("cannot list desktop windows while focusing profile %s", profile_id)
        return False
    if windows.returncode != 0:
        logger.warning("wmctrl could not list windows while focusing profile %s", profile_id)
        return False
    candidates: list[tuple[int, str]] = []
    for line in windows.stdout.splitlines():
        columns = line.split(maxsplit=4)
        if len(columns) < 4:
            continue
        try:
            window_pid = int(columns[2])
        except ValueError:
            continue
        if window_pid not in browser_pids and not _window_pid_uses_profile(window_pid, profile_path, browser_roots):
            continue
        # Prefer a window owned by Chrome's profile-bearing root process, then
        # try windows owned by its renderer/utility children. This avoids
        # activating an arbitrary transient child window first.
        candidates.append((0 if window_pid in browser_roots else 1, columns[0]))
    for _rank, window_id in sorted(candidates):
        try:
            focused = subprocess.run(
                [wmctrl, "-ia", window_id], capture_output=True, text=True,
                check=False, timeout=3, env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("cannot focus desktop window for profile %s", profile_id)
            focused = None
        if focused is not None and focused.returncode == 0:
            return True
        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                raised = subprocess.run([xdotool, "windowraise", window_id], capture_output=True, text=True, check=False, timeout=3, env=env)
                activated = subprocess.run([xdotool, "windowactivate", "--sync", "--clearmodifiers", window_id], capture_output=True, text=True, check=False, timeout=3, env=env)
                if raised.returncode == 0 and activated.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                logger.exception("xdotool could not focus desktop window for profile %s", profile_id)
    if not candidates:
        logger.warning("no X11 window matched browser profile %s on display %s", profile_id, env.get("DISPLAY", ""))
    else:
        logger.warning("all %s X11 focus attempts failed for browser profile %s on display %s", len(candidates), profile_id, env.get("DISPLAY", ""))
    return False


def local_browser_profiles_alive(profile_ids: set[str]) -> set[str]:
    """Return live profile IDs while scanning /proc only once for a session list."""
    root = Path(get_settings().profile_root)
    path_to_profile = {str((root / profile_id).resolve()): profile_id for profile_id in profile_ids}
    unresolved = set(path_to_profile)
    alive: set[str] = set()
    if not unresolved:
        return alive
    try:
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                command_line = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except (OSError, IsADirectoryError):
                continue
            for profile_path in tuple(unresolved):
                if profile_path in command_line:
                    alive.add(path_to_profile[profile_path])
                    unresolved.remove(profile_path)
            if not unresolved:
                break
    except (FileNotFoundError, PermissionError):
        pass
    return alive


def remove_local_profile(profile_id: str) -> None:
    """Delete the on-disk profile for an account that never fully connected.

    A profile is only kept once the account is fully signed in; sessions that
    close before authentication discard their scratch user-data dir so no
    half-created profile lingers for accounts that were never connected.
    """
    root = Path(get_settings().profile_root).resolve()
    target = (root / profile_id).resolve()
    if not target.is_relative_to(root) or target == root:
        logger.warning("refusing to remove profile outside the profile root: %s", target)
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
        logger.info("removed unsaved profile dir %s", target)


def close_local_browser(session_id: str) -> None:
    """Terminate the session's browser.

    The browser is closed by observer.stop()/context.close(); there is no
    separately spawned process to terminate here, so this stays a no-op that
    guarantees the call site contract is safe.
    """
    return None


from logging import getLogger

logger = getLogger(__name__)
