"""Qobuz Connect — qobit as a renderer in the official apps' device picker.

UI-agnostic: ``session`` (cloud socket), ``queue`` (server-queue mirror),
``renderer`` (playback state machine over a ``PlayerPort``), ``wire``
(framing). The TUI adapter lives in ``ui/play_context.py``. Protocol notes:
docs/qobuz-connect.md; design: docs/qobuz-connect-design.md.
"""

from __future__ import annotations

import socket
import uuid

from .. import __version__
from .proto import qconnect_pb2 as qp


def device_info() -> qp.DeviceInfo:
    """How qobit appears in the apps. The uuid is stable per host so the apps
    remember the device across restarts; volume is advertised as not remotely
    controllable — software gain would break bit-perfect output."""
    host = socket.gethostname().split(".")[0]
    return qp.DeviceInfo(
        device_uuid=uuid.uuid5(uuid.NAMESPACE_DNS, f"qobit.{host}").bytes,
        friendly_name=f"qobit ({host})",
        brand="qobit",
        model="qobit",
        type=qp.DEVICE_TYPE_COMPUTER,
        software_version=f"qobit-{__version__}",
        capabilities=qp.DeviceCapabilities(
            min_audio_quality=qp.AUDIO_QUALITY_MP3,
            max_audio_quality=qp.AUDIO_QUALITY_HIRES_LEVEL3,
            volume_remote_control=qp.VOLUME_REMOTE_CONTROL_NOT_ALLOWED,
        ),
    )
