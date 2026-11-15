import ast
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from deskremote import i18n
from deskremote.core.config import Config, default_config, load_config
from deskremote.i18n import EN, set_language, tr
from deskremote.ui.search import COUNTRIES
from deskremote.ui.settings import HOTKEY_LABELS, KIND_LABELS, SettingsWindow
from deskremote.ui.worker import OFFLINE_TEXT

SRC = Path(__file__).resolve().parents[1] / "src" / "deskremote"


@pytest.fixture(autouse=True)
def german_again():
    yield
    set_language("de")


def _tr_literals() -> set[str]:
    """Alle Texte, die im Quellcode direkt an tr() gehen – auch in Dict-Ausdrücken."""
    found = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
                elif isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Dict):
                    found.update(v.value for v in arg.value.values if isinstance(v, ast.Constant))
    return found


def test_every_text_has_an_english_version():
    keys = _tr_literals() | set(HOTKEY_LABELS.values()) | set(KIND_LABELS.values())
    keys |= {label for label, _ in COUNTRIES} | {OFFLINE_TEXT}
    assert len(keys) > 100  # der Scan findet wirklich etwas
    assert sorted(k for k in keys if k not in EN) == []


def test_english_placeholders_match_german():
    import string
    names = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
    assert [k for k, v in EN.items() if names(k) != names(v)] == []


def test_german_is_the_default_and_unchanged():
    assert i18n.language() == "de"
    assert tr("Beenden") == "Beenden"
    assert tr("In {room} läuft nichts", room="Bad") == "In Bad läuft nichts"


def test_english_with_placeholders():
    set_language("en")
    assert tr("Beenden") == "Quit"
    assert tr("In {room} läuft nichts", room="Bad") == "Nothing is playing in Bad"


def test_missing_translation_falls_back_to_german():
    set_language("en")
    assert tr("Gibt es nicht {x}", x=1) == "Gibt es nicht 1"


def test_unknown_language_means_german():
    set_language("fr")
    assert i18n.language() == "de"


def test_existing_config_without_language_stays_german(tmp_path, monkeypatch):
    monkeypatch.setattr("deskremote.core.config.system_language", lambda: "en")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"title": "Daheim"}), encoding="utf-8")
    assert load_config(path).language == "de"


def test_first_start_follows_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("deskremote.core.config.system_language", lambda: "en")
    assert load_config(tmp_path / "fehlt.json").language == "en"
    assert default_config().language == "en"


def test_language_is_saved_and_checked(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"title": "Daheim", "language": "en"}), encoding="utf-8")
    assert load_config(path).language == "en"
    path.write_text(json.dumps({"title": "Daheim", "language": "xx"}), encoding="utf-8")
    assert load_config(path).title == "DeskRemote"  # Unbekanntes wie jede Abweichung: Standardwerte


def _settings(qtbot, cfg):
    saved = []
    w = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w)
    return w, saved


def test_settings_switch_language_offers_restart(qtbot, monkeypatch):
    cfg = Config()
    w, saved = _settings(qtbot, cfg)
    monkeypatch.setattr(w, "ask_restart", lambda: True)
    w.language.setCurrentIndex(w.language.findData("en"))
    with qtbot.waitSignal(w.restart_requested, timeout=1000):
        w.findChild(QPushButton, "save").click()
    assert cfg.language == "en" and saved == [cfg]
    assert i18n.language() == "de"  # laufende Sitzung bleibt bis zum Neustart einheitlich


def test_settings_restart_declined_or_unchanged(qtbot, monkeypatch):
    cfg = Config()
    w, _ = _settings(qtbot, cfg)
    asked = []
    monkeypatch.setattr(w, "ask_restart", lambda: asked.append(1) or False)
    restarts = []
    w.restart_requested.connect(lambda: restarts.append(1))
    w.findChild(QPushButton, "save").click()  # Sprache gleich: keine Frage
    assert asked == [] and restarts == []

    w2, _ = _settings(qtbot, cfg)
    monkeypatch.setattr(w2, "ask_restart", lambda: asked.append(1) or False)
    w2.restart_requested.connect(lambda: restarts.append(1))
    w2.language.setCurrentIndex(w2.language.findData("en"))
    w2.findChild(QPushButton, "save").click()
    assert asked == [1] and restarts == [] and cfg.language == "en"


def test_settings_window_in_english(qtbot):
    set_language("en")
    w, _ = _settings(qtbot, Config())
    assert w.windowTitle() == "Settings"
    texts = {b.text() for b in w.findChildren(QPushButton)}
    assert {"Save", "Cancel", "Delete"} <= texts
