"""Write docs/rules.md from the rule catalog.

    python tools/build_rule_reference.py

The unit test ``tests/unit/test_rule_reference.py`` fails when the page is
out of date; ``pytest --update-golden`` regenerates it as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

from flowport.catalog import load_catalog
from flowport.catalog.reference import render_rule_reference

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "rules.md"


def main() -> int:
    OUTPUT.write_text(render_rule_reference(load_catalog()), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
