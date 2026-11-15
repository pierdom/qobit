"""ConnectSession — one device's membership in the user's Qobuz Connect session.

Owns the cloud websocket: mints a token per connection (the cloud serves one
socket per token, so reusing one makes sockets evict each other), sends the
handshake, joins as a device, recognises its own registration, keeps the
socket alive and reconnects with the web player's backoff schedule. Every
Connect message is handed to ``on_message``; this class has no player logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from . import wire
from .proto import qconnect_pb2 as qp

log = logging.getLogger(__name__)

# Web player's reconnect schedule (seconds), capped at the last value.
_BACKOFF = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20]
# Only a connection that stayed up this long resets the backoff.
_STABLE_S = 40.0
# If the server drops our registration while the socket stays up, rejoin — but
# at most this often, so two clients fighting over one device id can't flap.
_REJOIN_MIN_S = 10.0

Mint = Callable[[], Awaitable[tuple[str, str]]]  # -> (endpoint, jwt)
OnMessage = Callable[[qp.QConnectMessage], Awaitable[None]]
OnStatus = Callable[[str], None]  # "connecting" | "ready" | "offline"


class ConnectSession:
    def __init__(
        self, mint: Mint, device: qp.DeviceInfo, on_message: OnMessage, on_status: OnStatus
    ) -> None:
        self._mint = mint
        self.device = device
        self._on_message = on_message
        self._on_status = on_status
        self._outbox: asyncio.Queue[list[qp.QConnectMessage]] = asyncio.Queue()
        self._msg_id = 0
        self._batch_id = 0
        self._stopped = False
        self.renderer_id: int | None = None
        self._last_rejoin = 0.0

    # ── public ────────────────────────────────────────────────────────────────

    def send(self, *messages: qp.QConnectMessage) -> None:
        """Queue messages for the socket. Never blocks; dropped if offline
        (the session rejoins and re-reports on reconnect)."""
        self._outbox.put_nowait(list(messages))

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        """Connect/serve/reconnect until ``stop()`` (or the server refuses)."""
        attempt = 0
        while not self._stopped:
            self._on_status("connecting")
            started = time.monotonic()
            try:
                refused = await self._serve_once()
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException, ValueError) as e:
                log.info("connect: socket lost: %s", e)
                refused = False
            except Exception as e:  # token mint failure etc. — retry, don't die
                log.warning("connect: session error: %s", e)
                refused = False
            self.renderer_id = None
            self._on_status("offline")
            if refused or self._stopped:
                return
            if time.monotonic() - started >= _STABLE_S:
                attempt = 0
            await asyncio.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
            attempt += 1

    # ── internals ─────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _serve_once(self) -> bool:
        """One socket lifetime. Returns True when the server refused us
        (DISCONNECT without reconnect) — no point retrying."""
        endpoint, jwt = await self._mint()
        # ws pings every 30 s; a missing pong within 30 s closes the socket,
        # which is our "a minute of silence = dead" rule.
        async with connect(endpoint, ping_interval=30, ping_timeout=30, max_size=None) as ws:
            self._msg_id += 1
            await ws.send(wire.handshake(self._msg_id, jwt))
            self._msg_id += 1
            # Drop anything queued while offline: it was stamped with stale
            # queue versions; the renderer re-reports once registered.
            while not self._outbox.empty():
                self._outbox.get_nowait()
            self.send(self._join_message())
            writer = asyncio.create_task(self._writer(ws))
            try:
                async for raw in ws:
                    if isinstance(raw, str):
                        continue
                    messages, disconnect = wire.parse(raw)
                    for m in messages:
                        self._note_registration(m)
                        await self._on_message(m)
                    if disconnect is not None:
                        return not disconnect.reconnect
            finally:
                writer.cancel()
        return False

    async def _writer(self, ws) -> None:
        while True:
            batch = await self._outbox.get()
            self._batch_id += 1
            await ws.send(wire.payload(self._next_id(), self._batch_id, batch))

    def _join_message(self) -> qp.QConnectMessage:
        return qp.QConnectMessage(
            message_type=qp.MESSAGE_TYPE_CTRL_SRVR_JOIN_SESSION,
            ctrl_srvr_join_session=qp.CtrlSrvrJoinSession(device_info=self.device),
        )

    def _note_registration(self, m: qp.QConnectMessage) -> None:
        if m.message_type == qp.MESSAGE_TYPE_SRVR_CTRL_ADD_RENDERER:
            added = m.srvr_ctrl_add_renderer
            if added.device_info.device_uuid == self.device.device_uuid:
                self.renderer_id = added.renderer_id
                self._on_status("ready")
        elif m.message_type == qp.MESSAGE_TYPE_SRVR_CTRL_REMOVE_RENDERER:
            removed = m.srvr_ctrl_remove_renderer.renderer_id
            if self.renderer_id is not None and removed == self.renderer_id:
                # Our registration is gone but the socket is fine — e.g. another
                # client joined with this device id and then left (verified:
                # the server then drops the device, and nothing else tells us).
                # Without this the device silently vanishes from every app.
                self.renderer_id = None
                self._on_status("connecting")
                now = time.monotonic()
                if now - self._last_rejoin >= _REJOIN_MIN_S:
                    self._last_rejoin = now
                    log.info("connect: registration removed by the server, rejoining")
                    self.send(self._join_message())
