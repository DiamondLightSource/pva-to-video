import subprocess
import sys

from pva_to_video import __version__


def test_cli_version():
    cmd = [sys.executable, "-m", "pva_to_video", "--version"]
    assert subprocess.check_output(cmd).decode().strip() == __version__
