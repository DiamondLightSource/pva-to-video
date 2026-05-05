"""Unit tests for PVStream encoding behaviour.

These tests exercise the compression logic without a live PVA connection
by calling ``_on_value`` directly with mock NTNDArray-like values.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import numpy as np

from pva_to_video.app import PVStream

_DEFAULT_DTYPE = np.dtype(np.uint8)


def _make_fake_value(
    shape: tuple[int, ...] = (64, 64, 3),
    dtype: np.dtype = _DEFAULT_DTYPE,  # type: ignore[type-arg]
) -> np.ndarray:  # type: ignore[type-arg]
    """Create a fake NTNDArray-like value (plain ndarray works for our code)."""
    return np.zeros(shape, dtype=dtype)


def _make_stream() -> PVStream:
    """A PVStream with a mocked p4p Context (no real subscription)."""
    ctx = MagicMock()
    return PVStream(pv_name="TEST:PV", ctx=ctx)


def test_no_encode_without_clients() -> None:
    """Frames arriving with no connected clients should NOT be JPEG-encoded."""
    stream = _make_stream()
    assert stream.client_count == 0

    value = _make_fake_value()
    asyncio.run(stream.on_value(value))

    # Raw value is cached...
    assert stream.latest_raw is value
    # ...but no JPEG frame was produced (no CPU spent encoding).
    assert stream.latest_frame is None


def test_encode_with_client_connected() -> None:
    """Frames arriving while a client is connected should be JPEG-encoded."""
    stream = _make_stream()
    stream.add_client()
    assert stream.client_count == 1

    value = _make_fake_value()
    asyncio.run(stream.on_value(value))

    assert stream.latest_frame is not None
    # The frame should be a valid MJPEG block starting with the boundary.
    assert stream.latest_frame.startswith(b"--frame\r\n")


def test_cached_value_encoded_on_first_client() -> None:
    """A new client should immediately get the last frame even if the camera
    stopped producing before the client connected."""
    stream = _make_stream()
    value = _make_fake_value()

    # Simulate: frame arrives with no client.
    asyncio.run(stream.on_value(value))
    assert stream.latest_frame is None

    # Client connects — should trigger encode of the cached value.
    stream.add_client()
    assert stream.latest_frame is not None
    assert stream.latest_frame.startswith(b"--frame\r\n")
