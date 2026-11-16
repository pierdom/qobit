"""qobit.store — JSON persistence for runtime state (player position, mixes
cache). conftest.py's autouse fixture redirects DATA_DIR to a tmp dir, so
nothing here touches a real ~/.local/share/qobit."""

from __future__ import annotations

from qobit import store


def test_load_missing_file_returns_empty_dict():
    assert store.load("does_not_exist.json") == {}


def test_save_then_load_round_trips():
    data = {"track": {"id": "1", "title": "x"}, "position": 12.5}
    store.save("player_state.json", data)
    assert store.load("player_state.json") == data


def test_save_creates_data_dir_if_missing():
    assert not store.DATA_DIR.exists()
    store.save("x.json", {"a": 1})
    assert store.DATA_DIR.exists()


def test_save_is_atomic_no_leftover_tmp_file():
    store.save("x.json", {"a": 1})
    assert [p.name for p in store.DATA_DIR.iterdir()] == ["x.json"]


def test_data_dir_and_files_are_owner_only():
    """DATA_DIR holds mpv's IPC socket (its `run` command executes programs)
    and the listening history: 0700, files 0600, whatever the umask."""
    import os
    import stat

    os.umask(0o022)
    store.save("x.json", {"a": 1})
    assert stat.S_IMODE(store.DATA_DIR.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.DATA_DIR / "x.json").stat().st_mode) == 0o600


def test_load_corrupt_json_returns_empty_dict_not_raise():
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (store.DATA_DIR / "broken.json").write_text("{not valid json")
    assert store.load("broken.json") == {}


def test_save_overwrites_previous_contents():
    store.save("x.json", {"a": 1})
    store.save("x.json", {"b": 2})
    assert store.load("x.json") == {"b": 2}
