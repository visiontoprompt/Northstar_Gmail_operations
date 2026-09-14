import shutil
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("google-chrome") is None and shutil.which("google-chrome-stable") is None,
    reason="Chrome not installed",
)

from app.observer import BrowserObserver


def test_observer_launches_persistent_browser_and_observes(tmp_path: Path):
    observed = []
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    observer = BrowserObserver(
        "s1",
        "p1",
        0,
        profile_dir=str(tmp_path / "profile"),
        executable=chrome,
        headless=True,
        start_url="data:text/html,<h1>Sign in</h1>",
        observe_session_page=lambda sid, url, title, text: observed.append((url, title)),
        interval=0.05,
        settle_seconds=0.0,
    )
    observer.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not observed:
        time.sleep(0.05)
    observer.stop()
    assert any(url.startswith("data:") for url, _ in observed), observed
    # The persistent profile stays on disk after the browser closes so a later
    # session can reuse the saved cookies/state without re-authentication.
    profile = tmp_path / "profile"
    assert profile.exists()
    assert (profile / "Default").exists() or (profile / "Local State").exists()