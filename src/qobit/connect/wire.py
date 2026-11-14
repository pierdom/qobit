"""Qobuz Connect cloud-socket framing — pure functions, no I/O.

Every binary websocket message is one or more frames::

    [type: varint][length: varint][body: protobuf]   (repeated)

``type`` is ``qcloud.MessageType``. Connect messages travel inside a
``PAYLOAD`` frame as a ``QConnectBatch`` addressed to the backend channel.
See docs/qobuz-connect.md for the full protocol walk-through.
"""

from __future__ import annotations

import time

from .proto import qcloud_pb2 as qc
from .proto import qconnect_pb2 as qp

PROTO_QCONNECT = 1
BACKEND_CHANNEL = b"\x02"


def now_ms() -> int:
    return int(time.time() * 1000)


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
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def encode_frames(frames: list[tuple[int, bytes]]) -> bytes:
    return b"".join(_varint(kind) + _varint(len(body)) + body for kind, body in frames)


def decode_frames(buf: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    i = 0
    while i < len(buf):
        kind, i = _read_varint(buf, i)
        length, i = _read_varint(buf, i)
        if i + length > len(buf):
            raise ValueError("truncated frame")
        out.append((kind, buf[i : i + length]))
        i += length
    return out


def handshake(msg_id: int, jwt: str) -> bytes:
    """Authenticate + Subscribe, sent together right after the socket opens."""
    auth = qc.Authenticate(msg_id=msg_id, msg_date=now_ms(), jwt=jwt)
    sub = qc.Subscribe(msg_id=msg_id + 1, msg_date=now_ms(), proto=PROTO_QCONNECT)
    return encode_frames(
        [(qc.AUTHENTICATE, auth.SerializeToString()), (qc.SUBSCRIBE, sub.SerializeToString())]
    )


def payload(msg_id: int, batch_id: int, messages: list[qp.QConnectMessage]) -> bytes:
    batch = qp.QConnectBatch(messages_time=now_ms(), messages_id=batch_id, messages=messages)
    frame = qc.Payload(
        msg_id=msg_id,
        msg_date=now_ms(),
        proto=PROTO_QCONNECT,
        dests=[BACKEND_CHANNEL],
        payload=batch.SerializeToString(),
    )
    return encode_frames([(qc.PAYLOAD, frame.SerializeToString())])


def parse(buf: bytes) -> tuple[list[qp.QConnectMessage], qc.Disconnect | None]:
    """Decode one websocket message into its Connect messages, plus the
    Disconnect frame if the server sent one. Unknown frame types are skipped."""
    messages: list[qp.QConnectMessage] = []
    disconnect: qc.Disconnect | None = None
    for kind, body in decode_frames(buf):
        if kind == qc.PAYLOAD:
            p = qc.Payload.FromString(body)
            messages.extend(qp.QConnectBatch.FromString(p.payload).messages)
        elif kind == qc.DISCONNECT:
            disconnect = qc.Disconnect.FromString(body)
    return messages, disconnect
