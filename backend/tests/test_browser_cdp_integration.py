import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("google-chrome") is None and shutil.which("google-chrome-stable") is None,
    reason="Chrome not installed",
)

from app.observer import BrowserObserver


@pytest.fixture
def chrome_profile(tmp_path: Path):
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--user-data-dir={tmp_path / 'profile'}",
            "--remote-debugging-port=0",
            "--no-first-run",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    port = None
    active = tmp_path / "profile" / "DevToolsActivePort"
    while time.monotonic() < deadline and port is None:
        if active.exists():
            try:
                port = int(active.read_text().strip().splitlines()[0])
            except (OSError, ValueError):
                pass
        if port is None:
            time.sleep(0.25)
    yield port
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_observer_connects_to_chrome_cdp(chrome_profile):
    if chrome_profile is None:
        pytest.skip("Chrome did not expose DevToolsActivePort")
    observed = []
    observer = BrowserObserver("s1", "p1", chrome_profile, observe_session_page=lambda sid, url, title, text: observed.append((url, title)), interval=0.05)
    observer.start()
    time.sleep(1.2)
    observer.stop()
    assert any(("about:blank" in url or url) for url, _ in observed)