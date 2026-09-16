"""docs/rules.md is generated from the catalog and must match it."""

from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.catalog.reference import RULE_REFERENCE_URL, anchor, render_rule_reference
from flowport.loaders import load
from flowport.rules import analyze

DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "rules.md"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"


def test_rule_reference_is_up_to_date(update_golden: bool) -> None:
    expected = render_rule_reference(load_catalog())
    if update_golden:
        DOCS.write_text(expected, encoding="utf-8", newline="\n")
        pytest.skip("docs/rules.md regenerated")
    assert DOCS.read_text(encoding="utf-8") == expected, (
        "docs/rules.md is out of date; run python tools/build_rule_reference.py"
    )


def test_every_rule_has_a_section_and_findings_link_to_it() -> None:
    catalog = load_catalog()
    page = render_rule_reference(catalog)
    for rule_id in catalog.rules:
        assert f"### {rule_id}\n" in page
    findings = analyze(load(FIXTURES / "flow.json.gz"), catalog)
    for finding in findings:
        assert finding.reference == f"{RULE_REFERENCE_URL}#{anchor(finding.rule_id)}"
        assert f"### {finding.rule_id}\n" in page
    for replacement in catalog.replacements.values():
        assert f"`{replacement.from_type}`" in page
