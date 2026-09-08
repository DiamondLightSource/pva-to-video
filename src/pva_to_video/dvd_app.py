"""
Use davidia backend to deliver plot data to frontend
"""

import logging
from collections.abc import AsyncGenerator

from davidia.server.plot_server import PlotServer, handle_client
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware  # comment this on deployment
from fastapi.responses import StreamingResponse

from pva_to_video import pvaccess

logger = logging.getLogger("main")


async def gated_mjpeg_stream(pv_name: str, request: Request) -> Response:
    if pvaccess.pva_context is None:
        return Response(status_code=503, content="PVA context not ready")

    # as we need to initiate the stream with a callback, block access
    if not pvaccess.has_stream(pv_name):
        logger.debug("PVAStream not started")
        return Response(
            status_code=503, content="Stream needs to initialized by davidia"
        )

    stream = await pvaccess.get_stream(pv_name)
    logger.debug("PVAStream active")

    async def _guarded() -> AsyncGenerator[bytes, None]:
        try:
            async for frame in pvaccess.attr_generator(stream, "latest_frame"):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            stream.remove_client()

    logger.debug("Start streaming MJPEG")
    return StreamingResponse(
        _guarded(),
        media_type=pvaccess.MJPEG_CONTENT_TYPE,
        headers={
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def create_app():
    logger.debug(
        "Starting fastapi app with lifespan %s, context %s",
        pvaccess.lifespan,
        pvaccess.pva_context,
    )
    app = FastAPI(title="Davidia streamer", lifespan=pvaccess.lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_headers=["*"],
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_credentials=True,
    )  # comment this on deployment
    ps = PlotServer()
    # setattr(app, "_plot_server", ps)

    @app.websocket("/plot/{uuid}/{plot_id}")
    async def websocket(websocket: WebSocket, uuid: str, plot_id: str):
        """End point for plot server to web UI communication.

        PlotMessages are passed between client/server
        """
        await websocket.accept()
        await handle_client(ps, plot_id, websocket, uuid)

    app.add_api_route("/mjpg/{pv_name:path}", gated_mjpeg_stream, methods=["GET"])
    return app


def create_parser():
    from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

    parser = ArgumentParser(
        description="Davidia plot server", formatter_class=ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-H", "--host", help="Set the host address for server", default="127.0.0.1"
    )
    parser.add_argument(
        "-P", "--port", help="Set the port number for server", type=int, default=80
    )
    return parser


def _setup_logger():
    ch = logging.StreamHandler()
    ch.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)


def run_app(host="127.0.0.1", port=80):
    import uvicorn

    _setup_logger()
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


def main():
    args = create_parser().parse_args()
    run_app(
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
