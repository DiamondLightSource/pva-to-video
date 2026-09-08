"""MJPEG streaming backend for areaDetector NTNDArray PVs.

Serves ``GET /mjpg/{pv_name}`` as a ``multipart/x-mixed-replace`` MJPEG
stream.  A p4p subscription is started on the first client connect and
torn down after :data:`IDLE_TEARDOWN_DELAY` seconds with no clients.

A minimal viewer page is served at ``GET /`` for manual validation.
"""

from __future__ import annotations

import html
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from . import pvaccess

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="NTNDArray MJPEG streamer", lifespan=pvaccess.lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
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
    img, canvas {{ border: 1px solid #555; max-width: 100%%; background: #000;
               display: block; margin-bottom: .5em; }}
    form {{ margin-bottom: 1em; }}
    input[type=text] {{ width: 24em; padding: .3em; }}
    h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.1em; margin-top: 1.5em; }}
  </style>
</head>
<body>
  <h1>PVA &rarr; MJPEG / WebSocket Viewer</h1>
  <form id="pvform">
    <label>PV name:
      <input type="text" id="pvname" value="{default_pv}">
    </label>
    <button type="submit">View</button>
  </form>
  <h2>MJPEG (HTTP)</h2>
  <img id="stream" src="/mjpg/{default_pv}" alt="MJPEG stream">
  <h2>WebSocket (canvas)</h2>
  <canvas id="wscanvas"></canvas>
  <script>
    var ctx = document.getElementById('wscanvas').getContext('2d');
    var ws;
    function connectWs(pv) {{
      if (ws) ws.close();
      var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(proto + '//' + location.host + '/ws/mjpg/' + pv);
      ws.binaryType = 'blob';
      ws.onmessage = async function(e) {{
        var bmp = await createImageBitmap(e.data);
        ctx.canvas.width = bmp.width;
        ctx.canvas.height = bmp.height;
        ctx.drawImage(bmp, 0, 0);
      }};
    }}
    document.getElementById('pvform').addEventListener('submit', function(e) {{
      e.preventDefault();
      var pv = document.getElementById('pvname').value.trim();
      if (pv) {{
        document.getElementById('stream').src = '/mjpg/' + pv;
        connectWs(pv);
      }}
    }});
    connectWs(document.getElementById('pvname').value.trim());
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def viewer(pv: str = "BL01T-DI-CAM-01:PVA:OUTPUT") -> str:
    return _VIEWER_HTML.format(default_pv=html.escape(pv))


# ---------------------------------------------------------------------------
# MJPEG route
# ---------------------------------------------------------------------------


async def mjpeg_stream(pv_name: str, request: Request) -> Response:
    if pvaccess.pva_context is None:
        return Response(status_code=503, content="PVA context not ready")

    stream = await pvaccess.get_stream(pv_name)

    async def _guarded() -> AsyncGenerator[bytes, None]:
        try:
            async for frame in pvaccess.attr_generator(stream, "latest_frame"):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            stream.remove_client()

    return StreamingResponse(
        _guarded(),
        media_type=pvaccess.MJPEG_CONTENT_TYPE,
        headers={
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


app.add_api_route("/mjpg/{pv_name:path}", mjpeg_stream, methods=["GET"])


# ---------------------------------------------------------------------------
# WebSocket JPEG route
# ---------------------------------------------------------------------------


@app.websocket("/ws/mjpg/{pv_name:path}")
async def ws_mjpeg_stream(websocket: WebSocket, pv_name: str) -> None:
    if pvaccess.pva_context is None:
        await websocket.close(code=1013)  # Try again later
        return

    stream = await pvaccess.get_stream(pv_name)

    await websocket.accept()
    try:
        async for jpeg in pvaccess.attr_generator(stream, "latest_jpeg"):
            try:
                await websocket.send_bytes(jpeg)
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        stream.remove_client()
