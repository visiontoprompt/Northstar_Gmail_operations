from unittest.mock import patch
from pathlib import Path

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
        if args[1] == "-lpx":
            return CompletedProcess(args, 0, "0x045 0 4321 google-chrome.Google-chrome localhost Chrome\n", "")
        return CompletedProcess(args, 0, "", "")

    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", side_effect=lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None), \
             patch("app.runtime.subprocess.run", side_effect=run):
            assert runtime.focus_local_browser_profile("profile_1", display=":107") is True
        assert calls == [["/usr/bin/wmctrl", "-lpx"], ["/usr/bin/wmctrl", "-ia", "0x045"]]
    finally:
        settings.profile_root = original_profile_root


def test_focus_local_browser_profile_reports_missing_window(tmp_path) -> None:
    from subprocess import CompletedProcess

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", return_value="/usr/bin/wmctrl"), \
             patch("app.runtime.subprocess.run", return_value=CompletedProcess([], 0, "0x045 0 8765 google-chrome.Google-chrome localhost Chrome\n", "")):
            assert runtime.focus_local_browser_profile("profile_1") is False
    finally:
        settings.profile_root = original_profile_root


def test_focus_local_browser_profile_matches_chrome_child_window(tmp_path) -> None:
    from subprocess import CompletedProcess

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "-lpx":
            return CompletedProcess(args, 0, "0x071 0 8765 google-chrome.Google-chrome localhost Chrome\n", "")
        return CompletedProcess(args, 0, "", "")

    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime._descendant_process_pids", return_value={4321, 8765}), \
             patch("app.runtime.shutil.which", side_effect=lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None), \
             patch("app.runtime.subprocess.run", side_effect=run):
            assert runtime.focus_local_browser_profile("profile_1", display=":107") is True
        assert calls == [["/usr/bin/wmctrl", "-lpx"], ["/usr/bin/wmctrl", "-ia", "0x071"]]
    finally:
        settings.profile_root = original_profile_root


def test_focus_local_browser_profile_falls_back_to_xdotool(tmp_path) -> None:
    from subprocess import CompletedProcess

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)

    def run(args, **kwargs):
        if args[1] == "-lpx":
            return CompletedProcess(args, 0, "0x072 0 4321 google-chrome.Google-chrome localhost Chrome\n", "")
        if args[0] == "/usr/bin/wmctrl":
            return CompletedProcess(args, 1, "", "cannot activate window")
        if args[1:] == ["getwindowfocus"]:
            return CompletedProcess(args, 0, "0x072\n", "")
        return CompletedProcess(args, 0, "", "")

    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"wmctrl", "xdotool"} else None), \
             patch("app.runtime.subprocess.run", side_effect=run) as run_mock:
            assert runtime.focus_local_browser_profile("profile_1", display=":107") is True
        assert [call.args[0][1:] for call in run_mock.call_args_list] == [["-lpx"], ["-ia", "0x072"], ["windowraise", "0x072"], ["windowactivate", "--sync", "--clearmodifiers", "0x072"], ["getwindowfocus"]]
    finally:
        settings.profile_root = original_profile_root


def test_focus_local_browser_profile_uses_profile_window_class(tmp_path) -> None:
    from subprocess import CompletedProcess
    import hashlib

    original_profile_root = settings.profile_root
    settings.profile_root = str(tmp_path)
    expected_class = f"northstar-profile-{hashlib.sha256(b'profile_2').hexdigest()[:16]}"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["-lpx"]:
            # PID is deliberately unrelated; class identity should win.
            return CompletedProcess(args, 0, f"0x400001 0 9999 {expected_class}.{expected_class} localhost Chrome\n", "")
        if args[1:] == ["getwindowfocus"]:
            return CompletedProcess(args, 0, "4194305\n", "")
        return CompletedProcess(args, 0, "", "")

    try:
        with patch("app.runtime._find_chrome_pids_for_profile", return_value=[4321]), \
             patch("app.runtime.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"wmctrl", "xdotool"} else None), \
             patch("app.runtime.subprocess.run", side_effect=run):
            assert runtime.focus_local_browser_profile("profile_2", display=":107") is True
        assert calls[0] == ["/usr/bin/wmctrl", "-lpx"]
        assert ["/usr/bin/wmctrl", "-ia", "0x400001"] in calls
        assert ["/usr/bin/xdotool", "getwindowfocus"] in calls
    finally:
        settings.profile_root = original_profile_root


def test_window_pid_matches_profile_from_ancestor(tmp_path) -> None:
    parent = tmp_path / "4321"
    child = tmp_path / "8765"
    parent.mkdir()
    child.mkdir()
    (parent / "cmdline").write_bytes(b"/usr/bin/chrome\x00--user-data-dir=/profiles/profile_1\x00")
    (parent / "stat").write_text("4321 (chrome browser) S 1 0")
    (child / "cmdline").write_bytes(b"/usr/bin/chrome\x00--type=renderer\x00")
    (child / "stat").write_text("8765 (chrome renderer) S 4321 0")

    assert runtime._window_pid_uses_profile(8765, Path("/profiles/profile_1"), set(), proc_root=tmp_path) is True


def test_descendant_process_pids_handles_comm_with_spaces_and_parentheses(tmp_path) -> None:
    parent = tmp_path / "100"
    child = tmp_path / "101"
    grandchild = tmp_path / "102"
    unrelated = tmp_path / "200"
    for path in (parent, child, grandchild, unrelated):
        path.mkdir()
    # /proc stat fields after the last ')' are state then ppid.
    (parent / "stat").write_text("100 (chrome (browser)) S 1 0 0")
    (child / "stat").write_text("101 (chrome child) S 100 0 0")
    (grandchild / "stat").write_text("102 (renderer child) S 101 0 0")
    (unrelated / "stat").write_text("200 (other chrome) S 1 0 0")

    assert runtime._descendant_process_pids({100}, proc_root=tmp_path) == {100, 101, 102}


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
