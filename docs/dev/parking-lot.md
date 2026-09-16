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
