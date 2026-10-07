"""docs/archive_verify.json is a run of the stand-alone verifier, made again here (scripts/archive_verify.py)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("archive_verify_script", ROOT / "scripts" / "archive_verify.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_recorded_run_is_what_the_verifier_says_today_and_the_demo_shows_that_line():
    kept = json.loads((ROOT / "docs" / "archive_verify.json").read_text(encoding="utf-8"))
    assert _script().run() == kept
    assert kept["lines"][-1].startswith("VERIFIED: ") and kept["lines"][-1].endswith("No network, no Knos.")
    assert any(line.startswith("note: UNSIGNED:") for line in kept["lines"])          # the samples carry no token, and it is said
    assert len(kept["says"].split()) <= 12 and (ROOT / kept["link"]).is_file()
    shown = json.loads((ROOT / "web" / "demo_data.json").read_text(encoding="utf-8"))
    assert shown["verify"] == {"says": kept["says"], "link": kept["link"]} and shown["bank_file"] is True
    assert "archive_verify.json" in (ROOT / kept["link"]).read_text(encoding="utf-8")
