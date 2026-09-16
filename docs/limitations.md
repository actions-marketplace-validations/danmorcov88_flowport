# Known limitations

## Inputs and versions

- The catalog covers NiFi 1.28.1 to 2.12.0. Flows from older 1.x releases
  are analyzed with a warning; component types that only existed before
  1.28.1 are reported as unknown. Upgrade to 1.28.x first, as the Apache
  migration guidance recommends.
- `flow.xml.gz` is not read. Start the last 1.x release once to get
  `flow.json.gz`.
- Sensitive property values are encrypted in `flow.json.gz` and absent from
  flow definitions and templates. flowport carries them over as they are; a
  `${variable}` inside a sensitive value cannot be seen and is not migrated.
- Cloudera-specific components are not in the catalog.

## Analyzer

- Properties renamed between releases are not reported: NiFi 2.x migrates
  most of them when the flow loads (InvokeHTTP proxy settings, for example).
  Property-level removals on components that still exist are not covered.
- Registry clients and parameter providers in `flow.json` are not analyzed.
- A component from a third-party NAR is reported MANUAL (the fix is a NAR
  built for the 2.x API, not a flow change), although it loads as a ghost.

## migrate variables

- Only references the analyzer marks `NIFI2-VARIABLE-REFERENCE` are
  rewritten: a property that evaluates the Variable Registry only, with no
  Expression Language function on the variable. Attribute-scope references,
  functions and sensitive properties stay findings.
- A group that already has a parameter context gets parameters added only
  when no name collides; a collision leaves the whole group unchanged.
- When an existing parameter context sits between the group that defines a
  variable and the group that uses it, the reference is reported instead of
  rewritten (flowport does not edit the inheritance of a context you own).
- Some NiFi validators skip their check while a value contains `${...}` and
  validate the literal once `#{...}` is substituted; a migrated component can
  become invalid on 1.x where the configured value was wrong all along.

## migrate templates

- A converted template carries no `controllerServiceApis` and no property
  `displayName`s; NiFi fills both in on import.
- Templates whose processors reference controller services of a parent
  group keep the reference id; the service is not part of the template.
- The templates stay in the migrated flow; NiFi 2.x drops them itself.

## migrate components

- Only the replacements in `replacements.yaml` are applied. Components with
  a documented successor that is not 1:1 (HashContent, HashAttribute, JMS,
  Slack, Kafka `_1_0`/`_2_0`, Hive, Azure `_v12`, Elasticsearch) stay
  findings with the successor named in the suggestion.
- A replaced processor keeps its connections; relationships that the new
  component has and the old one did not are auto-terminated as the mapping
  says. Check them.
- The cron year field is reported, not removed: removing it changes when the
  processor runs.

## validate

- The flow is imported as a process group, so controller-level services and
  reporting tasks of a `flow.json` are not validated (they are listed as
  warnings). Put the migrated `flow.json.gz` into `conf/` of a 2.x instance
  to validate everything.
- `--docker` needs a Docker engine on the machine; it does not work inside
  the flowport Docker image.
- NiFi 2.x creates a `StandardProxyConfigurationService` while importing an
  InvokeHTTP with deprecated proxy properties; flowport disables it before
  removing the validation group.
