from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def flow_gz(fixtures_dir: Path) -> Path:
    return fixtures_dir / "nifi-1.28.1" / "flow.json.gz"


@pytest.fixture(scope="session")
def flow_json(fixtures_dir: Path) -> Path:
    return fixtures_dir / "nifi-1.28.1" / "flow.json"


@pytest.fixture(scope="session")
def definitions_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "nifi-1.28.1" / "definitions"
