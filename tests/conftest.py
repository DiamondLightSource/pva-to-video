"""Shared pytest configuration."""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest


def fixture_is_used(fixture_name: str, session: pytest.Session) -> bool:
    for item in session.items:
        for f in item.fixturenames:
            if f == fixture_name:
                return True
    return False


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    if fixture_is_used("docker_composer", session):
        check_docker_sock()


def check_docker_sock() -> None:
    """Verify the container engine socket is reachable.

    Raises :exc:`RuntimeError` with remediation hints if ``docker info``
    fails, so CI and devcontainer users get an actionable error rather than
    a cryptic fixture failure later.
    """
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as err:
        message = (
            "Cannot communicate with the container engine on the host.\n"
            "Please make sure $DOCKER_HOST points to the correct socket.\n"
            "NOTE:\n"
            "  For podman, set:\n"
            '    export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/podman/podman.sock"\n'
            "  and enable the socket with:\n"
            "    systemctl --user enable podman --now"
        )
        raise RuntimeError(message) from err


@pytest.fixture(scope="module")
def docker_composer():  # type: ignore[return]
    """Module-scoped factory fixture for running docker compose services.

    Yields a callable that starts a ``docker compose up`` process, optionally
    waits for a log line or a fixed delay, then tears the service down after
    the test module finishes.

    Example::

        def my_service(docker_composer):
            yield from docker_composer(
                docker_args=["-f", "./compose.yaml"],
                docker_services="my-svc",
                ready_log_line="Server ready",
                start_timeout=60.0,
            )
    """

    def inner_docker_composer(
        docker_args: list[str] | None = None,
        docker_services: list[str] | str | None = None,
        ready_log_line: str | None = None,
        start_timeout: float | None = None,
        stop_timeout: float | None = None,
        wait_time: float | None = None,
    ):  # type: ignore[return]
        if docker_args is None:
            docker_args = []

        if docker_services is None:
            docker_services = []
        elif isinstance(docker_services, str):
            docker_services = [docker_services]

        process = subprocess.Popen(
            ["docker", "compose", *docker_args, "up", *docker_services],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            preexec_fn=os.setsid,  # kill the whole process group on teardown
        )

        start_time = time.time()
        if ready_log_line is not None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="")
                    if ready_log_line in line:
                        break
                    if (
                        start_timeout is not None
                        and time.time() - start_time > start_timeout
                    ):
                        raise TimeoutError(
                            f"docker compose {docker_args} timed out waiting for "
                            f"{ready_log_line!r}"
                        )
            except Exception:
                process.terminate()
                raise

        if wait_time is not None:
            time.sleep(wait_time)

        yield  # service is now expected to be ready

        try:
            subprocess.run(
                ["docker", "compose", *docker_args, "down", *docker_services],
                check=False,
            )
        except Exception as exc:
            print(f"Failed to bring down docker services: {exc}")

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass  # already exited

        if process.stdout:
            process.stdout.close()

        process.wait(timeout=stop_timeout)

    yield inner_docker_composer
