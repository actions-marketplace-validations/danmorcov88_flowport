# Quickstart: from a NiFi 1.x flow to NiFi 2.x

This walks through a migration end to end with flowport. Every step works
on files; nothing touches your NiFi until you choose to validate.

## 1. Get the flow

Upgrade the source instance to the last 1.x release (1.28.x) and start it
once, so that it writes `conf/flow.json.gz`. Copy that file. flowport does
not read `flow.xml.gz`, following the
[Apache migration guidance](https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance).

An exported flow definition (`Download flow definition` on a process group,
or a NiFi Registry snapshot) works as well; the file type is detected from
the content.

## 2. Install flowport

```bash
pipx install flowport            # or: python -m pip install flowport
flowport --version
```

Or without installing anything:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/danmorcov88/flowport analyze /work/flow.json.gz
```

## 3. See what will break

```bash
flowport analyze flow.json.gz
flowport analyze flow.json.gz --format html --output report.html   # to share
```

The report lists every finding with severity, component path, message,
suggestion, the Apache source and a link to the
[rule reference](rules.md). Severities:

- **BLOCKER**: NiFi 2.x does not start, or the component loads as an
  invalid ghost.
- **MANUAL**: needs a decision; flowport will not change it.
- **AUTO_FIXABLE**: a `migrate` command fixes it.
- **INFO**: works, but worth knowing.

Exit code 1 when a BLOCKER exists (`--fail-on` changes the threshold), so
the command fits a CI pipeline:

```yaml
- uses: danmorcov88/flowport@v1
  with:
    file: conf/flow.json.gz
    command: analyze --fail-on blocker
```

## 4. Migrate what can be migrated safely

```bash
flowport migrate all flow.json.gz --output migrated/
```

This runs, in order:

1. `migrate variables`: process group variables become parameter contexts
   (one per group, inheriting from the nearest ancestor), `${name}` becomes
   `#{name}` where that cannot change behavior.
2. `migrate components`: documented 1:1 replacements (GetHTTP and PostHTTP
   to InvokeHTTP, Base64EncodeContent to EncodeContent, the Jolt processors
   and cache services under their new names) and the event-driven scheduling
   fix, without which NiFi 2.x does not start.
3. `migrate templates`: every template becomes a flow definition under
   `migrated/templates/`, ready for "Upload flow definition".

`migrated/` holds the migrated `flow.json.gz`, `changes.json` with every
edit (old and new value, component, path) and `report.md` with the findings
that remain. Each step is also available on its own; `--dry-run` shows the
changes without writing.

The migrated flow is still a NiFi 1.x flow. Put it into `conf/` of a 1.x
instance first if you want to check it where the variables and the old
components still work.

## 5. Read the remaining findings

What stays in `report.md` needs a decision: a variable used with Expression
Language functions, a variable referenced from a property that also reads
FlowFile attributes, a component without a documented 1:1 successor, a
script engine that 2.x no longer ships. Each finding says what to do and
links to the Apache source.

## 6. Validate on NiFi 2.x

```bash
flowport validate migrated/flow.json.gz --docker        # throwaway apache/nifi:2.12.0
flowport validate migrated/flow.json.gz --nifi-url https://nifi2.example.org:8443/nifi-api \
    --username admin --password '...' --insecure
```

The flow is imported as a process group, NiFi validates every component,
flowport prints the invalid ones with NiFi's own messages and removes the
group again (`--keep` leaves it in place). Exit code 1 when something is
invalid or a type is missing.

## 7. Upgrade

Put `migrated/flow.json.gz` into `conf/` of the NiFi 2.x installation
(`nifi.sensitive.props.key` must be set, as on 1.x), start it, and upload
the converted templates where you need them.
