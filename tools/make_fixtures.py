"""Generate test fixtures from a real NiFi 1.x instance.

Starts ``apache/nifi:<version>`` in Docker, builds a set of example flows
through the REST API, then exports:

- ``tests/fixtures/nifi-<version>/flow.json.gz``     whole canvas, as written by NiFi
- ``tests/fixtures/nifi-<version>/flow.json``        the same, decompressed
- ``tests/fixtures/nifi-<version>/definitions/*.json`` one flow definition per top-level group
- ``tests/fixtures/nifi-<version>/templates/*.xml``  XML templates

Every fixture group targets one or more analyzer rule categories. See
``tests/fixtures/README.md`` for the list.

Usage::

    python tools/make_fixtures.py                 # start NiFi, build, export, stop
    python tools/make_fixtures.py --keep          # leave the container running
    python tools/make_fixtures.py --url http://localhost:8080/nifi-api  # reuse an instance

Runs at development time only. Never imported by the package.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_VERSION = "1.28.1"
CONTAINER = "flowport-fixtures"
IMAGE = "apache/nifi"
FLOW_PATH_IN_CONTAINER = "/opt/nifi/nifi-current/conf/flow.json.gz"
CLIENT_ID = "flowport-make-fixtures"
# Fixed key so that any test container (1.x or 2.x) can load the exported flow.
SENSITIVE_PROPS_KEY = "flowport-fixtures-key"

# A processor created as UpdateAttribute and rewritten after export to look
# like a component from a third-party NAR (what NiFi calls a "ghost" component
# when the NAR is missing). NiFi cannot create an unknown type through the API.
CUSTOM_PROCESSOR_NAME = "Custom Processor"
CUSTOM_TYPE = "com.example.nifi.processors.CustomProcessor"
CUSTOM_BUNDLE = {"group": "com.example", "artifact": "example-custom-nar", "version": "1.0.0"}


class NiFi:
    """Minimal NiFi 1.x REST client using only the standard library."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._types: dict[str, dict[str, str]] = {}

    # -- HTTP -----------------------------------------------------------

    def request(self, method: str, path: str, body: Any = None, *, raw: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "*/*"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from None
        if raw or not payload:
            return payload
        return json.loads(payload)

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body)

    def wait_ready(self, timeout: float = 600) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                self.get("/flow/process-groups/root")
                return
            except (urllib.error.URLError, RuntimeError, ConnectionError, OSError) as exc:
                last = str(exc)
            time.sleep(3)
        raise RuntimeError(f"NiFi did not become ready: {last}")

    # -- lookups --------------------------------------------------------

    def bundle_for(self, kind: str, type_name: str) -> dict[str, str]:
        """Return the bundle of an installed component type."""
        if not self._types:
            for endpoint, key in (
                ("/flow/processor-types", "processorTypes"),
                ("/flow/controller-service-types", "controllerServiceTypes"),
                ("/flow/reporting-task-types", "reportingTaskTypes"),
            ):
                for entry in self.get(endpoint)[key]:
                    self._types[entry["type"]] = entry["bundle"]
        try:
            return self._types[type_name]
        except KeyError:
            raise RuntimeError(f"{kind} type {type_name} is not installed") from None

    @property
    def root_id(self) -> str:
        return str(self.get("/process-groups/root")["id"])

    # -- canvas building ------------------------------------------------

    def create_group(self, parent_id: str, name: str, x: float = 0, y: float = 0) -> str:
        entity = self.post(
            f"/process-groups/{parent_id}/process-groups",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {"name": name, "position": {"x": x, "y": y}},
            },
        )
        return str(entity["id"])

    def set_variables(self, group_id: str, variables: dict[str, str]) -> None:
        current = self.get(f"/process-groups/{group_id}/variable-registry")
        self.put(
            f"/process-groups/{group_id}/variable-registry",
            {
                "processGroupRevision": current["processGroupRevision"] | {"clientId": CLIENT_ID},
                "variableRegistry": {
                    "processGroupId": group_id,
                    "variables": [
                        {"variable": {"name": name, "value": value}}
                        for name, value in variables.items()
                    ],
                },
            },
        )

    def create_processor(
        self,
        group_id: str,
        type_name: str,
        name: str,
        properties: dict[str, str | None] | None = None,
        *,
        x: float = 0,
        y: float = 0,
        config: dict[str, Any] | None = None,
        auto_terminate: list[str] | None = None,
        comments: str = "",
    ) -> dict[str, Any]:
        entity = self.post(
            f"/process-groups/{group_id}/processors",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {
                    "type": type_name,
                    "bundle": self.bundle_for("processor", type_name),
                    "name": name,
                    "position": {"x": x, "y": y},
                },
            },
        )
        update: dict[str, Any] = {"comments": comments}
        if properties:
            update["properties"] = properties
        if auto_terminate:
            update["autoTerminatedRelationships"] = auto_terminate
        if config:
            update.update(config)
        return self.put(
            f"/processors/{entity['id']}",
            {
                "revision": entity["revision"] | {"clientId": CLIENT_ID},
                "component": {"id": entity["id"], "config": update},
            },
        )

    def create_controller_service(
        self,
        group_id: str,
        type_name: str,
        name: str,
        properties: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        entity = self.post(
            f"/process-groups/{group_id}/controller-services",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {
                    "type": type_name,
                    "bundle": self.bundle_for("controller service", type_name),
                    "name": name,
                },
            },
        )
        if not properties:
            return entity
        return self.put(
            f"/controller-services/{entity['id']}",
            {
                "revision": entity["revision"] | {"clientId": CLIENT_ID},
                "component": {"id": entity["id"], "properties": properties},
            },
        )

    def create_reporting_task(
        self, type_name: str, name: str, properties: dict[str, str | None] | None = None
    ) -> dict[str, Any]:
        entity = self.post(
            "/controller/reporting-tasks",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {
                    "type": type_name,
                    "bundle": self.bundle_for("reporting task", type_name),
                    "name": name,
                },
            },
        )
        if not properties:
            return entity
        return self.put(
            f"/reporting-tasks/{entity['id']}",
            {
                "revision": entity["revision"] | {"clientId": CLIENT_ID},
                "component": {"id": entity["id"], "properties": properties},
            },
        )

    def create_parameter_context(
        self,
        name: str,
        parameters: dict[str, str],
        inherits: list[str] | None = None,
        description: str = "",
    ) -> str:
        entity = self.post(
            "/parameter-contexts",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {
                    "name": name,
                    "description": description,
                    "parameters": [
                        {"parameter": {"name": k, "value": v, "sensitive": False}}
                        for k, v in parameters.items()
                    ],
                    "inheritedParameterContexts": [
                        {"id": ctx_id, "component": {"id": ctx_id}} for ctx_id in (inherits or [])
                    ],
                },
            },
        )
        return str(entity["id"])

    def assign_parameter_context(self, group_id: str, context_id: str) -> None:
        entity = self.get(f"/process-groups/{group_id}")
        self.put(
            f"/process-groups/{group_id}",
            {
                "revision": entity["revision"] | {"clientId": CLIENT_ID},
                "component": {"id": group_id, "parameterContext": {"id": context_id}},
            },
        )

    def connect(
        self,
        group_id: str,
        source: dict[str, Any],
        destination: dict[str, Any],
        relationships: list[str],
        *,
        source_type: str = "PROCESSOR",
        destination_type: str = "PROCESSOR",
        **extra: Any,
    ) -> dict[str, Any]:
        """Connect two components. ``extra`` are ConnectionDTO fields (name, bends, ...)."""
        return self.post(
            f"/process-groups/{group_id}/connections",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {
                    "source": {
                        "id": source["id"],
                        "groupId": source["component"]["parentGroupId"],
                        "type": source_type,
                    },
                    "destination": {
                        "id": destination["id"],
                        "groupId": destination["component"]["parentGroupId"],
                        "type": destination_type,
                    },
                    "selectedRelationships": relationships,
                    **extra,
                },
            },
        )

    def create_port(self, group_id: str, kind: str, name: str) -> dict[str, Any]:
        return self.post(
            f"/process-groups/{group_id}/{kind}-ports",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {"name": name, "position": {"x": 0, "y": 0}},
            },
        )

    def create_funnel(self, group_id: str) -> dict[str, Any]:
        return self.post(
            f"/process-groups/{group_id}/funnels",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {"position": {"x": 0, "y": 0}},
            },
        )

    def create_label(self, group_id: str, text: str) -> dict[str, Any]:
        return self.post(
            f"/process-groups/{group_id}/labels",
            {
                "revision": {"version": 0, "clientId": CLIENT_ID},
                "component": {"label": text, "position": {"x": 0, "y": 0}},
            },
        )

    def create_template(
        self,
        group_id: str,
        name: str,
        processors: list[dict[str, Any]],
        **others: list[dict[str, Any]],
    ) -> str:
        """Template of the given processors plus, by snippet key, other entities of
        the same group (connections, funnels, labels, inputPorts, outputPorts,
        processGroups)."""

        def revisions(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
            return {
                e["id"]: {"version": e["revision"]["version"], "clientId": CLIENT_ID}
                for e in entities
            }

        snippet = self.post(
            "/snippets",
            {
                "snippet": {
                    "parentGroupId": group_id,
                    "processors": revisions(processors),
                    **{key: revisions(entities) for key, entities in others.items()},
                }
            },
        )
        template = self.post(
            f"/process-groups/{group_id}/templates",
            {
                "name": name,
                "description": f"Fixture template {name}",
                "snippetId": snippet["snippet"]["id"],
            },
        )
        return str(template["template"]["id"])

    # -- export ---------------------------------------------------------

    def download_definition(self, group_id: str) -> bytes:
        return bytes(self.request("GET", f"/process-groups/{group_id}/download", raw=True))

    def download_template(self, template_id: str) -> bytes:
        return bytes(self.request("GET", f"/templates/{template_id}/download", raw=True))


# ---------------------------------------------------------------------------
# Fixture groups. Each function builds one top-level process group and returns
# its id. Names are stable so the golden files can reference them.
# ---------------------------------------------------------------------------

STD = "org.apache.nifi.processors.standard."


def build_variables(nifi: NiFi, root: str) -> str:
    """Variables at three nesting levels, referenced from properties with every EL scope."""
    top = nifi.create_group(root, "Variables", x=0, y=0)
    nifi.set_variables(
        top,
        {
            "host": "example.org",
            "port": "8080",
            "shared": "from-top",
            "unused.var": "never referenced",
            "bad name!": "characters not allowed in parameter names",
        },
    )
    # VARIABLE_REGISTRY scope: safe to convert
    nifi.create_processor(
        top,
        STD + "ListenHTTP",
        "Listen on variable port",
        {"Listening Port": "${port}", "Base Path": "hooks"},
        x=0,
        y=0,
    )
    # VARIABLE_REGISTRY scope, reference inside a larger value
    gen = nifi.create_processor(
        top,
        STD + "GenerateFlowFile",
        "Generate with host text",
        {"generate-ff-custom-text": "host=${host} port=${port}"},
        x=0,
        y=200,
    )
    # FLOWFILE_ATTRIBUTES scope: a FlowFile attribute named host would shadow the variable
    put = nifi.create_processor(
        top,
        STD + "PutFile",
        "Put to shadowed directory",
        {"Directory": "/tmp/${host}/out"},
        x=0,
        y=400,
        auto_terminate=["success", "failure"],
    )
    nifi.connect(top, gen, put, ["success"])
    # dynamic property with FLOWFILE_ATTRIBUTES scope
    nifi.create_processor(
        top,
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        "Build URL attribute",
        {"url": "http://${host}:${port}/api"},
        x=400,
        y=0,
        auto_terminate=["success"],
    )
    # no EL support: ${host} here was never evaluated
    nifi.create_processor(
        top,
        STD + "LogAttribute",
        "Log literal dollar text",
        {"Attributes to Log": "${host}"},
        x=400,
        y=200,
        auto_terminate=["success"],
    )
    # EL function applied to a variable
    nifi.create_processor(
        top,
        STD + "TailFile",
        "Tail with function",
        {"File to Tail": "${shared:toUpper()}.log"},
        x=400,
        y=400,
        auto_terminate=["success"],
    )

    child = nifi.create_group(top, "Child", x=800, y=0)
    nifi.set_variables(child, {"path": "/data/in", "shared": "from-child"})
    nifi.create_processor(
        child,
        STD + "GetFile",
        "Get from variable path",
        {"Input Directory": "${path}"},
        auto_terminate=["success"],
    )
    # references the parent's variable
    nifi.create_processor(
        child,
        STD + "GenerateFlowFile",
        "Generate with parent host",
        {"generate-ff-custom-text": "${host}"},
        y=200,
    )

    grandchild = nifi.create_group(child, "Grandchild", x=0, y=400)
    # no variables of its own; references host (top) and path (child), and shadowed name
    nifi.create_processor(
        grandchild,
        STD + "GetFile",
        "Get from inherited path",
        {"Input Directory": "${path}/${shared}"},
        auto_terminate=["success"],
    )
    nifi.create_processor(
        grandchild,
        STD + "GenerateFlowFile",
        "Generate with inherited host",
        {"generate-ff-custom-text": "${host}"},
        y=200,
    )
    return top


def build_existing_context(nifi: NiFi, root: str) -> str:
    """A group that already has a parameter context, with a colliding variable name."""
    group = nifi.create_group(root, "Existing Context", x=0, y=600)
    base = nifi.create_parameter_context(
        "Base Context", {"env": "prod"}, description="Inherited by Fixture Context"
    )
    ctx = nifi.create_parameter_context(
        "Fixture Context", {"host": "param.example.org", "timeout": "30 sec"}, inherits=[base]
    )
    nifi.assign_parameter_context(group, ctx)
    nifi.set_variables(group, {"host": "variable.example.org", "retries": "3"})
    nifi.create_processor(
        group,
        STD + "InvokeHTTP",
        "Call with parameter",
        {"Remote URL": "https://#{host}/status", "Connection Timeout": "#{timeout}"},
        auto_terminate=["Response", "Retry", "No Retry", "Failure", "Original"],
    )
    nifi.create_processor(
        group,
        STD + "ListenHTTP",
        "Listen with variable",
        {"Listening Port": "${retries}081", "Base Path": "${host}"},
        y=200,
    )
    return group


def build_removed_components(nifi: NiFi, root: str) -> tuple[str, list[dict[str, Any]]]:
    """Processors, services and a reporting task that NiFi 2.x no longer ships."""
    group = nifi.create_group(root, "Removed Components", x=1200, y=0)
    processors = [
        nifi.create_processor(
            group,
            STD + "GetHTTP",
            "Fetch checksum",
            {"URL": "https://example.org/nifi.sha256", "Filename": "nifi.sha256"},
            x=0,
            y=0,
            auto_terminate=["success"],
        ),
        nifi.create_processor(
            group,
            STD + "PostHTTP",
            "Post to listener",
            {"URL": "http://localhost:8081/contentListener"},
            x=0,
            y=200,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "HashContent",
            "Hash content",
            {"Hash Attribute Name": "hash.value", "Hash Algorithm": "SHA-256"},
            x=0,
            y=400,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "HashAttribute",
            "Hash attribute",
            {"Hash Value Attribute Key": "label-hash", "label": ".*"},
            x=0,
            y=600,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "Base64EncodeContent",
            "Base64 encode",
            {"Mode": "Encode"},
            x=400,
            y=0,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "ListenTCPRecord",
            "Listen TCP records",
            {"port": "9999"},
            x=400,
            y=200,
            auto_terminate=["success"],
        ),
        nifi.create_processor(
            group,
            STD + "ParseCEF",
            "Parse CEF",
            None,
            x=400,
            y=400,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "ListenRELP",
            "Listen RELP",
            {"Port": "5170"},
            x=400,
            y=600,
            auto_terminate=["success"],
        ),
        nifi.create_processor(
            group,
            STD + "PutJMS",
            "Put JMS",
            {
                "JMS Provider": "ActiveMQ",
                "URL": "tcp://localhost:61616",
                "Destination Name": "queue.out",
                "Destination Type": "Queue",
            },
            x=800,
            y=0,
            auto_terminate=["success", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "GetJMSQueue",
            "Get JMS queue",
            {
                "JMS Provider": "ActiveMQ",
                "URL": "tcp://localhost:61616",
                "Destination Name": "queue.in",
            },
            x=800,
            y=200,
            auto_terminate=["success"],
        ),
        nifi.create_processor(
            group,
            STD + "ConvertJSONToSQL",
            "Convert JSON to SQL",
            {"Statement Type": "INSERT", "Table Name": "events"},
            x=800,
            y=400,
            auto_terminate=["original", "sql", "failure"],
        ),
        nifi.create_processor(
            group,
            STD + "EncryptContent",
            "Encrypt content",
            None,
            x=800,
            y=600,
            auto_terminate=["success", "failure"],
        ),
    ]
    # Components from other NARs, to make sure the catalog is not limited to the standard NAR.
    for type_name, name, props, x, y in (
        ("org.apache.nifi.processors.slack.PutSlack", "Post to Slack", None, 1200, 0),
        (
            "org.apache.nifi.processors.kafka.pubsub.ConsumeKafka_2_6",
            "Consume Kafka 2.6",
            None,
            1200,
            200,
        ),
        (
            "org.apache.nifi.processors.elasticsearch.PutElasticsearchHttp",
            "Put Elasticsearch HTTP",
            None,
            1200,
            400,
        ),
        (
            "org.apache.nifi.processors.azure.storage.PutAzureBlobStorage",
            "Put Azure blob (v8)",
            None,
            1200,
            600,
        ),
    ):
        try:
            processors.append(nifi.create_processor(group, type_name, name, props, x=x, y=y))
        except RuntimeError as exc:
            print(f"  skipped {type_name}: {exc}", file=sys.stderr)

    # A processor that still exists in 2.x, to check for false positives.
    processors.append(
        nifi.create_processor(
            group,
            "org.apache.nifi.processors.attributes.UpdateAttribute",
            "Still supported",
            {"kept": "yes"},
            x=1600,
            y=0,
            auto_terminate=["success"],
        )
    )
    # Bundle moved to nifi-jolt-nar in 2.x (NIFI-12554)
    nifi.create_processor(
        group,
        STD + "JoltTransformJSON",
        "Jolt transform",
        {"jolt-transform": "jolt-transform-shift", "jolt-spec": '{"a": "b"}'},
        x=1600,
        y=200,
        auto_terminate=["success", "failure"],
    )
    # Controller services renamed in 2.x (NIFI-13596)
    server = nifi.create_controller_service(
        group,
        "org.apache.nifi.distributed.cache.server.map.DistributedMapCacheServer",
        "Map cache server",
        {"Port": "4557"},
    )
    nifi.create_controller_service(
        group,
        "org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService",
        "Map cache client",
        {"Server Hostname": "localhost", "Server Port": "4557"},
    )
    del server
    # Reporting task removed in 2.x (NIFI-13507)
    try:
        nifi.create_reporting_task(
            "org.apache.nifi.reporting.prometheus.PrometheusReportingTask",
            "Prometheus metrics",
            {"prometheus-reporting-task-metrics-endpoint-port": "9092"},
        )
    except RuntimeError as exc:
        print(f"  skipped PrometheusReportingTask: {exc}", file=sys.stderr)
    return group, processors


def build_scripting(nifi: NiFi, root: str) -> str:
    """Scripted components using engines removed in 2.x, plus Groovy which remains."""
    group = nifi.create_group(root, "Scripting", x=1200, y=600)
    script_type = "org.apache.nifi.processors.script."
    cases = [
        ("ExecuteScript", "Jython script", "python", "flowFile = session.get()\n"),
        ("ExecuteScript", "ECMAScript script", "ECMAScript", "var ff = session.get();\n"),
        ("ExecuteScript", "Ruby script", "ruby", "ff = session.get\n"),
        ("ExecuteScript", "Lua script", "lua", "local ff = session:get()\n"),
        ("ExecuteScript", "Groovy script", "Groovy", "def ff = session.get()\n"),
        ("InvokeScriptedProcessor", "Scripted processor in Jython", "python", "class P: pass\n"),
        ("ScriptedTransformRecord", "Record transform in Groovy", "Groovy", "record\n"),
    ]
    for i, (type_name, name, engine, body) in enumerate(cases):
        nifi.create_processor(
            group,
            script_type + type_name,
            name,
            {"Script Engine": engine, "Script Body": body},
            x=(i % 3) * 400,
            y=(i // 3) * 200,
            auto_terminate=["success", "failure"]
            if type_name != "InvokeScriptedProcessor"
            else None,
        )
    nifi.create_controller_service(
        group,
        "org.apache.nifi.lookup.script.ScriptedLookupService",
        "Lookup in Jython",
        {"Script Engine": "python", "Script Body": "class L: pass\n"},
    )
    nifi.create_controller_service(
        group,
        "org.apache.nifi.record.script.ScriptedReader",
        "Reader in Groovy",
        {"Script Engine": "Groovy", "Script Body": "class R {}\n"},
    )
    return group


def build_scheduling(nifi: NiFi, root: str) -> str:
    """Event-driven scheduling and cron expressions that changed meaning in 2.x."""
    group = nifi.create_group(root, "Scheduling", x=2400, y=0)
    gen = nifi.create_processor(group, STD + "GenerateFlowFile", "Timer source", None)
    event = nifi.create_processor(
        group,
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        "Event driven",
        {"touched": "true"},
        y=200,
        auto_terminate=["success"],
        config={"schedulingStrategy": "EVENT_DRIVEN"},
    )
    nifi.connect(group, gen, event, ["success"])
    nifi.create_processor(
        group,
        STD + "GenerateFlowFile",
        "Cron with year field",
        None,
        x=400,
        config={"schedulingStrategy": "CRON_DRIVEN", "schedulingPeriod": "0 0 12 * * ? 2030"},
        auto_terminate=["success"],
    )
    nifi.create_processor(
        group,
        STD + "GenerateFlowFile",
        "Cron with numeric weekday",
        None,
        x=400,
        y=200,
        config={"schedulingStrategy": "CRON_DRIVEN", "schedulingPeriod": "0 0 6 ? * 1"},
        auto_terminate=["success"],
    )
    nifi.create_processor(
        group,
        STD + "GenerateFlowFile",
        "Cron unaffected",
        None,
        x=400,
        y=400,
        config={"schedulingStrategy": "CRON_DRIVEN", "schedulingPeriod": "0 0 6 ? * MON-FRI"},
        auto_terminate=["success"],
    )
    return group


def build_deprecated_properties(nifi: NiFi, root: str) -> str:
    """Properties deprecated in 1.x and dropped in 2.x on components that still exist."""
    group = nifi.create_group(root, "Deprecated Properties", x=2400, y=600)
    nifi.create_processor(
        group,
        STD + "InvokeHTTP",
        "Invoke through proxy",
        {
            "Remote URL": "https://example.org/",
            "Proxy Host": "proxy.example.org",
            "Proxy Port": "3128",
        },
        auto_terminate=["Response", "Retry", "No Retry", "Failure", "Original"],
    )
    return group


def build_custom_nar(nifi: NiFi, root: str) -> str:
    """Placeholder for a component from a third-party NAR; rewritten after export."""
    group = nifi.create_group(root, "Custom NAR", x=3600, y=0)
    nifi.create_processor(
        group,
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        CUSTOM_PROCESSOR_NAME,
        {"custom.property": "value"},
        auto_terminate=["success"],
        comments="Type and bundle rewritten by make_fixtures.py",
    )
    return group


def build_clean(nifi: NiFi, root: str) -> str:
    """Only components and features that work unchanged on 2.x. Expected: no findings."""
    group = nifi.create_group(root, "Clean", x=3600, y=600)
    ctx = nifi.create_parameter_context("Clean Context", {"directory": "/data/clean"})
    nifi.assign_parameter_context(group, ctx)
    inp = nifi.create_port(group, "input", "in")
    gen = nifi.create_processor(
        group, STD + "GenerateFlowFile", "Generate", {"File Size": "1 KB"}, y=200
    )
    upd = nifi.create_processor(
        group,
        "org.apache.nifi.processors.attributes.UpdateAttribute",
        "Tag",
        {"source": "${filename:toUpper()}"},
        y=400,
    )
    funnel = nifi.create_funnel(group)
    put = nifi.create_processor(
        group,
        STD + "PutFile",
        "Store",
        {"Directory": "#{directory}/${now():format('yyyy')}"},
        y=600,
        auto_terminate=["success", "failure"],
    )
    log = nifi.create_processor(
        group, STD + "LogAttribute", "Log", None, x=400, y=400, auto_terminate=["success"]
    )
    out = nifi.create_port(group, "output", "out")
    nifi.connect(group, inp, upd, [""], source_type="INPUT_PORT")
    nifi.connect(group, gen, upd, ["success"])
    nifi.connect(group, upd, funnel, ["success"], destination_type="FUNNEL")
    nifi.connect(group, funnel, put, [""], source_type="FUNNEL")
    nifi.connect(group, upd, log, ["success"])
    nifi.connect(group, log, out, ["success"], destination_type="OUTPUT_PORT")
    nifi.create_label(group, "Nothing here needs migration.")
    return group


def build_template_conversion(nifi: NiFi, root: str) -> tuple[str, dict[str, Any]]:
    """Everything a template can hold, using only components that exist on 2.x.

    Returns the group id and the entities to put in the template, keyed like a
    snippet. The template is created from the group's whole content.
    """
    group = nifi.create_group(root, "Template Conversion", x=4200, y=600)
    reader = nifi.create_controller_service(
        group,
        "org.apache.nifi.csv.CSVReader",
        "Reader",
        {"schema-access-strategy": "csv-header-derived"},
    )
    writer = nifi.create_controller_service(
        group, "org.apache.nifi.json.JsonRecordSetWriter", "Writer", {"Pretty Print JSON": "true"}
    )
    gen = nifi.create_processor(
        group,
        STD + "GenerateFlowFile",
        "Generate",
        {"generate-ff-custom-text": "a,b\n1,2", "File Size": "0B"},
        config={"schedulingPeriod": "1 min", "yieldDuration": "2 sec", "penaltyDuration": "45 sec"},
        comments="Seeds the flow",
    )
    convert = nifi.create_processor(
        group,
        STD + "ConvertRecord",
        "Convert",
        {"record-reader": reader["id"], "record-writer": writer["id"]},
        y=200,
        auto_terminate=["failure"],
        config={"concurrentlySchedulableTaskCount": 2, "bulletinLevel": "ERROR"},
    )
    funnel = nifi.create_funnel(group)
    done = nifi.create_processor(
        group, STD + "LogAttribute", "Done", None, x=400, y=600, auto_terminate=["success"]
    )
    label = nifi.create_label(group, "Converted from a template by flowport")

    sink = nifi.create_group(group, "Sink", y=400)
    sink_in = nifi.create_port(sink, "input", "in")
    log = nifi.create_processor(sink, STD + "LogAttribute", "Log", {"Log Level": "warn"}, y=200)
    sink_out = nifi.create_port(sink, "output", "out")
    nifi.connect(sink, sink_in, log, [""], source_type="INPUT_PORT")
    nifi.connect(sink, log, sink_out, ["success"], destination_type="OUTPUT_PORT")

    connections = [
        nifi.connect(
            group,
            gen,
            convert,
            ["success"],
            name="generated",
            backPressureObjectThreshold=500,
            backPressureDataSizeThreshold="2 GB",
            flowFileExpiration="1 hour",
            prioritizers=["org.apache.nifi.prioritizer.FirstInFirstOutPrioritizer"],
            bends=[{"x": 300.0, "y": 100.0}],
            labelIndex=1,
            zIndex=2,
        ),
        nifi.connect(group, convert, funnel, ["success"], destination_type="FUNNEL"),
        nifi.connect(
            group,
            funnel,
            {"id": sink_in["id"], "component": {"parentGroupId": sink}},
            [""],
            source_type="FUNNEL",
            destination_type="INPUT_PORT",
        ),
        nifi.connect(
            group,
            {"id": sink_out["id"], "component": {"parentGroupId": sink}},
            done,
            [""],
            source_type="OUTPUT_PORT",
            loadBalanceStrategy="ROUND_ROBIN",
            loadBalanceCompression="COMPRESS_ATTRIBUTES_ONLY",
        ),
    ]
    entities = {
        "processors": [gen, convert, done],
        "connections": connections,
        "funnels": [funnel],
        "labels": [label],
        "processGroups": [nifi.get(f"/process-groups/{sink}")],
    }
    return group, entities


# ---------------------------------------------------------------------------
# Docker and export
# ---------------------------------------------------------------------------


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def start_container(version: str, port: int) -> None:
    docker("rm", "-f", CONTAINER, check=False)
    docker(
        "run",
        "-d",
        "--name",
        CONTAINER,
        "-p",
        f"{port}:{port}",
        "-e",
        f"NIFI_WEB_HTTP_PORT={port}",
        "-e",
        "NIFI_WEB_HTTP_HOST=0.0.0.0",
        f"{IMAGE}:{version}",
    )


def stop_container() -> None:
    docker("rm", "-f", CONTAINER, check=False)


def rewrite_custom_processor(node: Any) -> int:
    """Rewrite the placeholder processor in place (any JSON layout). Returns count."""
    count = 0
    if isinstance(node, dict):
        if node.get("name") == CUSTOM_PROCESSOR_NAME and "bundle" in node and "type" in node:
            node["type"] = CUSTOM_TYPE
            node["bundle"] = dict(CUSTOM_BUNDLE)
            count += 1
        for value in node.values():
            count += rewrite_custom_processor(value)
    elif isinstance(node, list):
        for value in node:
            count += rewrite_custom_processor(value)
    return count


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=False) + "\n")


def export(nifi: NiFi, out_dir: Path, groups: dict[str, str], templates: dict[str, str]) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "definitions").mkdir(parents=True)
    (out_dir / "templates").mkdir()

    for name, group_id in groups.items():
        data = json.loads(nifi.download_definition(group_id))
        rewrite_custom_processor(data)
        write_json(out_dir / "definitions" / f"{name}.json", data)
        print(f"  definitions/{name}.json")

    for name, template_id in templates.items():
        (out_dir / "templates" / f"{name}.xml").write_bytes(nifi.download_template(template_id))
        print(f"  templates/{name}.xml")

    # NiFi writes flow.json.gz shortly after the last change.
    time.sleep(10)
    tmp = out_dir / "flow.raw.json.gz"
    docker("cp", f"{CONTAINER}:{FLOW_PATH_IN_CONTAINER}", str(tmp))
    with gzip.open(tmp, "rb") as fh:
        flow = json.load(fh)
    tmp.unlink()
    rewritten = rewrite_custom_processor(flow)
    if rewritten != 1:
        raise RuntimeError(
            f"expected to rewrite 1 custom processor in flow.json.gz, got {rewritten}"
        )
    write_json(out_dir / "flow.json", flow)
    with gzip.GzipFile(out_dir / "flow.json.gz", "wb", mtime=0) as gz:
        gz.write(json.dumps(flow, indent=2).encode("utf-8"))
    print("  flow.json, flow.json.gz")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--nifi-version", default=DEFAULT_VERSION)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--url", help="reuse a running NiFi instead of starting Docker")
    parser.add_argument("--keep", action="store_true", help="leave the container running")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = (
        args.out
        or Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / f"nifi-{args.nifi_version}"
    )
    base_url = args.url or f"http://localhost:{args.port}/nifi-api"

    if not args.url:
        print(f"starting {IMAGE}:{args.nifi_version} as {CONTAINER}")
        start_container(args.nifi_version, args.port)
    nifi = NiFi(base_url)
    print("waiting for NiFi")
    nifi.wait_ready()
    root = nifi.root_id
    print("building fixtures")
    groups: dict[str, str] = {}
    groups["variables"] = build_variables(nifi, root)
    groups["existing-context"] = build_existing_context(nifi, root)
    removed_group, removed_processors = build_removed_components(nifi, root)
    groups["removed-components"] = removed_group
    groups["scripting"] = build_scripting(nifi, root)
    groups["scheduling"] = build_scheduling(nifi, root)
    groups["deprecated-properties"] = build_deprecated_properties(nifi, root)
    groups["custom-nar"] = build_custom_nar(nifi, root)
    groups["clean"] = build_clean(nifi, root)
    conversion_group, conversion_entities = build_template_conversion(nifi, root)
    groups["template-conversion"] = conversion_group
    templates = {
        "removed-components": nifi.create_template(
            removed_group, "Removed Components Template", removed_processors[:4]
        ),
        "template-conversion": nifi.create_template(
            conversion_group, "Template Conversion Template", **conversion_entities
        ),
    }
    print(f"exporting to {out_dir}")
    export(nifi, out_dir, groups, templates)
    if not args.url and not args.keep:
        stop_container()
    return 0


if __name__ == "__main__":
    sys.exit(main())
