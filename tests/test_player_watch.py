"""PlayerWatch: what a poll tick of mpv means, above all how a track ended."""

from __future__ import annotations

from qobit.audio.watch import PlayerWatch


class Player:
    def __init__(self):
        self.gen, self.running = 1, True
        self.props = {"time-pos": 12.5, "duration": 200.0, "pause": False, "playlist-pos": 0}

    def snapshot(self):
        return self.gen, self.running

    def get_property(self, name):
        return self.props.get(name)

    def stop(self):  # what MpvPlayer.stop() does: new generation, not running
        self.gen += 1
        self.running = False

    def exit(self):  # mpv ended by itself
        self.running = False


def test_a_running_mpv_reports_position_duration_and_pause():
    w = PlayerWatch(Player())
    r = w.read(was_playing=False)
    assert (r.state, r.pos, r.dur, r.paused, r.moved_on) == ("playing", 12.5, 200.0, False, False)


def test_a_property_mpv_didnt_answer_is_none():
    p = Player()
    p.props.pop("time-pos")
    p.props["pause"] = True
    r = PlayerWatch(p).read(True)
    assert r.pos is None and r.dur == 200.0 and r.paused is True


def test_mpv_exiting_by_itself_is_a_natural_end():
    p = Player()
    w = PlayerWatch(p)
    w.read(True)
    p.exit()
    r = w.read(True)
    assert r.state == "stopped" and r.natural_end is True


def test_a_stop_is_never_an_end():
    p = Player()
    w = PlayerWatch(p)
    w.read(True)
    p.stop()  # the user skipped, quit or started another track
    r = w.read(True)
    assert r.state == "stopped" and r.natural_end is False


def test_a_stop_after_a_restart_is_still_not_an_end():
    p = Player()
    w = PlayerWatch(p)
    w.read(True)
    p.stop()
    p.running = True  # play() started a new mpv under the new generation
    w.read(True)
    p.exit()
    assert w.read(True).natural_end is True  # that new mpv ended by itself
    p.running = True
    w.read(True)
    p.stop()
    assert w.read(True).natural_end is False


def test_idle_when_nothing_ran_and_nothing_was_playing():
    p = Player()
    p.running = False
    assert PlayerWatch(p).read(False).state == "idle"


def test_the_stopped_state_is_reported_once_then_idle():
    p = Player()
    w = PlayerWatch(p)
    w.read(True)
    p.exit()
    assert w.read(True).state == "stopped"
    assert w.read(False).state == "idle"  # the app has marked it not playing


def test_moving_on_to_the_next_entry_is_seen_once():
    p = Player()
    w = PlayerWatch(p)
    assert w.read(True).moved_on is False
    p.props["playlist-pos"] = 1
    assert w.read(True).moved_on is True
    assert w.read(True).moved_on is False


def test_mpvs_start_up_is_not_a_move_on():
    p = Player()
    p.props["playlist-pos"] = -1  # while mpv starts up
    w = PlayerWatch(p)
    assert w.read(True).moved_on is False
    p.props["playlist-pos"] = 0
    assert w.read(True).moved_on is False


def test_a_new_process_starts_its_entries_afresh():
    p = Player()
    w = PlayerWatch(p)
    p.props["playlist-pos"] = 1
    w.read(True)
    p.stop()
    w.read(True)  # stopped: the entry memory is reset
    p.running = True
    p.props["playlist-pos"] = 0
    assert w.read(True).moved_on is False
