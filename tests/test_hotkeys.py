import pytest

from deskremote.core.config import default_config
from deskremote.ui.hotkeys import (
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, NoTarget, dispatch, parse_hotkey,
)


def test_parse_ctrl_alt_up():
    assert parse_hotkey("Ctrl+Alt+Up") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x26)


def test_parse_digit_space_letter_f_key():
    assert parse_hotkey("Ctrl+Alt+1")[1] == 0x31
    assert parse_hotkey("Ctrl+Alt+Space")[1] == 0x20
    assert parse_hotkey("Ctrl+Shift+S") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("S"))
    assert parse_hotkey("Meta+F12") == (MOD_WIN | MOD_NOREPEAT, 0x7B)
    assert parse_hotkey("ctrl+alt+down")[1] == 0x28


def test_parse_invalid_raises():
    for bad in ("Ctrl+Alt+Banane", "", "Ctrl+Alt+", "Ctrl+Alt+Up+Down"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_dispatch_favorite_uses_target_room():
    cfg = default_config()
    cfg.target_room = "uuid:wohn"
    assert dispatch("favorite_2", cfg) == ("play_favorite", ("uuid:wohn", cfg.favorites[1]))


def test_dispatch_volume_play_standby():
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    assert dispatch("volume_up", cfg) == ("change_group_volume", ("uuid:bad", 5))
    assert dispatch("volume_down", cfg) == ("change_group_volume", ("uuid:bad", -5))
    assert dispatch("play_pause", cfg) == ("toggle_play", ("uuid:bad",))
    assert dispatch("standby", cfg) == ("standby_group", ("uuid:bad",))


def test_dispatch_missing_favorite_is_ignored():
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    cfg.favorites = cfg.favorites[:1]
    assert dispatch("favorite_3", cfg) is None


def test_dispatch_without_target_raises():
    with pytest.raises(NoTarget):
        dispatch("play_pause", default_config())
