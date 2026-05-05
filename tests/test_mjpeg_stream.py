"""System test — connects to a running pva-to-video instance and the PVA
simulation, fetches MJPEG frames over HTTP, and validates image dimensions.

Run against a live instance::

    pytest tests/test_mjpeg_stream.py --pva-to-video-url http://localhost:8080

The test is skipped if no ``--pva-to-video-url`` option is supplied.
"""

from __future__ import annotations

import io

import httpx
from PIL import Image

# Default PV served by the simulation IOC (see check_pva.py).
TEST_PV = "BL01T-DI-CAM-01:PVA:OUTPUT"


def _parse_mjpeg_frames(data: bytes) -> list[bytes]:
    """Extract JPEG payloads from multipart/x-mixed-replace data."""
    frames: list[bytes] = []
    # Split on the boundary marker.
    parts = data.split(b"--frame\r\n")
    for part in parts:
        # Find the JPEG SOI marker (0xFF 0xD8).
        soi = part.find(b"\xff\xd8")
        if soi == -1:
            continue
        # JPEG ends at EOI marker (0xFF 0xD9).
        eoi = part.find(b"\xff\xd9", soi)
        if eoi == -1:
            continue
        frames.append(part[soi : eoi + 2])
    return frames


def test_mjpeg_stream_returns_valid_frames(base_url: str) -> None:
    """Connect to the MJPEG endpoint, collect ≥3 frames, and verify size."""
    url = f"{base_url}/mjpg/{TEST_PV}"

    collected = bytearray()
    target_frames = 3

    with httpx.Client(timeout=30.0) as client:
        with client.stream("GET", url) as response:
            assert response.status_code == 200
            content_type = response.headers.get("content-type", "")
            assert "multipart/x-mixed-replace" in content_type

            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                collected.extend(chunk)
                frames = _parse_mjpeg_frames(bytes(collected))
                if len(frames) >= target_frames:
                    break

    frames = _parse_mjpeg_frames(bytes(collected))
    assert len(frames) >= target_frames, (
        f"Expected ≥{target_frames} frames, got {len(frames)}"
    )

    for i, jpeg_bytes in enumerate(frames[:target_frames]):
        img = Image.open(io.BytesIO(jpeg_bytes))
        assert img.size == (1024, 1024), (
            f"Frame {i}: expected 1024×1024, got {img.size}"
        )


def test_viewer_page_loads(base_url: str) -> None:
    """The viewer HTML page should be served at ``/``."""
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(base_url)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "/mjpg/" in resp.text
