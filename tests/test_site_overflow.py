"""No page of the site scrolls sideways at 320, 360, 390, 768 or 1280 px: tests/web/overflow.mjs, run on a build of web/.

The measuring is in the node script (headless Chromium through the `playwright` package, as tests/web/site.mjs). This
file builds the site as the Pages build does and runs it. No node, no package or no browser: skipped, with the reason."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = "/opt/pw-browsers"          # where this project's machines keep Chromium; anywhere else playwright's default holds


def test_no_page_scrolls_sideways(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    if os.name == "nt":
        pytest.skip("the Pages build runs on ubuntu-latest")
    site = tmp_path / "site"
    env = {**os.environ, "PYTHON": sys.executable, "PYTHONPATH": str(ROOT / "src")}
    if "PLAYWRIGHT_BROWSERS_PATH" not in env and Path(BROWSERS).is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS
    subprocess.run(["bash", str(ROOT / "scripts" / "build_site.sh"), str(site), "c" * 40], check=True, env=env, capture_output=True, timeout=120)
    run = subprocess.run([node, str(ROOT / "tests" / "web" / "overflow.mjs"), str(site)], env=env, capture_output=True, text=True, timeout=600)
    if run.returncode == 0 and run.stdout.startswith("SKIP"):
        pytest.skip(run.stdout.strip()[5:])
    assert run.returncode == 0, "\n".join(line for line in (run.stdout + run.stderr).splitlines() if not line.startswith("ok"))
    assert "none scrolls sideways" in run.stdout
