"""Golden-file tests: fixture in, JSON report out, compared to tests/golden.

Refresh on purpose with ``pytest --update-golden``. The ``tool`` block is
stripped before comparison so that version bumps do not touch golden files.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.reports import build_report, render_json
from flowport.rules import analyze

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "nifi-1.28.1"

CASES = [
    ("flow", FIXTURES / "flow.json.gz"),
    *[(f"definition-{p.stem}", p) for p in sorted((FIXTURES / "definitions").glob("*.json"))],
]


def _report(path: Path) -> dict[str, Any]:
    flow = load(path)
    catalog = load_catalog()
    report = build_report(flow, analyze(flow, catalog), catalog, path)
    del report["tool"]
    return report


@pytest.mark.parametrize(("name", "path"), CASES, ids=[c[0] for c in CASES])
def test_report_matches_golden(name: str, path: Path, update_golden: bool) -> None:
    actual = render_json(_report(path))
    golden = GOLDEN / f"{name}.json"
    if update_golden:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8", newline="\n")
        pytest.skip("golden file updated")
    assert golden.exists(), f"missing golden file {golden}; run pytest --update-golden"
    expected = golden.read_text(encoding="utf-8")
    assert json.loads(actual) == json.loads(expected)
    assert actual == expected, "JSON output differs from golden file (formatting)"


def test_clean_fixture_has_no_findings() -> None:
    report = _report(FIXTURES / "definitions" / "clean.json")
    assert report["summary"]["total"] == 0


def test_every_rule_category_is_exercised_by_the_flow_fixture() -> None:
    report = _report(FIXTURES / "flow.json.gz")
    rules = set(report["summary"]["by_rule"])
    expected = {
        "NIFI2-REMOVED-COMPONENT",
        "NIFI2-RENAMED-COMPONENT",
        "NIFI2-THIRD-PARTY-BUNDLE",
        "NIFI2-SCRIPT-ENGINE",
        "NIFI2-VARIABLES-DEFINED",
        "NIFI2-VARIABLE-REFERENCE",
        "NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE",
        "NIFI2-VARIABLE-REFERENCE-FUNCTION",
        "NIFI2-VARIABLE-REFERENCE-NO-EL",
        "NIFI2-VARIABLE-UNUSED",
        "NIFI2-VARIABLE-NAME",
        "NIFI2-TEMPLATE",
        "NIFI2-EVENT-DRIVEN",
        "NIFI2-CRON-YEAR-FIELD",
        "NIFI2-CRON-NUMERIC-DAY-OF-WEEK",
        "NIFI2-INVOKEHTTP-PROXY-PROPERTIES",
    }
    assert expected <= rules
