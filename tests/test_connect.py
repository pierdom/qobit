"""Qobuz Connect: framing, the server-queue mirror, and the renderer state
machine against a fake player.

The renderer scenarios replay the message sequences captured live from the
iOS app (docs/qobuz-connect.md, "Verified end-to-end" table) — notably that
picking a song is *only* QUEUE_TRACKS_LOADED, and that queue item ids restart
at 0 on every new queue.
"""

from __future__ import annotations

import asyncio

import pytest

from qobit.connect import wire
from qobit.connect.proto import qcloud_pb2 as qc
from qobit.connect.proto import qconnect_pb2 as qp
from qobit.connect.queue import LOOP_ALL, LOOP_ONE, QueueItem, ServerQueue
from qobit.connect.renderer import PAUSED, PLAYING, STOPPED, ConnectRenderer

# ── wire ─────────────────────────────────────────────────────────────────────


def test_frames_round_trip_including_multibyte_varints():
    frames = [(qc.PAYLOAD, b"x" * 300), (qc.DISCONNECT, b"")]
    assert wire.decode_frames(wire.encode_frames(frames)) == frames


def test_truncated_frame_raises():
    data = wire.encode_frames([(qc.PAYLOAD, b"abcdef")])
    with pytest.raises(ValueError):
        wire.decode_frames(data[:-2])


def test_payload_round_trips_connect_messages():
    m = qp.QConnectMessage(
        message_type=qp.MESSAGE_TYPE_SRVR_RNDR_SET_ACTIVE,
        srvr_rndr_set_active=qp.SrvrRndrSetActive(active=True),
    )
    messages, disconnect = wire.parse(wire.payload(1, 1, [m, m]))
    assert [x.message_type for x in messages] == [qp.MESSAGE_TYPE_SRVR_RNDR_SET_ACTIVE] * 2
    assert disconnect is None


def test_parse_surfaces_disconnect():
    d = qc.Disconnect(msg_id=1, msg_date=0, reconnect=False)
    _, disconnect = wire.parse(wire.encode_frames([(qc.DISCONNECT, d.SerializeToString())]))
    assert disconnect is not None and disconnect.reconnect is False


# ── ServerQueue ──────────────────────────────────────────────────────────────


def _loaded(track_ids: list[int], pos: int = 0, major: int = 2) -> qp.SrvrCtrlQueueTracksLoaded:
    return qp.SrvrCtrlQueueTracksLoaded(
        queue_version=qp.QueueVersion(major=major, minor=1),
        tracks=[qp.QueueTrack(queue_item_id=i, track_id=t) for i, t in enumerate(track_ids)],
        queue_position=pos,
    )


def _state(track_ids: list[int], **kw) -> qp.SrvrCtrlQueueState:
    return qp.SrvrCtrlQueueState(
        queue_version=qp.QueueVersion(major=kw.pop("major", 2), minor=1),
        tracks=[qp.QueueTrackRef(queue_item_id=i, track_id=t) for i, t in enumerate(track_ids)],
        **kw,
    )


def test_load_replaces_queue_and_returns_start_item():
    q = ServerQueue()
    start = q.load(_loaded([10, 11, 12], pos=1))
    assert start == QueueItem(1, 11)
    assert [it.track_id for it in q.play_order()] == [10, 11, 12]
    assert q.version.major == 2


def test_load_out_of_range_position_returns_none():
    assert ServerQueue().load(_loaded([10], pos=5)) is None


def test_version_never_goes_backwards():
    q = ServerQueue()
    q.note_version(qp.QueueVersion(major=5, minor=1))
    q.note_version(qp.QueueVersion(major=4, minor=9))
    assert (q.version.major, q.version.minor) == (5, 1)


def test_state_snapshot_uses_shuffle_order_and_appends_autoplay():
    q = ServerQueue()
    q.set_state(
        _state(
            [10, 11, 12],
            shuffle_mode=True,
            shuffled_track_indexes=[2, 0, 1],
            autoplay_tracks=[qp.QueueTrackRef(queue_item_id=50, track_id=99)],
        )
    )
    order = q.play_order()
    assert [it.track_id for it in order] == [12, 10, 11, 99]
    assert order[-1].autoplay is True


def test_invalid_shuffle_order_falls_back_to_queue_order():
    q = ServerQueue()
    q.set_state(_state([10, 11, 12], shuffle_mode=True, shuffled_track_indexes=[0, 0, 1]))
    assert [it.track_id for it in q.play_order()] == [10, 11, 12]


def test_next_after_respects_loop_modes():
    q = ServerQueue()
    q.load(_loaded([10, 11]))
    assert q.next_after(0) == QueueItem(1, 11)
    assert q.next_after(1) is None
    assert q.next_after(1, LOOP_ALL) == QueueItem(0, 10)
    assert q.next_after(1, LOOP_ONE) == QueueItem(1, 11)
    assert q.previous_before(1) == QueueItem(0, 10)
    assert q.previous_before(0) is None


# ── ConnectRenderer ──────────────────────────────────────────────────────────


class FakePort:
    """Stands in for the TUI player. Tracks in ``unplayable`` fail to load."""

    def __init__(self, unplayable: set[int] | None = None) -> None:
        self.running = False
        self.paused = False
        self.pos = 0.0
        self.loaded: list[tuple[int, float, bool]] = []
        self.unplayable = unplayable or set()
        self.active_calls: list[bool] = []
        self.mirror_cursor_item = None

    async def load_track(self, track_id, start_s, play):
        self.loaded.append((track_id, start_s, play))
        if track_id in self.unplayable:
            return False
        self.running, self.paused, self.pos = play, False, start_s
        return True

    def set_paused(self, paused):
        self.paused = paused

    def seek(self, pos_s):
        self.pos = pos_s

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running

    def position_s(self):
        return self.pos

    def duration_s(self):
        return 200.0

    async def queue_changed(self, queue, current_item_id):
        self.mirror_cursor_item = current_item_id

    def active_changed(self, active):
        self.active_calls.append(active)


def _msg(**kw) -> qp.QConnectMessage:
    ((field, value),) = kw.items()
    return qp.QConnectMessage(
        message_type=getattr(qp, f"MESSAGE_TYPE_{field.upper()}"), **{field: value}
    )


def _set_state(**kw) -> qp.QConnectMessage:
    return _msg(srvr_rndr_set_state=qp.SrvrRndrSetState(**kw))


def _ref(item: int, track: int) -> qp.QueueTrackRef:
    return qp.QueueTrackRef(queue_item_id=item, track_id=track)


def _make(unplayable=None) -> tuple[ConnectRenderer, FakePort, list]:
    sent: list[qp.QConnectMessage] = []
    port = FakePort(unplayable)
    r = ConnectRenderer(port, lambda *ms: sent.extend(ms))
    return r, port, sent


def _activate(r: ConnectRenderer) -> None:
    asyncio.run(r.handle(_msg(srvr_rndr_set_active=qp.SrvrRndrSetActive(active=True))))


def _reports(sent) -> list[qp.QueueRendererState]:
    return [
        m.rndr_srvr_state_updated.state
        for m in sent
        if m.message_type == qp.MESSAGE_TYPE_RNDR_SRVR_STATE_UPDATED
    ]


def test_activation_asks_for_queue_state_and_reports():
    r, port, sent = _make()
    _activate(r)
    kinds = [m.message_type for m in sent]
    assert qp.MESSAGE_TYPE_CTRL_SRVR_ASK_FOR_QUEUE_STATE in kinds
    assert qp.MESSAGE_TYPE_RNDR_SRVR_STATE_UPDATED in kinds
    assert port.active_calls == [True]


def test_paused_handover_prepares_without_starting_audio():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PAUSED, current_track=_ref(0, 111))))
    assert port.loaded == [(111, 0.0, False)]
    assert not port.running and r.state == PAUSED


def test_play_after_paused_handover_starts_audio_at_stored_position():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(
        r.handle(
            _set_state(playing_state=PAUSED, current_track=_ref(0, 111), current_position=5000)
        )
    )
    asyncio.run(r.handle(_set_state(playing_state=PLAYING)))
    assert port.loaded[-1] == (111, 5.0, True)
    assert port.running and r.state == PLAYING


def test_picking_a_song_is_only_queue_loaded_and_renderer_starts_it():
    # Verified against the iOS app: no SET_STATE follows the load.
    r, port, sent = _make()
    _activate(r)
    loaded = _loaded([500, 501, 502], pos=2, major=3)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=loaded)))
    assert port.loaded[-1] == (502, 0.0, True)
    assert r.item_id == 2 and r.state == PLAYING
    last = _reports(sent)[-1]
    assert last.current_queue_item_id == 2 and last.queue_version.major == 3


def test_new_queue_at_item_zero_reloads_even_though_item_id_is_unchanged():
    # Item ids restart at 0 per queue: the old "same id → no reload" bug.
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 222))))
    assert [t for t, _, _ in port.loaded] == [111, 222]


def test_bare_playing_state_toggles_pause_on_running_player():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    asyncio.run(r.handle(_set_state(playing_state=PAUSED)))
    assert port.paused and r.state == PAUSED
    asyncio.run(r.handle(_set_state(playing_state=PLAYING)))
    assert not port.paused and len(port.loaded) == 1  # resumed, not reloaded


def test_bare_position_is_a_seek():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    asyncio.run(r.handle(_set_state(current_position=60000)))
    assert port.pos == 60.0 and len(port.loaded) == 1


def test_negative_item_is_stop():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    asyncio.run(r.handle(_set_state(current_track=_ref(-1, 0))))
    assert not port.running and r.state == STOPPED and r.item_id == -1


def test_natural_end_advances_through_the_mirror_then_stops():
    r, port, sent = _make()
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([500, 501]))))
    asyncio.run(r.track_ended())
    assert port.loaded[-1] == (501, 0.0, True) and r.item_id == 1
    asyncio.run(r.track_ended())
    assert r.state == STOPPED
    assert _reports(sent)[-1].playing_state == STOPPED


def test_unplayable_track_is_skipped_forward():
    r, port, _ = _make(unplayable={500})
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([500, 501]))))
    assert [t for t, _, _ in port.loaded] == [500, 501]
    assert r.item_id == 1 and r.state == PLAYING


def test_structural_queue_events_trigger_a_snapshot_request():
    r, _, sent = _make()
    sent.clear()
    added = qp.SrvrCtrlQueueTracksAdded(queue_version=qp.QueueVersion(major=4, minor=2))
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_added=added)))
    assert [m.message_type for m in sent] == [qp.MESSAGE_TYPE_CTRL_SRVR_ASK_FOR_QUEUE_STATE]


def test_volume_commands_are_ignored_for_bit_perfect():
    r, port, sent = _make()
    _activate(r)
    sent.clear()
    asyncio.run(r.handle(_msg(srvr_rndr_set_volume=qp.SrvrRndrSetVolume(volume=10))))
    assert sent == []


def test_losing_active_stops_audio_and_stops_reporting():
    r, port, sent = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    sent.clear()
    asyncio.run(r.handle(_msg(srvr_rndr_set_active=qp.SrvrRndrSetActive(active=False))))
    assert not port.running and port.active_calls == [True, False]
    r.report()
    assert _reports(sent) == []


def test_local_toggle_pause_and_seek_report_to_the_server():
    r, port, sent = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    sent.clear()
    asyncio.run(r.toggle_pause())
    asyncio.run(r.seek(42.0))
    reps = _reports(sent)
    assert reps[0].playing_state == PAUSED
    assert reps[-1].current_position.value == 42000


def test_seek_command_reports_the_target_not_the_stale_polled_position():
    # The player's position is polled; right after a seek it still reads the
    # old value. Live run showed the report lagging until the next tick.
    r, port, sent = _make()
    _activate(r)
    asyncio.run(r.handle(_set_state(playing_state=PLAYING, current_track=_ref(0, 111))))
    port.seek = lambda pos_s: None  # player hasn't caught up yet
    port.pos = 4.9
    sent.clear()
    asyncio.run(r.handle(_set_state(current_position=60000)))
    assert _reports(sent)[-1].current_position.value == 60000


def test_delayed_activation_handover_does_not_reload_the_item_already_playing():
    # Live trace (qobuz-mcp controller): SET_ACTIVE, the controller's queue load
    # starts item 0, *then* the server's handover SET_STATE{PAUSED, item 0, same
    # queue version} arrives. It must not restart the track — only apply state.
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([500, 501], major=30))))
    assert port.loaded == [(500, 0.0, True)]
    asyncio.run(
        r.handle(
            _set_state(
                playing_state=PAUSED,
                queue_version=qp.QueueVersion(major=30, minor=1),
                current_track=_ref(0, 500),
            )
        )
    )
    assert port.loaded == [(500, 0.0, True)]  # no reload
    assert port.running and port.paused and r.state == PAUSED


def test_same_item_id_in_a_newer_queue_still_reloads():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([500], major=30))))
    asyncio.run(
        r.handle(
            _set_state(
                playing_state=PLAYING,
                queue_version=qp.QueueVersion(major=31, minor=1),
                current_track=_ref(0, 500),
            )
        )
    )
    assert [t for t, _, _ in port.loaded] == [500, 500]


def test_same_item_with_position_is_a_seek_not_a_reload():
    r, port, _ = _make()
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([500], major=30))))
    asyncio.run(
        r.handle(
            _set_state(
                queue_version=qp.QueueVersion(major=30, minor=1),
                current_track=_ref(0, 500),
                current_position=0,
            )
        )
    )
    assert len(port.loaded) == 1 and port.pos == 0.0


def test_report_quality_sends_file_and_device_format():
    # Without these the iOS app shows "unknown quality".
    r, _, sent = _make()
    _activate(r)
    sent.clear()
    r.report_quality(format_id=27, sampling_rate_hz=192000, bit_depth=24)
    file_q = sent[0].rndr_srvr_file_audio_quality_changed
    dev_q = sent[1].rndr_srvr_device_audio_quality_changed
    assert sent[0].message_type == qp.MESSAGE_TYPE_RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED
    assert (file_q.sampling_rate, file_q.bit_depth, file_q.nb_channels) == (192000, 24, 2)
    assert file_q.audio_quality == qp.AUDIO_QUALITY_HIRES_LEVEL2
    assert (dev_q.sampling_rate, dev_q.bit_depth) == (192000, 24)  # bit-perfect: same


def test_report_quality_maps_cd_and_is_silent_when_inactive():
    r, _, sent = _make()
    r.report_quality(format_id=6, sampling_rate_hz=44100, bit_depth=16)
    assert sent == []  # not the active renderer
    _activate(r)
    sent.clear()
    r.report_quality(format_id=6, sampling_rate_hz=44100, bit_depth=16)
    assert sent[0].rndr_srvr_file_audio_quality_changed.audio_quality == qp.AUDIO_QUALITY_CD


# ── phase 2: local queue edits → serialized server commands ─────────────────


def _cmds(sent) -> list[str]:
    return [
        qp.MessageType.Name(m.message_type).removeprefix("MESSAGE_TYPE_CTRL_SRVR_")
        for m in sent
        if qp.MessageType.Name(m.message_type).startswith("MESSAGE_TYPE_CTRL_SRVR_QUEUE")
    ]


def _echo(kind: str, action: bytes, **kw) -> qp.QConnectMessage:
    field = f"srvr_ctrl_{kind}"
    cls = {
        "queue_tracks_added": qp.SrvrCtrlQueueTracksAdded,
        "queue_tracks_inserted": qp.SrvrCtrlQueueTracksInserted,
        "queue_tracks_removed": qp.SrvrCtrlQueueTracksRemoved,
        "queue_error_message": qp.SrvrCtrlQueueErrorMessage,
    }[kind]
    return _msg(**{field: cls(action_uuid=action, **kw)})


_CMD_FIELDS = (
    "ctrl_srvr_queue_add_tracks",
    "ctrl_srvr_queue_insert_tracks",
    "ctrl_srvr_queue_remove_tracks",
    "ctrl_srvr_queue_load_tracks",
)


def _last_cmd(sent):
    """The last queue command sent (echoes also trigger state requests, so
    sent[-1] is not necessarily a command)."""
    for m in reversed(sent):
        for f in _CMD_FIELDS:
            if m.HasField(f):
                return getattr(m, f)
    raise AssertionError("no queue command sent")


def _last_action(sent) -> bytes:
    for m in reversed(sent):
        for f in (
            "ctrl_srvr_queue_add_tracks",
            "ctrl_srvr_queue_insert_tracks",
            "ctrl_srvr_queue_remove_tracks",
            "ctrl_srvr_queue_load_tracks",
        ):
            if m.HasField(f):
                return bytes(getattr(m, f).action_uuid)
    raise AssertionError("no queue command sent")


class QueueErrPort(FakePort):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def queue_error(self, message):
        self.errors.append(message)


def test_queue_commands_go_out_one_at_a_time_released_by_their_echo():
    r, _, sent = _make()
    _activate(r)
    sent.clear()
    r.queue_add([1])
    r.queue_add([2])
    assert _cmds(sent) == ["QUEUE_ADD_TRACKS"]  # second waits for the first's echo
    # Another controller's echo must not release the line.
    asyncio.run(r.handle(_echo("queue_tracks_added", b"someone-else")))
    assert _cmds(sent) == ["QUEUE_ADD_TRACKS"]
    asyncio.run(r.handle(_echo("queue_tracks_added", _last_action(sent))))
    assert _cmds(sent) == ["QUEUE_ADD_TRACKS", "QUEUE_ADD_TRACKS"]
    assert list(_last_cmd(sent).track_ids) == [2]


def test_commands_are_stamped_with_the_version_current_when_sent():
    r, _, sent = _make()
    _activate(r)
    r.queue_add([1])
    r.queue_add([2])
    # The first echo arrives with a bumped version; the second command must use it.
    asyncio.run(
        r.handle(
            _echo(
                "queue_tracks_added",
                _last_action(sent),
                queue_version=qp.QueueVersion(major=7, minor=3),
            )
        )
    )
    ref = _last_cmd(sent).queue_version_ref
    assert (ref.major, ref.minor) == (7, 3)


def test_queue_error_drops_pending_and_resyncs():
    sent: list[qp.QConnectMessage] = []
    port = QueueErrPort()
    r = ConnectRenderer(port, lambda *ms: sent.extend(ms))
    _activate(r)
    r.queue_add([1])
    r.queue_add([2])
    in_flight = r._in_flight[0]
    sent.clear()
    err = qp.Error(code="409", message="version mismatch")
    asyncio.run(r.handle(_echo("queue_error_message", in_flight, error=err)))
    assert port.errors == ["version mismatch"]
    assert _cmds(sent) == []  # the pending ADD of [2] was dropped
    assert any(m.message_type == qp.MESSAGE_TYPE_CTRL_SRVR_ASK_FOR_QUEUE_STATE for m in sent)


def test_insert_after_current_item_and_at_front():
    r, _, sent = _make()
    _activate(r)
    r.queue_insert([9], after_item_id=3)
    cmd = _last_cmd(sent)
    assert cmd.HasField("insert_after") and cmd.insert_after == 3
    asyncio.run(r.handle(_echo("queue_tracks_inserted", _last_action(sent))))
    r.queue_insert([9], after_item_id=None)
    assert not _last_cmd(sent).HasField("insert_after")


def test_own_load_echo_starts_playback_like_an_app_pick():
    r, port, sent = _make()
    _activate(r)
    r.queue_load([700, 701])
    action = _last_action(sent)
    loaded = _loaded([700, 701], major=9)
    loaded.action_uuid = action
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=loaded)))
    assert port.loaded[-1] == (700, 0.0, True) and r.state == PLAYING
    assert r._in_flight is None  # the echo released the line


def test_radio_replace_targets_the_queue_current_at_send_time():
    # Menu "Play radio" = load + replace, back to back. The replace must remove
    # the *new* queue's upcoming items, which only exist after the load echo.
    r, port, sent = _make()
    _activate(r)
    # Old queue: 5 items (upcoming 1..4). New: 3 items (upcoming 1, 2). Different
    # on purpose — equal lengths would hide a remove computed on the old queue.
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([1, 2, 3, 4, 5], major=5))))
    sent.clear()
    r.queue_load([800, 801, 802])  # new selection: item 0 = 800, upcoming 1, 2
    r.queue_replace_upcoming([900, 901])
    assert _cmds(sent) == ["QUEUE_LOAD_TRACKS"]
    loaded = _loaded([800, 801, 802], major=6)
    loaded.action_uuid = _last_action(sent)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=loaded)))
    assert _cmds(sent) == ["QUEUE_LOAD_TRACKS", "QUEUE_REMOVE_TRACKS"]
    assert list(_last_cmd(sent).queue_item_ids) == [1, 2]
    assert _last_cmd(sent).queue_version_ref.major == 6
    asyncio.run(r.handle(_echo("queue_tracks_removed", _last_action(sent))))
    assert _cmds(sent)[-1] == "QUEUE_ADD_TRACKS"
    assert list(_last_cmd(sent).track_ids) == [900, 901]


def test_radio_replace_with_nothing_upcoming_skips_the_remove():
    r, _, sent = _make()
    _activate(r)
    asyncio.run(r.handle(_msg(srvr_ctrl_queue_tracks_loaded=_loaded([1], major=5))))
    sent.clear()
    r.queue_replace_upcoming([900])
    assert _cmds(sent) == ["QUEUE_ADD_TRACKS"]
