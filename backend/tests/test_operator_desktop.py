from pathlib import Path

from app.config import get_settings
from app.operator_desktop import _slot_for


def test_operator_desktop_slots_are_persisted_and_never_shared(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "profile_root", str(tmp_path / "browser-profiles"))

    one = _slot_for("employee_one")
    two = _slot_for("employee_two")

    assert one != two
    assert _slot_for("employee_one") == one
    assert Path(tmp_path / ".operator-desktops.json").exists()
