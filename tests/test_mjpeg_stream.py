"""System test — starts the example-services IOC and pva-to-video, fetches
MJPEG frames over HTTP, and validates image dimensions.

Requires ``EXAMPLE_SERVICES_PATH`` to point to a checked-out copy of the
``example-services`` submodule (set automatically in the devcontainer and in
CI).  The tests are skipped if the variable is absent.

To run manually inside the devcontainer::

    pytest tests/test_mjpeg_stream.py
"""

from __future__ import annotations

import io
import os
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from PIL import Image

# PV served by the bl01t-di-cam-01 simulation IOC.
TEST_PV = "BL01T-DI-CAM-01:PVA:OUTPUT"


def _parse_mjpeg_frames(data: bytes) -> list[bytes]:
    """Extract JPEG payloads from multipart/x-mixed-replace data."""
    frames: list[bytes] = []
    parts = data.split(b"--frame\r\n")
    for part in parts:
        soi = part.find(b"\xff\xd8")
        if soi == -1:
            continue
        eoi = part.find(b"\xff\xd9", soi)
        if eoi == -1:
            continue
        frames.append(part[soi : eoi + 2])
    return frames


def _pva_name_servers(example_services_path: str) -> str:
    """Return ``EPICS_PVA_NAME_SERVERS`` value matching the example-services .env."""
    env_file = Path(example_services_path) / ".env"
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("EPICS_PVA_SERVER_PORT="):
            port = line.split("=", 1)[1].strip()
            return f"127.0.0.1:{port}"
    return "127.0.0.1:5075"  # p4p default


@pytest.fixture(scope="module")
def pvagw(docker_composer):  # type: ignore[return]
    """Start the pvagw service (runs init first via depends_on) so PVA traffic
    from the channel_access Docker network is accessible on 127.0.0.1."""
    example_services_path = os.environ.get("EXAMPLE_SERVICES_PATH")
    if example_services_path is None:
        yield
        return
    yield from docker_composer(
        docker_args=["-f", f"{example_services_path}/compose.yaml"],
        docker_services="pvagw",
        ready_log_line="Setup GW clients to ignore GW servers",
        start_timeout=60.0,
        wait_time=3.0,
    )


@pytest.fixture(scope="module")
def ioc(pvagw, docker_composer):  # type: ignore[return]
    """Start the bl01t-di-cam-01 simulation IOC from the example-services submodule."""
    example_services_path = os.environ.get("EXAMPLE_SERVICES_PATH")
    if example_services_path is None:
        yield
        return
    yield from docker_composer(
        docker_args=["-f", f"{example_services_path}/compose.yaml"],
        docker_services="bl01t-di-cam-01",
        ready_log_line="iocRun: All initialization complete",
        start_timeout=120.0,
    )


@pytest.fixture(scope="module")
def base_url(ioc: None) -> Generator[str, None, None]:
    """Start pva-to-video on port 18080 and yield its base URL.

    Skips the entire module when ``EXAMPLE_SERVICES_PATH`` is not set.
    """
    example_services_path = os.environ.get("EXAMPLE_SERVICES_PATH")
    if example_services_path is None:
        pytest.skip(
            "Set EXAMPLE_SERVICES_PATH (e.g. to ./example-services after running "
            "'git submodule update --init') to run system tests."
        )

    port = 18080
    env = {
        **os.environ,
        "EPICS_PVA_NAME_SERVERS": _pva_name_servers(example_services_path),
    }

    process = subprocess.Popen(
        ["pva-to-video", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        env=env,
    )

    start_time = time.time()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            if "Application startup complete" in line:
                break
            if time.time() - start_time > 30.0:
                process.terminate()
                raise TimeoutError("pva-to-video did not start within 30 s")
    except Exception:
        process.terminate()
        raise

    yield f"http://localhost:{port}"

    process.terminate()
    try:
        process.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        process.kill()
    if process.stdout:
        process.stdout.close()


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
