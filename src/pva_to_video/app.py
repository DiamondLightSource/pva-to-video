"""MJPEG streaming backend for areaDetector NTNDArray PVs.

Serves ``GET /mjpg/{pv_name}`` as a ``multipart/x-mixed-replace`` MJPEG
stream.  A p4p subscription is started on the first client connect and
torn down after :data:`IDLE_TEARDOWN_DELAY` seconds with no clients.

A minimal viewer page is served at ``GET /`` for manual validation.
"""

from __future__ import annotations

import asyncio
import html
import io
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from p4p.client.asyncio import Context
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MJPEG_BOUNDARY = b"frame"
MJPEG_CONTENT_TYPE = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY.decode()}"

IDLE_TEARDOWN_DELAY = 60.0
MAX_CLIENT_FPS = 30.0
JPEG_QUALITY = 92

# ---------------------------------------------------------------------------
# NTNDArray → JPEG
# ---------------------------------------------------------------------------


def _normalize_to_uint8(
    pixels: np.ndarray[Any, Any],
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Map any integer or float pixel array to uint8.

    Signed integers: flip the sign bit (XOR 0x80) so that
    dtype_min → 0 and dtype_max → 255.

    Unsigned integers wider than 8 bits: right-shift to fit into [0, 255].

    Floats: assumed [0, 1] → [0, 255].
    """
    dt = pixels.dtype
    if dt == np.uint8:
        return pixels  # type: ignore[return-value]
    if np.issubdtype(dt, np.signedinteger):
        # Flip sign bit: e.g. int8  -128→0, 127→255
        #                     int16 -32768→0, 32767→65535 then >>8
        sign_bit = 1 << (dt.itemsize * 8 - 1)
        unsigned = pixels.view(np.dtype(f"u{dt.itemsize}")) ^ sign_bit
        if dt.itemsize > 1:
            shift = (dt.itemsize - 1) * 8
            return (unsigned >> shift).astype(np.uint8)
        return unsigned.astype(np.uint8)
    if np.issubdtype(dt, np.unsignedinteger):
        if dt.itemsize > 1:
            shift = (dt.itemsize - 1) * 8
            return (pixels >> shift).astype(np.uint8)
        return pixels.astype(np.uint8)
    # Float: assume [0, 1]
    return np.clip(pixels * 255, 0, 255).astype(np.uint8)


def _ndarray_to_jpeg(value: object) -> bytes:
    """Convert a p4p NTNDArray *value* to JPEG bytes (raw-pixel path only).

    *value* is a ``p4p.nt.ndarray.ntndarray`` — a numpy ndarray subclass
    that already carries the correct shape and dtype.
    """
    pixels: np.ndarray[Any, Any] = np.array(value)

    if pixels.ndim < 2:
        raise ValueError(f"Expected ≥2-D array, got {pixels.ndim}-D")

    pixels = _normalize_to_uint8(pixels)
    pixels = np.ascontiguousarray(pixels)

    mode = "L" if pixels.ndim == 2 else "RGB"
    buf = io.BytesIO()
    Image.fromarray(pixels, mode=mode).save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _encode_mjpeg_frame(jpeg_bytes: bytes) -> bytes:
    """Wrap JPEG bytes in a multipart MJPEG boundary block."""
    header = (
        b"--" + MJPEG_BOUNDARY + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
    )
    return header + jpeg_bytes + b"\r\n"


# ---------------------------------------------------------------------------
# Per-PV subscription state
# ---------------------------------------------------------------------------


class PVStream:
    """Live state for a single PV's MJPEG stream."""

    def __init__(self, pv_name: str, ctx: Context) -> None:
        self.pv_name = pv_name
        self.ctx = ctx
        self.latest_frame: bytes | None = None
        self.latest_raw: object = None
        self.frame_condition = asyncio.Condition()
        self.client_count = 0
        self._subscription: object = None
        self._teardown_task: asyncio.Task[None] | None = None
        self._torn_down = False

    def start(self) -> None:
        logger.info("Opening PVA subscription for %s", self.pv_name)
        self._subscription = self.ctx.monitor(  # type: ignore[no-untyped-call]
            self.pv_name, self.on_value, notify_disconnect=True
        )

    # -- p4p callbacks -------------------------------------------------------

    async def on_value(self, value: object) -> None:
        """p4p async monitor callback."""
        if isinstance(value, Exception):
            logger.error("PVA error on %s: %s", self.pv_name, value)
            return

        # Always cache the raw value so new clients get the latest frame.
        self.latest_raw = value

        # Only spend CPU on JPEG encoding when clients are watching.
        if self.client_count == 0:
            return

        self.encode(value)
        async with self.frame_condition:
            self.frame_condition.notify_all()

    def encode(self, value: object) -> None:
        """Encode *value* to JPEG and store as latest_frame."""
        try:
            jpeg = _ndarray_to_jpeg(value)
            self.latest_frame = _encode_mjpeg_frame(jpeg)
        except Exception:
            logger.warning("Failed to encode frame for %s", self.pv_name, exc_info=True)

    # -- client bookkeeping --------------------------------------------------

    def add_client(self) -> None:
        self.client_count += 1
        logger.debug("%s: client connected (%d total)", self.pv_name, self.client_count)
        if self._teardown_task and not self._teardown_task.done():
            logger.info("%s: cancelling idle teardown", self.pv_name)
            self._teardown_task.cancel()
            self._teardown_task = None
        # If we have a cached raw value but no encoded frame yet (because
        # no clients were connected when it arrived), encode it now.
        if self.latest_frame is None and self.latest_raw is not None:
            self.encode(self.latest_raw)

    def remove_client(self) -> None:
        self.client_count = max(0, self.client_count - 1)
        logger.debug(
            "%s: client disconnected (%d remaining)",
            self.pv_name,
            self.client_count,
        )
        if self.client_count == 0:
            self._schedule_teardown()

    def _schedule_teardown(self) -> None:
        logger.info(
            "%s: scheduling teardown in %.0f s",
            self.pv_name,
            IDLE_TEARDOWN_DELAY,
        )
        self._teardown_task = asyncio.get_running_loop().create_task(
            self._teardown_after_delay()
        )

    async def _teardown_after_delay(self) -> None:
        try:
            await asyncio.sleep(IDLE_TEARDOWN_DELAY)
        except asyncio.CancelledError:
            return
        await self.teardown()

    async def teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        logger.info("Closing PVA subscription for %s", self.pv_name)
        if self._subscription is not None:
            self._subscription.close()  # type: ignore[union-attr]
        _streams.pop(self.pv_name, None)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_streams: dict[str, PVStream] = {}
_streams_lock = asyncio.Lock()
_pva_context: Context | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    global _pva_context  # noqa: PLW0603
    logger.info("Starting p4p asyncio Context")
    _pva_context = Context("pva")
    yield
    logger.info("Shutting down — closing all PVA subscriptions")
    for stream in list(_streams.values()):
        await stream.teardown()
    _pva_context.close()
    logger.info("p4p Context closed")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="NTNDArray MJPEG streamer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Viewer page
# ---------------------------------------------------------------------------

_VIEWER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PVA MJPEG Viewer</title>
  <style>
    body {{ font-family: sans-serif; margin: 2em; background: #1e1e1e; color: #ccc; }}
    img  {{ border: 1px solid #555; max-width: 100%%; background: #000; }}
    form {{ margin-bottom: 1em; }}
    input[type=text] {{ width: 24em; padding: .3em; }}
    h1 {{ font-size: 1.4em; }}
  </style>
</head>
<body>
  <h1>PVA &rarr; MJPEG Viewer</h1>
  <form id="pvform">
    <label>PV name:
      <input type="text" id="pvname" value="{default_pv}">
    </label>
    <button type="submit">View</button>
  </form>
  <img id="stream" src="/mjpg/{default_pv}" alt="MJPEG stream">
  <script>
    document.getElementById("pvform").addEventListener("submit", function(e) {{
      e.preventDefault();
      var pv = document.getElementById("pvname").value.trim();
      if (pv) document.getElementById("stream").src = "/mjpg/" + pv;
    }});
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def viewer(pv: str = "BL01T-DI-CAM-01:PVA:OUTPUT") -> str:
    return _VIEWER_HTML.format(default_pv=html.escape(pv))


# ---------------------------------------------------------------------------
# MJPEG generator
# ---------------------------------------------------------------------------


async def _mjpeg_generator(stream: PVStream) -> AsyncGenerator[bytes, None]:
    min_interval = 1.0 / MAX_CLIENT_FPS
    last_sent = 0.0

    # Send the most recent frame immediately if available.
    if stream.latest_frame is not None:
        yield stream.latest_frame
        last_sent = time.monotonic()

    while True:
        try:
            async with asyncio.timeout(5.0):
                async with stream.frame_condition:
                    await stream.frame_condition.wait()
        except TimeoutError:
            # No frame in 5 s – keep connection alive; the outer wrapper
            # checks ``is_disconnected()``.
            continue

        frame = stream.latest_frame
        if frame is None:
            continue

        now = time.monotonic()
        gap = now - last_sent
        if gap < min_interval:
            await asyncio.sleep(min_interval - gap)

        yield frame
        last_sent = time.monotonic()


# ---------------------------------------------------------------------------
# MJPEG route
# ---------------------------------------------------------------------------


@app.get("/mjpg/{pv_name:path}")
async def mjpeg_stream(pv_name: str, request: Request) -> Response:
    if _pva_context is None:
        return Response(status_code=503, content="PVA context not ready")

    async with _streams_lock:
        stream = _streams.get(pv_name)
        if stream is None:
            stream = PVStream(pv_name=pv_name, ctx=_pva_context)
            stream.start()
            _streams[pv_name] = stream
        stream.add_client()

    async def _guarded() -> AsyncGenerator[bytes, None]:
        try:
            async for frame in _mjpeg_generator(stream):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            stream.remove_client()

    return StreamingResponse(
        _guarded(),
        media_type=MJPEG_CONTENT_TYPE,
        headers={
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
