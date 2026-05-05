"""Interface for ``python -m pva_to_video``."""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from collections.abc import Sequence

from . import __version__

__all__ = ["main"]

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080


def main(args: Sequence[str] | None = None) -> None:
    """Run the MJPEG streaming server."""
    parser = ArgumentParser(description="PVA → MJPEG streaming server")
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=__version__,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind address (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bind port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
    )
    parsed = parser.parse_args(args)

    logging.basicConfig(
        level=getattr(logging, parsed.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import uvicorn

    uvicorn.run(
        "pva_to_video.app:app",
        host=parsed.host,
        port=parsed.port,
        log_level=parsed.log_level,
        workers=1,  # p4p subscriptions are not fork-safe
    )


if __name__ == "__main__":
    main()
