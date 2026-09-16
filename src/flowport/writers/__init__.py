"""Write flow documents back to disk.

The writer serializes the raw JSON document that the loaders kept, so every
field survives a load -> write cycle unchanged. Key order is preserved (no
``sort_keys``), the indent is the two spaces NiFi uses, and gzip output has a
fixed timestamp so that the same document always produces the same bytes.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from flowport.model import Flow


def dumps(document: dict[str, Any]) -> str:
    """Serialize a flow document the way NiFi formats ``flow.json``."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_document(document: dict[str, Any], path: Path) -> None:
    """Write a document as JSON, gzip-compressed when the file name ends in ``.gz``."""
    data = dumps(document).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        data = gzip.compress(data, mtime=0)
    path.write_bytes(data)


def write(flow: Flow, path: Path) -> None:
    """Write a loaded flow (its raw document) to ``path``."""
    write_document(flow.raw, path)
