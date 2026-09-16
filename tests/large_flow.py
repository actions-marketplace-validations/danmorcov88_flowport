"""Generate a large flow.json.gz by repeating the fixture process groups.

Used by the performance test; never committed as a file.
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nifi-1.28.1" / "flow.json"


def _renumber(node: Any, suffix: str) -> None:
    if isinstance(node, dict):
        for key in ("identifier", "instanceIdentifier", "groupIdentifier"):
            if isinstance(node.get(key), str):
                node[key] = f"{node[key]}-{suffix}"
        for value in node.values():
            _renumber(value, suffix)
    elif isinstance(node, list):
        for value in node:
            _renumber(value, suffix)


def write_large_flow(path: Path, min_compressed_bytes: int) -> int:
    """Write a flow.json.gz of at least the given compressed size; returns the group count."""
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    groups = source["rootGroup"]["processGroups"]
    head = copy.deepcopy(source)
    head["rootGroup"]["processGroups"] = []
    text = json.dumps(head)
    before, after = text.rsplit('"processGroups": []', 1)
    count = 0
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write((before + '"processGroups": [').encode("utf-8"))
        first = True
        round_ = 0
        while True:
            for group in groups:
                clone = copy.deepcopy(group)
                clone["name"] = f"{group['name']} {round_}"
                _renumber(clone, f"c{round_}")
                fh.write(("" if first else ",\n").encode("utf-8"))
                fh.write(json.dumps(clone, indent=2).encode("utf-8"))
                first = False
                count += 1
            round_ += 1
            if round_ % 200 == 0:
                fh.flush()
                if path.stat().st_size >= min_compressed_bytes:
                    break
        fh.write(("]" + after).encode("utf-8"))
    return count
