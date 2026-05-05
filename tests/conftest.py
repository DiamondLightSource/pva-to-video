"""Shared pytest configuration."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--pva-to-video-url",
        default=None,
        help="Base URL of a running pva-to-video instance (e.g. http://localhost:8080)",
    )


@pytest.fixture
def base_url(request: pytest.FixtureRequest) -> str:
    url = request.config.getoption("--pva-to-video-url")
    if url is None:
        pytest.skip("--pva-to-video-url not supplied")
    return str(url)
