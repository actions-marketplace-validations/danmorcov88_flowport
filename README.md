# flowport

A migration tool for Apache NiFi: analyze NiFi 1.x flows for NiFi 2.x
incompatibilities and, in later versions, apply the changes that can be made
safely.

flowport works offline on files. It never modifies its input. Every finding
points to the exact component (process group path, name, id) and links to the
official Apache source (wiki page or JIRA issue) that documents the change.

> Status: v0.1, analyzer only. Automatic migration of variables, templates and
> component replacements is planned. See "Roadmap".

## Install

Python 3.11 or newer.

```bash
pipx install git+https://github.com/danmorcov88/flowport
# or, inside a virtual environment
python -m pip install git+https://github.com/danmorcov88/flowport
```

## Usage

```bash
flowport analyze flow.json.gz                       # summary and details in the terminal
flowport analyze flow.json.gz -f json -o report.json
flowport analyze flow.json.gz -f html -o report.html
flowport analyze flow.json.gz -f markdown -o report.md
flowport analyze flow.json.gz --fail-on blocker     # for CI (default threshold)
```

Input can be `flow.json.gz` or `flow.json` from `conf/` of a NiFi 1.x
instance (NiFi 1.16 or later), or a flow definition JSON exported from the
canvas or from NiFi Registry. The type is detected from the content, not the
file name. `flow.xml.gz` is not supported: start the last 1.x release once so
that it converts the flow to `flow.json.gz`, as the
[Apache migration guidance](https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance)
recommends.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No finding at or above the `--fail-on` threshold |
| 1 | At least one finding at or above the threshold |
| 2 | Input or internal error |

### Severities

| Severity | Meaning |
|---|---|
| BLOCKER | NiFi 2.x does not start, or the component loads as an invalid ghost |
| MANUAL | Needs a human decision; the tool will not change it |
| AUTO_FIXABLE | A later `flowport migrate` command can fix it safely |
| INFO | Works, but worth knowing |

The severities were established by loading the test fixtures into a real
NiFi 2.12.0 instance; see
[docs/dev/nifi2-load-experiment.md](docs/dev/nifi2-load-experiment.md).

## What it checks (NiFi 1.28.1 to 2.12.0)

| Rule | Severity | Detects |
|---|---|---|
| NIFI2-REMOVED-COMPONENT | BLOCKER | Processor, controller service or reporting task type that no longer exists (161 types, generated from the NAR manifests of both releases) |
| NIFI2-RENAMED-COMPONENT | BLOCKER | Type that moved to another package and bundle (for example JoltTransformJSON) |
| NIFI2-THIRD-PARTY-BUNDLE | MANUAL | Component from a non-Apache NAR, which must be rebuilt for the 2.x API |
| NIFI2-OPTIONAL-BUNDLE | MANUAL | Component whose NAR is not in the default 2.x distribution (for example the Hadoop bundle) |
| NIFI2-UNKNOWN-COMPONENT | INFO | Apache type not found in the 1.28.1 catalog |
| NIFI2-DEPRECATED-IN-TARGET | INFO | Type deprecated in 2.x |
| NIFI2-SCRIPT-ENGINE | BLOCKER | Scripted component using Jython, ECMAScript, Ruby or Lua |
| NIFI2-EVENT-DRIVEN | BLOCKER | Event-driven scheduling (NiFi 2.x refuses to start) |
| NIFI2-CRON-YEAR-FIELD | BLOCKER | Seven-field cron expression |
| NIFI2-CRON-NUMERIC-DAY-OF-WEEK | MANUAL | Numeric day of week (Quartz and Spring count differently) |
| NIFI2-VARIABLES-DEFINED | AUTO_FIXABLE | Process group with variables (dropped silently by 2.x) |
| NIFI2-VARIABLE-REFERENCE | AUTO_FIXABLE | `${var}` in a property that evaluates variables only |
| NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE | MANUAL | `${var}` in a property that also reads FlowFile attributes |
| NIFI2-VARIABLE-REFERENCE-FUNCTION | MANUAL | `${var:function()}` |
| NIFI2-VARIABLE-REFERENCE-NO-EL | INFO | `${var}` in a property without Expression Language |
| NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE | MANUAL | `${var}` in a property of an unknown component type |
| NIFI2-VARIABLE-UNUSED | INFO | Variable that nothing references |
| NIFI2-VARIABLE-NAME | MANUAL | Variable name that is not a valid parameter name |
| NIFI2-TEMPLATE | MANUAL | Template stored in the flow (dropped silently by 2.x) |
| NIFI2-INVOKEHTTP-PROXY-PROPERTIES | INFO | Deprecated proxy properties that NiFi 2.x migrates on load |

Rules are data: the component inventory in
`src/flowport/catalog/generated/` is produced by `tools/build_catalog.py` from
the extension manifests of every NAR of both releases, and the hand-written
rules in `src/flowport/catalog/manual.yaml` each cite an Apache source.

## Known limitations

- The catalog covers NiFi 1.28.1 to 2.12.0. Flows from older 1.x releases are
  analyzed with a warning; upgrade to 1.28.1 first for accurate results.
- Properties renamed between releases are not reported: NiFi 2.x migrates
  most of them itself when the flow loads.
- `nifi.properties`, `authorizers.xml` and other configuration files are out
  of scope.
- Cloudera-specific components are not covered.

## Roadmap

- v0.2: `flowport migrate variables` converts process group variables to
  parameter contexts.
- v0.3: `flowport migrate templates` converts XML templates to flow definitions.
- v0.4: `flowport migrate components` applies documented 1:1 replacements.
- v1.0: validation against a running NiFi 2.x, PyPI and Docker packaging,
  GitHub Action.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy
pytest                 # unit and golden tests
pytest -m slow         # large generated input (about 40 s)
pytest --update-golden # refresh expected reports on purpose
```

Fixtures are generated from a real NiFi 1.28.1 instance in Docker by
`tools/make_fixtures.py`; see [tests/fixtures/README.md](tests/fixtures/README.md).
See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a rule.

## License

Apache License 2.0. This project is not affiliated with or endorsed by the
Apache Software Foundation. Apache NiFi is a trademark of the Apache Software
Foundation.
