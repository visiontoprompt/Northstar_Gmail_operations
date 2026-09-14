from unittest.mock import Mock, patch

from app import runtime
from app.main import settings


def test_browser_observer_uses_an_ephemeral_cdp_port_per_profile(tmp_path) -> None:
    original_launch = settings.browser_launch_enabled
    original_observer = settings.browser_observer_enabled
    original_profile_root = settings.profile_root
    original_cdp_port = settings.browser_cdp_port
    settings.browser_launch_enabled = True
    settings.browser_observer_enabled = True
    settings.profile_root = str(tmp_path)
    settings.browser_cdp_port = 9222
    process = Mock(pid=1234)
    process.poll.return_value = None
    try:
        with patch("app.runtime.shutil.which", return_value="/usr/bin/google-chrome"), patch("app.runtime.subprocess.Popen", return_value=process) as popen, patch("app.runtime._wait_for_cdp_port", return_value=41001), patch("app.runtime._terminate_stale_browser") as kill_stale:
            launched, pid = runtime.launch_local_browser("profile_1", "session_1")
        command = popen.call_args.args[0]
        assert launched is True
        assert pid == 1234
        assert "--remote-debugging-port=0" in command
        assert "--remote-debugging-port=9222" not in command
        assert "--new-window" in command
        assert "--disable-background-networking" not in command
        assert "--no-session-restore" not in command
        assert runtime.browser_debug_port("session_1") == 41001
        kill_stale.assert_called_once()
    finally:
        runtime.close_local_browser("session_1")
        settings.browser_launch_enabled = original_launch
        settings.browser_observer_enabled = original_observer
        settings.profile_root = original_profile_root
        settings.browser_cdp_port = original_cdp_port
