"""Throwaway Qobuz Connect renderer spike.

Mints a qws token from qobit's saved OAuth session, joins the user's Connect
session over the cloud websocket, appears in the official apps' device picker,
logs every message, and obeys SetState commands with a *simulated* player
(no audio), or with --audio through qobit's own bit-perfect MpvPlayer.
Throwaway — see docs/qobuz-connect.md. Usage (from this dir):

    protoc --proto_path=proto --python_out=pb proto/qcloud.proto proto/qconnect.proto
    uv run --no-project --with protobuf --with websockets --with httpx \
        python connect_spike.py [--activate] [--drive] [--seconds N]

    # with real audio (needs the qobit package, so run inside the project):
    uv run --with protobuf --with websockets python connect_spike.py --audio
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import socket
import sys
import time
import uuid

import httpx
import websockets

sys.path.insert(0, str(pathlib.Path(__file__).parent / "pb"))
import qcloud_pb2 as qc  # noqa: E402
import qconnect_pb2 as qp  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

API = "https://www.qobuz.com/api.json/0.2"
CONFIG = pathlib.Path.home() / ".config/qobit/config.json"
DEVICE_UUID = uuid.uuid5(uuid.NAMESPACE_DNS, f"qobit-spike.{socket.gethostname()}")
NAME = "qobit (spike)"


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ── varint framing: [type varint][len varint][body] repeated ──────────────────
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def encode(frames: list[tuple[int, bytes]]) -> bytes:
    return b"".join(_varint(k) + _varint(len(b)) + b for k, b in frames)


def decode(buf: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i < len(buf):
        kind, i = _read_varint(buf, i)
        ln, i = _read_varint(buf, i)
        out.append((kind, buf[i : i + ln]))
        i += ln
    return out


def now_ms() -> int:
    return int(time.time() * 1000)


class Spike:
    def __init__(self, session: dict, activate: bool, drive: bool, audio: bool) -> None:
        self.app_id, self.user_token = session["app_id"], session["token"]
        self.want_active = activate
        self.drive, self.drove = drive, False
        self.next_track_id = 0
        # --audio: real playback via qobit's MpvPlayer (same bit-perfect flags
        # and configured device as the TUI). mpv is only started once the
        # renderer is told to PLAY, so a paused handover never blips audio.
        self.client = self.player = None
        self.mpv_item = -1  # queue item mpv is currently playing
        if audio:
            from qobit import config
            from qobit.audio.player import MpvPlayer
            from qobit.qobuz.client import QobuzClient

            self.client = QobuzClient()
            self.client.restore_session(self.app_id, self.user_token, session["secrets"])
            self.player = MpvPlayer(audio_device=config.get_audio_device())
        self.msg_id = 0
        self.batch_id = 0
        self.ws = None
        self.renderer_id: int | None = None
        self.queue_version = qp.QueueVersion()
        # simulated player
        self.state = qp.PLAYING_STATE_STOPPED
        self.item_id = -1
        self.next_item_id = -1
        self.track_id = 0
        self.duration_ms = 0
        self.pos_ms = 0
        self.pos_at = time.monotonic()

    # ── HTTP ──────────────────────────────────────────────────────────────────
    async def mint(self) -> tuple[str, str]:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{API}/qws/createToken",
                headers={"X-App-Id": self.app_id, "X-User-Auth-Token": self.user_token},
                data={"jwt": "jwt_qws"},
            )
            log("createToken HTTP", r.status_code)
            r.raise_for_status()
            tok = r.json()["jwt_qws"]
            return tok["endpoint"], tok["jwt"]

    async def track_info(self, track_id: int) -> tuple[str, int]:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{API}/track/get",
                params={
                    "track_id": track_id,
                    "app_id": self.app_id,
                    "user_auth_token": self.user_token,
                },
            )
            if r.status_code != 200:
                return f"track {track_id}", 0
            d = r.json()
            artist = (d.get("performer") or {}).get("name", "?")
            return f"{artist} — {d.get('title')}", int(d.get("duration", 0)) * 1000

    # ── socket ────────────────────────────────────────────────────────────────
    def _next_msg(self) -> int:
        self.msg_id += 1
        return self.msg_id

    async def send(self, *messages: qp.QConnectMessage) -> None:
        self.batch_id += 1
        batch = qp.QConnectBatch(
            messages_time=now_ms(), messages_id=self.batch_id, messages=list(messages)
        )
        payload = qc.Payload(
            msg_id=self._next_msg(),
            msg_date=now_ms(),
            proto=qc.PROTO_QCONNECT,
            dests=[bytes([2])],
            payload=batch.SerializeToString(),
        )
        for m in messages:
            log(">>", qp.MessageType.Name(m.message_type))
        await self.ws.send(encode([(qc.PAYLOAD, payload.SerializeToString())]))

    def device_info(self) -> qp.DeviceInfo:
        return qp.DeviceInfo(
            device_uuid=DEVICE_UUID.bytes,
            friendly_name=NAME,
            brand="qobit",
            model="spike",
            type=qp.DEVICE_TYPE_COMPUTER,
            software_version="qobit-spike-0",
            capabilities=qp.DeviceCapabilities(
                min_audio_quality=qp.AUDIO_QUALITY_MP3,
                max_audio_quality=qp.AUDIO_QUALITY_HIRES_LEVEL3,
                # bit-perfect: never accept remote software volume
                volume_remote_control=qp.VOLUME_REMOTE_CONTROL_NOT_ALLOWED,
            ),
        )

    def position(self) -> int:
        if self.state == qp.PLAYING_STATE_PLAYING:
            p = self.pos_ms + int((time.monotonic() - self.pos_at) * 1000)
            return min(p, self.duration_ms) if self.duration_ms else p
        return self.pos_ms

    def _set_pos(self, ms: int) -> None:
        self.pos_ms, self.pos_at = ms, time.monotonic()

    async def report(self) -> None:
        st = qp.QueueRendererState(
            playing_state=self.state,
            buffer_state=qp.BUFFER_STATE_OK,
            current_position=qp.Position(timestamp=now_ms(), value=self.position()),
            duration=self.duration_ms,
            queue_version=self.queue_version,
            current_queue_item_id=self.item_id,
            next_queue_item_id=self.next_item_id,
        )
        await self.send(
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_RNDR_SRVR_STATE_UPDATED,
                rndr_srvr_state_updated=qp.RndrSrvrStateUpdated(state=st),
            )
        )

    async def on_set_state(self, s: qp.SrvrRndrSetState) -> None:
        if s.HasField("queue_version"):
            self.queue_version.CopyFrom(s.queue_version)
        if s.HasField("next_track"):
            self.next_item_id = s.next_track.queue_item_id
            self.next_track_id = s.next_track.track_id
        seeked = False
        if s.HasField("current_track"):
            if s.current_track.queue_item_id < 0:
                log("   STOP")
                self.state, self.item_id, self.track_id = qp.PLAYING_STATE_STOPPED, -1, 0
                self._set_pos(0)
            else:
                self.item_id = s.current_track.queue_item_id
                self.track_id = s.current_track.track_id
                label, self.duration_ms = await self.track_info(self.track_id)
                log(f"   LOAD track_id={self.track_id} item={self.item_id}: {label}")
                self._set_pos(s.current_position if s.HasField("current_position") else 0)
        elif s.HasField("current_position"):
            log(f"   SEEK {s.current_position} ms")
            self._set_pos(s.current_position)
            seeked = True
        if s.HasField("playing_state"):
            self._set_pos(self.position())
            self.state = s.playing_state
            log("   STATE", qp.PlayingState.Name(s.playing_state))
        await self.sync_audio(seeked)
        await self.report()

    # ── real audio (--audio) ──────────────────────────────────────────────────
    async def stream_url(self, track_id: int) -> str | None:
        from qobit.qobuz.client import QobuzError
        from qobit.qobuz.models import StreamUrl

        for q in ("FLAC_24_192", "FLAC_24_96", "FLAC_CD"):
            try:
                st = StreamUrl.from_api(await self.client.get_streaming_url(str(track_id), q))
                log(f"   AUDIO stream {st.quality_badge}")
                return st.url
            except QobuzError:
                continue
        return None

    async def sync_audio(self, seeked: bool) -> None:
        """Make mpv match the simulated state (which stays the source of truth)."""
        p = self.player
        if p is None:
            return
        if self.item_id < 0 or self.state == qp.PLAYING_STATE_STOPPED:
            if p.running:
                p.stop()
            self.mpv_item = -1
            return
        if self.state == qp.PLAYING_STATE_PLAYING:
            if self.mpv_item != self.item_id or not p.running:
                url = await self.stream_url(self.track_id)
                if url is None:
                    log("   AUDIO no stream available")
                    return
                p.play(url, start=self.position() / 1000)
                self.mpv_item = self.item_id
            else:
                if seeked:
                    p.seek_to(self.position() / 1000)
                p._cmd(["set_property", "pause", False])
        elif p.running and self.mpv_item == self.item_id:  # paused
            if seeked:
                p.seek_to(self.position() / 1000)
            p._cmd(["set_property", "pause", True])

    async def natural_end(self) -> None:
        """mpv exited on its own: advance to next_track like a real renderer."""
        log("   AUDIO track ended")
        self.mpv_item = -1
        if self.next_item_id < 0:
            self.state = qp.PLAYING_STATE_STOPPED
            self._set_pos(0)
        else:
            self.item_id, self.track_id = self.next_item_id, self.next_track_id
            self.next_item_id, self.next_track_id = -1, 0
            label, self.duration_ms = await self.track_info(self.track_id)
            log(f"   NEXT track_id={self.track_id} item={self.item_id}: {label}")
            self._set_pos(0)
            await self.sync_audio(False)
        await self.report()

    async def handle(self, m: qp.QConnectMessage) -> None:
        name = qp.MessageType.Name(m.message_type)
        body = MessageToDict(m, preserving_proto_field_name=True)
        body.pop("message_type", None)
        log("<<", name, json.dumps(body)[:400])
        t = m.message_type
        if t == qp.MESSAGE_TYPE_SRVR_CTRL_ADD_RENDERER:
            if m.srvr_ctrl_add_renderer.device_info.device_uuid == DEVICE_UUID.bytes:
                self.renderer_id = m.srvr_ctrl_add_renderer.renderer_id
                log(f"** REGISTERED as renderer_id={self.renderer_id}")
                if self.want_active:
                    await self.send(
                        qp.QConnectMessage(
                            message_type=qp.MESSAGE_TYPE_CTRL_SRVR_SET_ACTIVE_RENDERER,
                            ctrl_srvr_set_active_renderer=qp.CtrlSrvrSetActiveRenderer(
                                renderer_id=self.renderer_id
                            ),
                        )
                    )
        elif t == qp.MESSAGE_TYPE_SRVR_CTRL_SESSION_STATE:
            ss = m.srvr_ctrl_session_state
            if ss.HasField("queue_version"):
                self.queue_version.CopyFrom(ss.queue_version)
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_STATE:
            await self.on_set_state(m.srvr_rndr_set_state)
            if self.drive and not self.drove:
                self.drove = True
                asyncio.create_task(self.drive_self())
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_ACTIVE:
            log("** ACTIVE =", m.srvr_rndr_set_active.active)
            await self.report()
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_MAX_AUDIO_QUALITY:
            q = m.srvr_rndr_set_max_audio_quality.max_audio_quality
            await self.send(
                qp.QConnectMessage(
                    message_type=qp.MESSAGE_TYPE_RNDR_SRVR_MAX_AUDIO_QUALITY_CHANGED,
                    rndr_srvr_max_audio_quality_changed=qp.RndrSrvrMaxAudioQualityChanged(
                        max_audio_quality=q
                    ),
                )
            )
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_VOLUME:
            log("   (volume ignored — bit-perfect)")

    async def control(self, **kw: object) -> None:
        log("   [ctrl] set_player_state", kw)
        await self.send(
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_SET_PLAYER_STATE,
                ctrl_srvr_set_player_state=qp.CtrlSrvrSetPlayerState(**kw),
            )
        )

    async def drive_self(self) -> None:
        """Act as our own controller: play, seek, skip, pause — 6 s apart."""
        await asyncio.sleep(3)
        await self.control(playing_state=qp.PLAYING_STATE_PLAYING)
        await asyncio.sleep(6)
        await self.control(current_position=60_000)
        await asyncio.sleep(6)
        await self.control(
            current_queue_item=qp.QueueItemRef(
                queue_version=self.queue_version, id=self.next_item_id
            )
        )
        await asyncio.sleep(6)
        await self.control(playing_state=qp.PLAYING_STATE_PAUSED)

    async def ticker(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            if self.state != qp.PLAYING_STATE_PLAYING:
                continue
            p = self.player
            if p is not None and self.mpv_item >= 0 and not p.running:
                await self.natural_end()
            elif p is not None and self.mpv_item >= 0:
                tp = p.get_property("time-pos")  # real position beats the clock
                if isinstance(tp, (int, float)):
                    self._set_pos(int(tp * 1000))
            if n % 5 == 0:
                await self.report()

    async def run(self, seconds: float) -> None:
        endpoint, jwt = await self.mint()
        log("endpoint", endpoint, "jwt len", len(jwt))
        async with websockets.connect(endpoint, ping_interval=30, max_size=None) as ws:
            self.ws = ws
            auth = qc.Authenticate(msg_id=self._next_msg(), msg_date=now_ms(), jwt=jwt)
            sub = qc.Subscribe(msg_id=self._next_msg(), msg_date=now_ms(), proto=qc.PROTO_QCONNECT)
            await ws.send(
                encode(
                    [
                        (qc.AUTHENTICATE, auth.SerializeToString()),
                        (qc.SUBSCRIBE, sub.SerializeToString()),
                    ]
                )
            )
            await self.send(
                qp.QConnectMessage(
                    message_type=qp.MESSAGE_TYPE_CTRL_SRVR_JOIN_SESSION,
                    ctrl_srvr_join_session=qp.CtrlSrvrJoinSession(device_info=self.device_info()),
                )
            )
            tick = asyncio.create_task(self.ticker())
            deadline = time.monotonic() + seconds
            try:
                while (left := deadline - time.monotonic()) > 0:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=left)
                    except TimeoutError:
                        break
                    if isinstance(raw, str):
                        log("<< text", raw[:200])
                        continue
                    for kind, body in decode(raw):
                        if kind == qc.PAYLOAD:
                            p = qc.Payload.FromString(body)
                            batch = qp.QConnectBatch.FromString(p.payload)
                            for m in batch.messages:
                                await self.handle(m)
                        elif kind == qc.DISCONNECT:
                            d = qc.Disconnect.FromString(body)
                            log("<< DISCONNECT reconnect=", d.reconnect)
                            return
                        else:
                            log(f"<< frame type={kind} len={len(body)} {body[:80]!r}")
            finally:
                tick.cancel()
                if self.player is not None:
                    self.player.stop()
        log("done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activate", action="store_true", help="make this device active on join")
    ap.add_argument(
        "--drive",
        action="store_true",
        help="also act as controller: play/seek/skip/pause ourselves",
    )
    ap.add_argument(
        "--audio",
        action="store_true",
        help="real playback via qobit's MpvPlayer (run inside the project)",
    )
    ap.add_argument("--seconds", type=float, default=600)
    a = ap.parse_args()
    s = json.loads(CONFIG.read_text())["oauth_session"]
    try:
        asyncio.run(Spike(s, a.activate, a.drive, a.audio).run(a.seconds))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
