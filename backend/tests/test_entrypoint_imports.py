"""Every real process entry point must import cleanly in a fresh interpreter.

In-suite imports hide circular-import bugs (module order differs), so each
entry module gets its own subprocess, as in compose.yaml.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# app.main (uvicorn app.main:app) is imported fresh by test_flag_off_imports,
# which also asserts no printer-lib leak -- no need to pay a second cold import.
ENTRY_MODULES = [
    "app.tasks.celery_app",  # celery -A app.tasks.celery_app (worker + beat)
    "app.printerd",  # python -m app.printerd
    "app.virtual_printer",  # python -m app.virtual_printer
]


@pytest.mark.parametrize("module", ENTRY_MODULES)
def test_entrypoint_imports_in_fresh_process(module):
    out = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        timeout=120,
    )
    assert out.returncode == 0, f"import {module} failed:\n{out.stderr}"
