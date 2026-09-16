"""Acceptance criterion: a 50 MB flow.json.gz is analyzed in under 60 seconds."""

import time
from pathlib import Path

import pytest

from flowport.catalog import load_catalog
from flowport.loaders import load
from flowport.reports import build_report, render_json
from flowport.rules import analyze
from tests.large_flow import write_large_flow

FIFTY_MB = 50 * 1024 * 1024


@pytest.mark.slow
def test_large_flow_under_sixty_seconds(tmp_path: Path) -> None:
    path = tmp_path / "large.json.gz"
    groups = write_large_flow(path, FIFTY_MB)
    assert path.stat().st_size >= FIFTY_MB

    started = time.perf_counter()
    catalog = load_catalog()
    flow = load(path)
    findings = analyze(flow, catalog)
    render_json(build_report(flow, findings, catalog, path))
    elapsed = time.perf_counter() - started

    assert sum(1 for _ in flow.groups()) > groups  # top-level clones plus their children
    assert findings
    assert elapsed < 60, f"analysis took {elapsed:.1f}s for {groups} groups"
