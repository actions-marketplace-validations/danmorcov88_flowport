"""Sanity checks on the committed fixtures."""

import gzip
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"


def test_flow_json_gz_matches_flow_json() -> None:
    with gzip.open(FIXTURES / "flow.json.gz", "rb") as fh:
        compressed = json.load(fh)
    plain = json.loads((FIXTURES / "flow.json").read_text(encoding="utf-8"))
    assert compressed == plain


@pytest.mark.parametrize(
    "path", sorted((FIXTURES / "definitions").glob("*.json")), ids=lambda p: p.stem
)
def test_definition_is_a_flow_definition(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "flowContents" in data
    assert data["flowContents"]["componentType"] == "PROCESS_GROUP"


def test_expected_groups_present() -> None:
    flow = json.loads((FIXTURES / "flow.json").read_text(encoding="utf-8"))
    names = {group["name"] for group in flow["rootGroup"]["processGroups"]}
    assert names == {
        "Variables",
        "Existing Context",
        "Removed Components",
        "Scripting",
        "Scheduling",
        "Deprecated Properties",
        "Custom NAR",
        "Clean",
    }
    assert len(flow["templates"]) == 1
