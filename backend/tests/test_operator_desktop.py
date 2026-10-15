import socket
import threading
from pathlib import Path

from app.config import get_settings
import app.operator_desktop as operator_desktop
from app.operator_desktop import _slot_for, _websocket_ready


def test_service_gives_xvfb_a_private_writable_tmp_namespace() -> None:
    service_file = Path(__file__).resolve().parents[2] / "deployment" / "northstar.service"
    unit = service_file.read_text()

    assert "PrivateTmp=true" in unit
    assert "ProtectSystem=strict" in unit


def test_operator_desktop_slots_are_persisted_and_never_shared(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "profile_root", str(tmp_path / "browser-profiles"))

    one = _slot_for("employee_one")
    two = _slot_for("employee_two")

    assert one != two
    assert _slot_for("employee_one") == one
    assert Path(tmp_path / ".operator-desktops.json").exists()


def test_managed_process_uses_popen_identity_instead_of_command_line_match(monkeypatch) -> None:
    class RunningProcess:
        def poll(self) -> None:
            return None

    key = ("operator_test", "x11vnc")
    monkeypatch.setitem(operator_desktop._PROCESSES, key, RunningProcess())
    monkeypatch.setattr(operator_desktop, "_process_matches", lambda _pattern: False)

    assert operator_desktop._process_is_alive(key, "fragile command line pattern") is True


def test_spawn_keeps_process_errors_visible_to_service_journal(monkeypatch) -> None:
    captured = {}

    class RunningProcess:
        def poll(self) -> None:
            return None

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return RunningProcess()

    key = ("operator_test", "x11vnc")
    monkeypatch.delitem(operator_desktop._PROCESSES, key, raising=False)
    monkeypatch.setattr(operator_desktop.subprocess, "Popen", fake_popen)

    operator_desktop._spawn(key, ["x11vnc", "-display", ":123"])

    assert captured.get("stderr") is None
    assert captured["stdout"] is operator_desktop.subprocess.DEVNULL


def test_websocket_readiness_requires_a_successful_upgrade() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve_upgrade() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(8192)
                connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
        finally:
            listener.close()

    thread = threading.Thread(target=serve_upgrade)
    thread.start()
    try:
        assert _websocket_ready(port) is True
    finally:
        thread.join(timeout=2)


def test_websocket_readiness_rejects_a_tcp_only_listener() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve_non_upgrade() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(8192)
                connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        finally:
            listener.close()

    thread = threading.Thread(target=serve_non_upgrade)
    thread.start()
    try:
        assert _websocket_ready(port) is False
    finally:
        thread.join(timeout=2)
