# Parking lot

Ideas that came up during development and are outside the current phase.

- **Removed properties.** The manifests show 313 types whose 1.28.1 properties
  no longer exist in 2.12.0, but most are renames that NiFi migrates on load
  (`migrateProperties`), as seen with InvokeHTTP "Remote URL" and the proxy
  properties. Reporting them needs knowledge of which ones NiFi migrates; that
  could be extracted from the 2.x source code per component.
- **Kerberos properties on Kafka and others** (NIFI-11202) and **PutMongo
  "Mongo URI"** (NIFI-11221): property-level removals on components that still
  exist. Candidates for hand-written rules once verified against 2.x.
- **Registry clients and parameter providers.** `registries` and
  `parameterProviders` in flow.json are not analyzed yet.
- **Sensitive properties key.** NiFi 2.x refuses to start with a blank
  `nifi.sensitive.props.key` when a flow exists. Configuration, not flow, but
  worth a note in the quickstart.
- **Rule reference generated from the catalog** (planned for Phase 5).
- **Generated large fixture as a committed file** was rejected: 50 MB in git.
  The performance test generates it on the fly instead.
- **Collision and visibility checks in `analyze`.** `NIFI2-VARIABLE-PARAMETER-COLLISION`
  and `NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE` are only produced by
  `migrate variables`; `analyze` could predict them so that users see them
  before migrating.
- **Inherit a created context into an existing one.** When an existing
  parameter context sits between the group that defines a variable and the
  group that uses it, flowport reports `...-NOT-VISIBLE` instead of adding
  the created context to `inheritedParameterContexts` of the existing one.
  Automatic inheritance needs cycle detection (a context can be assigned to
  several groups) and changes a context the user owns.
- **Parameter context referenced by name but missing from the file.** The
  migration treats it as a boundary that resolves nothing. Real exports
  always include the referenced contexts.
- **`--fail-on` for `migrate`.** The command exits 0 whenever it writes; a
  threshold on the remaining findings, as in `analyze`, would help in CI.
- **Rewriting `${var:function()}` to `${ #{var}:function() }`.** The user
  guide documents the form; still left MANUAL because the function may rely
  on the FlowFile-attribute fallback of the original expression.
- **Strip templates from the flow after conversion.** `migrate templates`
  could also write a `flow.json.gz` without the `templates` list; 2.x drops
  them anyway, so this only matters for a tidy 1.x flow.
- **`controllerServiceApis` from the catalog.** The extension manifests list
  the service APIs each controller service provides; `tools/build_catalog.py`
  could extract them so converted templates carry them. NiFi does not need
  them on import.
- **Templates that reference controller services outside the snippet.** A
  template made from a snippet whose processors use services of a parent
  group references ids that are not in the template. Not exercised by the
  fixtures; the import would leave the property pointing at an unknown id.
- **Unit test for `migrate all`** once it exists (Phase 5): the three
  migrations in sequence on one flow.

