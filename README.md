[![CI](https://github.com/DiamondLightSource/pva-to-video/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamondLightSource/pva-to-video/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/DiamondLightSource/pva-to-video/branch/main/graph/badge.svg)](https://codecov.io/gh/DiamondLightSource/pva-to-video)
[![PyPI](https://img.shields.io/pypi/v/pva-to-video.svg)](https://pypi.org/project/pva-to-video)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# pva_to_video

HTTP service that converts EPICS PVA NTNDArray streams into MJPEG video streams consumable by any web browser.

What            | Where
:---:           | :---:
Source          | <https://github.com/DiamondLightSource/pva-to-video>
PyPI            | `pip install pva-to-video`
Docker          | `docker run ghcr.io/diamondlightsource/pva-to-video:latest`
Releases        | <https://github.com/DiamondLightSource/pva-to-video/releases>

## Overview

EPICS areaDetector cameras publish images as NTNDArray structures over PVA.
Desktop tools like Phoebus can display these streams directly, but web
applications cannot speak PVA.  **pva-to-video** bridges the gap by
subscribing to a PVA channel on demand and re-encoding the pixel data as an
MJPEG stream served over HTTP — a format every browser understands natively
via an `<img>` tag.

### How it works

1. A client requests `GET /mjpg/<PV_NAME>`.
2. The service opens a PVA monitor on **PV_NAME** (if one is not already open).
3. Each incoming NTNDArray is normalised to uint8 and JPEG-encoded.
4. Frames are pushed to all connected clients as a `multipart/x-mixed-replace`
   MJPEG stream, capped at 30 fps per client.
5. When the last client disconnects the subscription is kept alive for 60 s
   to avoid churn, then torn down automatically.

Supported pixel formats: **uint8/16/32/64**, **int8/16/32/64**, and **float32/64**.
Greyscale (2-D) and colour (H×W×3 RGB) arrays are both handled.

## Usage

### Embedding in a web page

Point an `<img>` tag at the service:

```html
<img src="https://my-server:8080/mjpg/BL01T-DI-CAM-01:PVA:OUTPUT" />
```

A built-in viewer page is available at `GET /` for quick manual testing.

### Running with pip

```bash
pip install pva-to-video
pva-to-video                        # listen on 0.0.0.0:8080
pva-to-video --port 9000            # custom port
pva-to-video --log-level debug      # verbose logging
```

If cameras are on a different subnet, set the PVA name server:

```bash
EPICS_PVA_NAME_SERVERS=192.168.1.50 pva-to-video
```

### Running with Docker

```bash
docker run --rm -p 8080:8080 ghcr.io/diamondlightsource/pva-to-video:latest
```

To reach IOCs on the host network:

```bash
docker run --rm --network host ghcr.io/diamondlightsource/pva-to-video:latest
```

With a remote PVA gateway:

```bash
docker run --rm -p 8080:8080 \
  -e EPICS_PVA_NAME_SERVERS=192.168.1.50 \
  ghcr.io/diamondlightsource/pva-to-video:latest
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8080` | Bind port |
| `--log-level` | `info` | `debug`, `info`, `warning`, or `error` |
