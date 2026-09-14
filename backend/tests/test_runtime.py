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