"""Compiles firmware/fridge_node/control.cpp + outbox.cpp for the host and runs
tests/firmware_host/test_firmware_logic.cpp (skipped if no C++ compiler)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
def test_firmware_logic(tmp_path):
    script = Path(__file__).parent / "firmware_host" / "run.sh"
    r = subprocess.run(["sh", str(script)], capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all firmware logic checks passed" in r.stdout
