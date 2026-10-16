from unittest.mock import patch

from app import runtime
from app.main import settings


def test_launch_local_browser_preflights_profile_without_a_debug_port(tmp_path) -> None:
    original_launch = settings.browser_launch_enabled
    original_profile_root = settings.profile_root
    settings.browser_launch_enabled = True
    settings.profile_root = str(tmp_path)
    try:
        with patch("app.runtime.resolve_browser_executable", return_value="/usr/bin/google-chrome"), patch("app.runtime._terminate_stale_browser") as kill_stale:
            launched, pid = runtime.launch_local_browser("profile_1", "session_1")
        assert launched is True
        assert pid is None, "the observer owns the browser process; nothing is spawned here"
        kill_stale.assert_called_once()
        assert not (tmp_path / "profile_1" / "DevToolsActivePort").exists()
    finally:
        settings.browser_launch_enabled = original_launch
        settings.profile_root = original_profile_root


def test_launch_local_browser_skips_when_disabled(tmp_path) -> None:
    original_launch = settings.browser_launch_enabled
    original_profile_root = settings.profile_root
    settings.browser_launch_enabled = False
    settings.profile_root = str(tmp_path)
    try:
        with patch("app.runtime.resolve_browser_executable", return_value="/usr/bin/google-chrome"):
            launched, pid = runtime.launch_local_browser("profile_1", "session_1")
        assert launched is False
        assert pid is None
    finally:
        settings.browser_launch_enabled = original_launch
        settings.profile_root = original_profile_root


def test_resolve_browser_executable_uses_configured_then_known_browsers() -> None:
    original = settings.browser_executable
    settings.browser_executable = "google-chrome"
    try:
        with patch("app.runtime.shutil.which", side_effect=lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None):
            assert runtime.resolve_browser_executable() == "/usr/bin/google-chrome"
    finally:
        settings.browser_executable = original


def test_resolve_browser_executable_falls_back_across_browsers() -> None:
    original = settings.browser_executable
    settings.browser_executable = "google-chrome"
    try:
        with patch("app.runtime.shutil.which", side_effect=lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None):
            assert runtime.resolve_browser_executable() == "/usr/bin/brave-browser"
    finally:
        settings.browser_executable = original


def test_focus_local_browser_profile_activates_matching_window(tmp_path) -> None:
    from subprocess import CompletedProcess

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["env"]["DISPLAY"] == ":107"
        if args[1] == "-lp":
            return CompletedProcess(args, 0, "0x045 0 4321 localhost Chrome\n", "")
        return CompletedProcess(args, 0, "", "")

    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", return_value="/usr/bin/wmctrl"), \
             patch("app.runtime.subprocess.run", side_effect=run):
            assert runtime.focus_local_browser_profile("profile_1", display=":107") is True
        assert calls == [["/usr/bin/wmctrl", "-lp"], ["/usr/bin/wmctrl", "-ia", "0x045"]]
    finally:
        settings.profile_root = original_profile_root


def test_focus_local_browser_profile_reports_missing_window(tmp_path) -> None:
    from subprocess import CompletedProcess

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", return_value="/usr/bin/wmctrl"), \
             patch("app.runtime.subprocess.run", return_value=CompletedProcess([], 0, "0x045 0 8765 localhost Chrome\n", "")):
            assert runtime.focus_local_browser_profile("profile_1") is False
    finally:
        settings.profile_root = original_profile_root


def test_launch_local_browser_does_not_create_profile_dir_before_connection(tmp_path) -> None:
    original_launch = settings.browser_launch_enabled
    original_profile_root = settings.profile_root
    settings.browser_launch_enabled = True
    settings.profile_root = str(tmp_path)
    try:
        with patch("app.runtime.resolve_browser_executable", return_value="/usr/bin/google-chrome"), patch("app.runtime._terminate_stale_browser"):
            launched, _pid = runtime.launch_local_browser("notyet@example.com__org_demo", "session_1")
        assert launched is True
        assert not (tmp_path / "notyet@example.com__org_demo").exists(), "a profile dir must only appear once the account is connected"
    finally:
        settings.browser_launch_enabled = original_launch
        settings.profile_root = original_profile_root


def test_remove_local_profile_deletes_only_within_the_profile_root(tmp_path) -> None:
    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    try:
        folder = tmp_path / "ghost@example.com__org_demo"
        folder.mkdir(parents=True)
        (folder / "Cookies").write_text("scratch")
        runtime.remove_local_profile("ghost@example.com__org_demo")
        assert not folder.exists()

        outside = tmp_path / ".." / "escaped" / "profile"
        outside.mkdir(parents=True, exist_ok=True)
        runtime.remove_local_profile("../../escaped/profile")
        assert outside.exists(), "paths escaping the profile root must be refused"
    finally:
        settings.profile_root = original_profile_root
