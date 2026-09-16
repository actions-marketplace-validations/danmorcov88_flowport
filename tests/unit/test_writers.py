import gzip
import json
from pathlib import Path

import pytest

from flowport.loaders import load, read_bytes
from flowport.writers import dumps, write

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
INPUTS = [
    FIXTURES / "flow.json.gz",
    FIXTURES / "flow.json",
    *sorted((FIXTURES / "definitions").glob("*.json")),
]


@pytest.mark.parametrize("source", INPUTS, ids=[p.name for p in INPUTS])
def test_load_then_write_round_trips(source: Path, tmp_path: Path) -> None:
    flow = load(source)
    target = tmp_path / source.name
    write(flow, target)
    assert json.loads(read_bytes(target)) == json.loads(read_bytes(source))
    # A second load -> write cycle reproduces the bytes exactly.
    again = tmp_path / "again" / source.name
    write(load(target), again)
    assert again.read_bytes() == target.read_bytes()


def test_gzip_output_has_fixed_timestamp_and_no_name(tmp_path: Path) -> None:
    flow = load(FIXTURES / "flow.json")
    target = tmp_path / "out.json.gz"
    write(flow, target)
    raw = target.read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert raw[4:8] == b"\x00\x00\x00\x00"  # MTIME
    assert raw[3] == 0  # FLG: no FNAME, no comment
    assert gzip.decompress(raw).decode("utf-8") == dumps(flow.raw)


def test_plain_output_when_suffix_is_not_gz(tmp_path: Path) -> None:
    flow = load(FIXTURES / "flow.json.gz")
    target = tmp_path / "sub" / "flow.json"
    write(flow, target)
    assert target.read_bytes()[:1] == b"{"
    assert target.read_text(encoding="utf-8").endswith("}\n")


def test_dumps_keeps_key_order_and_unicode() -> None:
    text = dumps({"b": "é", "a": {"z": 1.0, "y": None}})
    assert text == '{\n  "b": "é",\n  "a": {\n    "z": 1.0,\n    "y": null\n  }\n}\n'
