# Prior art

Survey of existing tooling for migrating Apache NiFi 1.x flows to NiFi 2.x.
Checked on 2026-09-16 across GitHub, PyPI, the Apache NiFi wiki and the NiFi
toolkit documentation.

## Open source

| Tool | Link | What it covers | Last commit |
|---|---|---|---|
| nifi-migrate (Stackable) | https://github.com/stackabletech/nifi-migrate | Rust CLI. Rewrites `flow.json` for three documented renames: JoltTransformJSON and JoltTransformRecord (moved to `nifi-jolt-nar`, NIFI-12554) and the four Distributed*Cache* services (NIFI-13596). No analysis or report, no variables, no templates, no removed-component detection. Output re-serializes the whole file (key order changes). | 2026-08-28 (release 0.1.1, 2025-10-13) |
| NiFiMigrationTool (monicaruttle) | https://github.com/monicaruttle/NiFiMigrationTool | Jenkins pipeline that uploads XML templates from source control to a NiFi 1.x instance. Not related to 2.x migration. | 2020-04-09 |

PyPI: no package named `flowport`, `nifi-migration`, `nifi-migrate`,
`nifi2-migrate` or `nifi-upgrade` exists.

## Apache NiFi itself

- The NiFi 1.x UI offers "Convert to parameter" per property, one property at a
  time, once a parameter context is assigned to the process group. There is no
  bulk conversion.
- The NiFi Toolkit CLI (`nifi pg-get-vars`, `pg-set-var`, `pg-set-param-context`,
  `export-param-context`) reads and writes variables and parameter contexts on a
  live instance. It does not convert one to the other and does not analyze flows
  for 2.x compatibility.
- NiFi 2.x cannot import XML templates; they were removed (NIFI-12006).
  The 1.x UI can download a template; the 1.x REST API can instantiate a
  template into a process group, which can then be exported as a flow
  definition. Both need a running 1.x instance.
- The official guidance is manual: the wiki pages
  [Migration Guidance](https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance),
  [Deprecated Components and Features](https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features)
  and
  [Migrating Deprecated Components and Features for 2.0.0](https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0).
  NiFi 1.18.0 and later also write `nifi-deprecation.log` at runtime, which
  lists deprecated components in use on a running instance.

## Commercial / closed

| Tool | Link | Notes |
|---|---|---|
| Cloudera Data Flow Migration Tool | https://docs.cloudera.com/dataflow/cloud/migration-tool/topics/cdf-migration-tool.html | Analyzes 1.x flows and converts variables to parameters. Requires a Cloudera license. |
| Ksolves migration service | https://www.ksolves.com/apache-nifi-upgrade | Consulting service with private scripts. |

## Conclusion

No open-source tool offers offline analysis of a 1.x flow for 2.x
incompatibilities, or bulk conversion of variables to parameter contexts.
Stackable's `nifi-migrate` is the closest and covers only component renames;
its rule list is a useful cross-check for the replacement catalog.
Development of flowport continues.
