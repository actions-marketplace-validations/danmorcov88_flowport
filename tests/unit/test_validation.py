"""validate: the document it uploads, the result it builds, the CLI contract (offline)."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from flowport.cli import app
from flowport.loaders import load
from flowport.nifi import ComponentState, NiFiClient, NiFiError
from flowport.validation import definition_for_validation, validate_flow

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nifi-1.28.1"
runner = CliRunner()


def test_flow_json_is_wrapped_into_a_definition() -> None:
    document, warnings = definition_for_validation(load(FIXTURES / "flow.json.gz"))
    assert document["flowEncodingVersion"] == "1.0"
    assert document["flowContents"]["name"] == "NiFi Flow"
    assert "templates" not in document["flowContents"]
    assert set(document["parameterContexts"]) == {
        "Base Context",
        "Fixture Context",
        "Clean Context",
    }
    assert document["parameterContexts"]["Fixture Context"]["inheritedParameterContexts"] == [
        "Base Context"
    ]
    assert [w.split(":")[0] for w in warnings] == [
        "2 template(s) are not imported; NiFi 2.x drops them "
        "(convert them with 'migrate templates').",
        "reporting tasks are not imported with a process group",
    ]


def test_definition_is_uploaded_as_is() -> None:
    flow = load(FIXTURES / "definitions" / "clean.json")
    document, warnings = definition_for_validation(flow)
    assert document == flow.raw and document is not flow.raw
    assert warnings == []


class FakeNiFi(NiFiClient):
    """Answers like a NiFi that finds one invalid processor; records what was called."""

    def __init__(self) -> None:
        super().__init__("https://fake:8443/nifi-api", username="u", password="p")
        self.calls: list[str] = []

    def login(self) -> None:
        self.calls.append("login")
        self.token = "t"

    def about(self) -> dict[str, Any]:
        return {"version": "2.12.0"}

    def upload_definition(self, parent_id: str, name: str, document: dict[str, Any]) -> str:
        self.calls.append(f"upload:{name}:{document['flowContents']['name']}")
        return "g1"

    def wait_validated(
        self, group_id: str = "root", timeout: float = 120
    ) -> dict[str, ComponentState]:
        self.calls.append(f"validate:{group_id}")
        return {
            "p1": ComponentState("p1", "ok", "t", "PROCESSOR", "/g", "VALID", (), False),
            "p2": ComponentState("p2", "bad", "t", "PROCESSOR", "/g", "INVALID", ("boom",), False),
            "p3": ComponentState("p3", "gone", "t", "PROCESSOR", "/g", "INVALID", (), True),
        }

    def delete_group(self, group_id: str, timeout: float = 60) -> None:
        self.calls.append(f"delete:{group_id}")


def test_validate_flow_collects_state_and_cleans_up() -> None:
    client = FakeNiFi()
    result = validate_flow(load(FIXTURES / "definitions" / "clean.json"), client)
    assert client.calls == [
        "login",
        "upload:flowport validation of clean.json:Clean",
        "validate:g1",
        "delete:g1",
    ]
    assert result.summary() == {"components": 3, "valid": 1, "invalid": 1, "ghosts": 1}
    assert [c.name for c in result.invalid] == ["bad", "gone"]
    as_dict = result.to_dict()
    assert as_dict["nifi"] == {"url": "https://fake:8443/nifi-api", "version": "2.12.0"}
    assert {c["name"]: c["errors"] for c in as_dict["components"]}["bad"] == ["boom"]
    kept = validate_flow(load(FIXTURES / "definitions" / "clean.json"), FakeNiFi(), keep=True)
    assert kept.kept and kept.group_id == "g1"


def test_delete_failure_becomes_a_warning() -> None:
    class Stubborn(FakeNiFi):
        def delete_group(self, group_id: str, timeout: float = 60) -> None:
            raise NiFiError("409 nope")

    result = validate_flow(load(FIXTURES / "definitions" / "clean.json"), Stubborn())
    assert any("could not delete" in w for w in result.warnings)


def test_cli_rejects_missing_or_conflicting_targets(tmp_path: Path) -> None:
    flow = str(FIXTURES / "definitions" / "clean.json")
    assert runner.invoke(app, ["validate", flow]).exit_code == 2
    assert (
        runner.invoke(app, ["validate", flow, "--nifi-url", "http://x", "--docker"]).exit_code == 2
    )
    unreachable = runner.invoke(
        app, ["validate", flow, "--nifi-url", "http://127.0.0.1:9/nifi-api"]
    )
    assert unreachable.exit_code == 2
    missing = runner.invoke(app, ["validate", str(tmp_path / "no.json"), "--nifi-url", "http://x"])
    assert missing.exit_code == 2


def test_cli_json_output(monkeypatch: Any, tmp_path: Path) -> None:
    import flowport.validation as validation

    monkeypatch.setattr(
        validation,
        "validate_flow",
        lambda flow, client, keep=False: validate_flow(flow, FakeNiFi(), keep=keep),
    )
    out = tmp_path / "v.json"
    result = runner.invoke(
        app,
        [
            "validate",
            str(FIXTURES / "definitions" / "clean.json"),
            "--nifi-url",
            "https://fake",
            "-f",
            "json",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 1, result.output  # one invalid, one ghost
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["ghosts"] == 1
    terminal = runner.invoke(
        app,
        ["validate", str(FIXTURES / "definitions" / "clean.json"), "--nifi-url", "https://fake"],
    )
    assert terminal.exit_code == 1
    assert "1 invalid" in terminal.output and "missing type" in terminal.output
