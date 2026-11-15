"""The `i` menu's option builders: which rows appear, and their wording,
follow the favourite state of the track, album or artist."""

from __future__ import annotations

from qobit.ui.screens.context_menu import album_option, artist_option, track_options


def _ids(options):
    return [o[0] for o in options]


def test_track_options_put_favourite_after_radio():
    assert _ids(track_options(favorite=False)) == [
        "play_next",
        "add_queue",
        "radio",
        "favorite",
        "artist",
        "album",
    ]


def test_track_favourite_label_follows_state():
    labels = {o[0]: o[2] for o in track_options(favorite=True)}
    assert labels["favorite"] == "Remove from favourites"
    labels = {o[0]: o[2] for o in track_options(favorite=False)}
    assert labels["favorite"] == "Add to favourites"


def test_remove_from_queue_only_for_up_next_rows():
    assert "remove_queue" not in _ids(track_options(favorite=False))
    assert _ids(track_options(favorite=False, in_queue=True))[-1] == "remove_queue"


def test_album_and_artist_options():
    assert album_option(True) == ("favorite_album", "♥", "Remove album from favourites")
    assert album_option(False) == ("favorite_album", "♡", "Add album to favourites")
    assert artist_option(True) == ("follow_artist", "♥", "Unfollow artist")
    assert artist_option(False) == ("follow_artist", "♡", "Follow artist")
