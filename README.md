# flowport

A migration tool for Apache NiFi: analyze NiFi 1.x flows for NiFi 2.x
incompatibilities and apply the changes that can be made safely.

flowport works offline on files. It never modifies its input. Every finding
points to the exact component (process group path, name, id) and links to the
official Apache source (wiki page or JIRA issue) that documents the change.

> Status: v0.4. `analyze` reports incompatibilities; `migrate variables`
> converts process group variables to parameter contexts; `migrate templates`
> converts templates to flow definitions; `migrate components` applies the
> documented component replacements. See "Roadmap" for v1.0.

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

### Migrating variables

```bash
flowport migrate variables flow.json.gz --output migrated/flow.json.gz
flowport migrate variables flow.json.gz --output migrated/flow.json.gz --dry-run   # show changes only
flowport migrate variables flow.json.gz --output migrated/flow.json.gz --keep-unused
```

NiFi 2.x removed the Variable Registry and silently drops process group
variables when a flow loads. `migrate variables` converts them to parameter
contexts and writes three files next to the output: the migrated flow (same
kind as the input, gzip when the name ends in `.gz`), `changes.json` with
every edit, and `report.md` with the findings that remain.

The output is still a NiFi 1.x flow. Load it on your 1.x instance, check it,
then upgrade. What the command does:

1. **One parameter context per process group that defines variables**, named
   `<group name> Variables` (a number is appended when the name is taken). A
   context inherits from the nearest ancestor context, so a variable that a
   child group used from its parent keeps resolving. Groups that only use
   inherited variables are assigned the nearest ancestor context, because a
   process group does not inherit its parent's context on its own.
2. **A group that already has a parameter context** gets the parameters added
   to that context, unless a variable name collides with a parameter the
   context resolves (its own or inherited). On a collision nothing changes in
   that group and `NIFI2-VARIABLE-PARAMETER-COLLISION` is reported.
3. **`${name}` becomes `#{name}` only where that cannot change behavior**: the
   property evaluates Expression Language against the Variable Registry only
   (not FlowFile attributes, which take priority over variables on 1.x), the
   reference has no functions, the property is not sensitive, and the parameter
   resolves through the group's context. Only the `${name}` span is replaced;
   `${host}:8080` becomes `#{host}:8080`. Everything else stays a finding
   (`...-ATTRIBUTE-SCOPE`, `...-FUNCTION`, `...-SENSITIVE`, `...-NOT-VISIBLE`),
   with the parameter already created so the manual edit is a one-liner.
4. **Properties without Expression Language support are left alone**: the
   text was never evaluated.
5. **Variable names that are not valid parameter names** (anything but letters,
   digits, `-`, `_`, `.` and space) are not migrated.
6. **Unused variables** are reported and skipped unless `--keep-unused`.

A variable is removed from the group only once every evaluated reference to
it was rewritten. A variable with a reference that needs a decision stays, so
the flow keeps working on 1.x; the report shows what is left. Parameters are
created non-sensitive, since variables never are.

One thing to expect on 1.x: some validators (for example "directory exists")
skip the check while a value contains `${...}` and validate the literal once a
parameter is substituted, so a migrated component can become invalid where the
configured value was wrong all along.

Same input, same output: generated identifiers derive from the source group
id, and every list is ordered, so the command can run in CI and the result can
be diffed.

### Migrating templates

```bash
flowport migrate templates flow.json.gz --output migrated/templates/   # every template in the flow
flowport migrate template my-template.xml --output my-flow.json        # one exported XML template
```

NiFi 2.x removed templates and drops them when a flow loads; the replacement
is the flow definition, the JSON behind "Download flow definition" and
"Upload flow definition". `migrate templates` converts every template stored
in `flow.json` (they are kept there as JSON, the XML is not needed) and writes
`<template>.json` plus `<template>.report.md` per template; `migrate template`
converts one exported `.xml` file. Both accept the same templates: the XML
export and the copy inside `flow.json` give byte-identical definitions.

Everything a template can hold is converted: processors with their scheduling
and properties, controller services (references from processors keep
resolving), input and output ports, funnels, labels, connections with back
pressure, prioritizers, bends and load balancing, nested process groups and
remote process groups with their ports. The analyzer runs on each result, so
the report tells you what in the template still needs work (a removed
processor imports as a ghost, exactly as the report says).

Import the definition on 2.x with "Upload flow definition" on the canvas, or
on 1.x first to check it. The conversion was verified by importing the
fixture templates into a real NiFi 2.12.0 through the REST API; see
[docs/dev/templates-conversion.md](docs/dev/templates-conversion.md).

### Replacing components

```bash
flowport migrate components flow.json.gz --output migrated/flow.json.gz
flowport migrate components flow.json.gz --output migrated/flow.json.gz --dry-run
```

Applies only the 1:1 replacements that the Apache migration guide or a NiFi
JIRA documents, listed in `src/flowport/catalog/replacements.yaml` with their
property, value and relationship mappings. A component keeps its id, name,
comments, position and connections; connections and auto-terminated
relationships are remapped. Also switches processors scheduled `EVENT_DRIVEN`
to `TIMER_DRIVEN` (run schedule 0 sec), because NiFi 2.x refuses to start
with that strategy in the flow.

| Replaced | By | Notes |
|---|---|---|
| Base64EncodeContent | EncodeContent | `Encoding` set to `base64` |
| GetHTTP | InvokeHTTP | `HTTP Method: GET`, `Response FlowFile Naming Strategy: URL_PATH`; `success` becomes `Response`, the request-side relationships are auto-terminated |
| PostHTTP | InvokeHTTP | `HTTP Method: POST`; `success` becomes `Original`, `failure` becomes `Failure, Retry, No Retry`; compression level maps to `Request Content-Encoding`. Not replaced when `Send as FlowFile` is true (MANUAL) |
| JoltTransformJSON, JoltTransformRecord | same names in `nifi-jolt-nar` | property names changed with the move |
| DistributedMapCacheServer, DistributedMapCacheClientService | MapCacheServer, MapCacheClientService | processors keep referencing the same instance |
| DistributedSetCacheServer, DistributedSetCacheClientService | SetCacheServer, SetCacheClientService | |

Every replacement is reported (`NIFI2-COMPONENT-REPLACED`, with what to
check), a property without an equivalent is reported when it was set
(`NIFI2-REPLACED-PROPERTY-DROPPED`), and a replacement that cannot be applied
safely is reported instead of applied (`NIFI2-REPLACEMENT-SKIPPED`).
Components without a documented 1:1 successor (HashContent, HashAttribute,
the JMS and Slack processors, ...) stay findings. Each mapping is checked
against the extension manifests of both releases and verified by importing
the migrated fixture into a real NiFi 2.12.0; see
[docs/dev/component-replacements.md](docs/dev/component-replacements.md).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No finding at or above the `--fail-on` threshold (`analyze`); output written (`migrate`) |
| 1 | At least one finding at or above the threshold (`analyze`) |
| 2 | Input or internal error |

### Severities

| Severity | Meaning |
|---|---|
| BLOCKER | NiFi 2.x does not start, or the component loads as an invalid ghost |
| MANUAL | Needs a human decision; the tool will not change it |
| AUTO_FIXABLE | A `flowport migrate` command can fix it safely |
| INFO | Works, but worth knowing |

The severities were established by loading the test fixtures into a real
NiFi 2.12.0 instance; see
[docs/dev/nifi2-load-experiment.md](docs/dev/nifi2-load-experiment.md). The
variables migration was verified on a real NiFi 1.28.1
([docs/dev/variables-migration.md](docs/dev/variables-migration.md)) and the
template conversion on a real NiFi 2.12.0
([docs/dev/templates-conversion.md](docs/dev/templates-conversion.md)), as
were the component replacements
([docs/dev/component-replacements.md](docs/dev/component-replacements.md)).

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
| NIFI2-VARIABLE-REFERENCE-SENSITIVE | MANUAL | `${var}` in a sensitive property (only a sensitive parameter may be referenced there) |
| NIFI2-VARIABLE-PARAMETER-COLLISION | MANUAL | Variable name already resolved by the group's parameter context (reported by `migrate variables`) |
| NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE | MANUAL | Parameter created for the variable is not visible through the group's own context (reported by `migrate variables`) |
| NIFI2-VARIABLE-UNUSED | INFO | Variable that nothing references |
| NIFI2-VARIABLE-NAME | MANUAL | Variable name that is not a valid parameter name |
| NIFI2-TEMPLATE | MANUAL | Template stored in the flow (dropped silently by 2.x) |
| NIFI2-INVOKEHTTP-PROXY-PROPERTIES | INFO | Deprecated proxy properties that NiFi 2.x migrates on load |
| NIFI2-COMPONENT-REPLACED | INFO | Component replaced by `migrate components`, with what to check |
| NIFI2-REPLACED-PROPERTY-DROPPED | INFO | Property that was set but has no equivalent on the replacement |
| NIFI2-REPLACEMENT-SKIPPED | MANUAL | Documented replacement not applied because a property makes it unsafe |
| NIFI2-SCHEDULING-CHANGED | INFO | EVENT_DRIVEN switched to TIMER_DRIVEN by `migrate components` |

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
- A converted template has no `controllerServiceApis` and no property
  `displayName`s (templates do not carry them); NiFi fills both in on import.
- `migrate templates` leaves the templates in the flow; NiFi 2.x drops them
  itself. Convert them first, then upgrade.

## Roadmap

- v1.0: validation against a running NiFi 2.x, PyPI and Docker packaging,
  GitHub Action.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy
pytest                 # unit and golden tests
pytest -m slow         # large generated input (about 40 s)
pytest -m integration  # migrated flow into NiFi 1.28.1, templates and replacements into 2.12.0 (Docker)
pytest --update-golden # refresh expected reports on purpose
```

Fixtures are generated from a real NiFi 1.28.1 instance in Docker by
`tools/make_fixtures.py`; see [tests/fixtures/README.md](tests/fixtures/README.md).
See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a rule.

## License

Apache License 2.0. This project is not affiliated with or endorsed by the
Apache Software Foundation. Apache NiFi is a trademark of the Apache Software
Foundation.
