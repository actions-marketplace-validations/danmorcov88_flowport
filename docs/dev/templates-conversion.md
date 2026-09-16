# Templates to flow definitions: what was verified

Date: 2026-09-16. The two templates of the fixture flow were converted with
`flowport migrate templates` and uploaded into an unmodified
`apache/nifi:2.12.0` container (HTTPS, single-user credentials) through
`POST /nifi-api/process-groups/root/process-groups/upload`, the endpoint
behind "Upload flow definition" in the UI. The checks live in
`tests/integration/test_migrate_templates_nifi2.py` and run in CI on every
push to `main`.

## Sources

| Fact | Source |
|---|---|
| Templates were removed in NiFi 2.0 and are dropped when a 1.x flow loads. | [NIFI-12006](https://issues.apache.org/jira/browse/NIFI-12006), [Deprecated Components and Features](https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features) |
| Flow definitions replace templates: a process group can be downloaded as a flow definition and uploaded again. | [Migrating Deprecated Components and Features for 2.0.0](https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0) |
| Shape of a flow definition: the JSON that NiFi 1.28.1 writes for `GET /process-groups/{id}/download` (a `VersionedFlowSnapshot` with `flowContents`, `flowEncodingVersion` 1.0). | `tests/fixtures/nifi-1.28.1/definitions/template-conversion.json`, exported from the same group the template was made of |

## Where templates live

| Source | Shape |
|---|---|
| `flow.json` | `templates[].templateDto` with `snippet` (a `FlowSnippetDTO`): the REST API's DTOs, `config` blocks on processors, `contents` on nested groups. The XML is not stored. |
| `GET /templates/{id}/download` (the `.xml` file) | The same DTO rendered by JAXB: lists are repeated elements, maps are `<entry><key/><value/></entry>`, empty lists and `false` flags are omitted, unset properties are entries without `<value>`. |

The reader turns the XML into the JSON shape; converting the XML export and
the copy embedded in `flow.json` gives byte-identical definitions (unit
test on both fixture templates).

## Observations on 2.12.0

| Situation | Result |
|---|---|
| Definition with template identifiers as `identifier` (the `xxxxxxxx-xxxx-xxxx-0000-000000000000` ids a template carries), no `instanceIdentifier` | Imported; NiFi assigns new instance ids. |
| Processor property that holds a controller service id (`record-reader`) with `propertyDescriptors[...].identifiesControllerService: true` and the service in the same group's `controllerServices` | The reference is remapped to the new service instance: the only validation error left on ConvertRecord is that the services are disabled. |
| `controllerServiceApis` absent on controller services (a template does not carry the service API) | Import works; the services are created with their type and bundle. |
| Connections between processors, funnels, the ports of a nested group and a processor; `selectedRelationships: [""]` for port and funnel sources | All created, with name, back pressure, expiration, prioritizer, bends, z-index and load balancing as in the template. |
| Remote process group with the ports it had discovered, `name` filled with the target URI (templates carry no name) | Created with target URI, transport protocol and its input port. |
| Component types that no longer exist (GetHTTP, PostHTTP, HashContent, HashAttribute) | Imported as ghosts (`extensionMissing: true`), the same components the analyzer reports as `NIFI2-REMOVED-COMPONENT` in the definition's report. |
| Bundle version `1.28.1` on components that still exist | Resolved to the 2.12.0 bundle, as when a flow loads. |

Not carried over, because a template does not have it: `displayName` of
property descriptors (the name is used), `controllerServiceApis`, unset
property values, sensitive values (templates never contain them).

## How to reproduce

```
pytest -m integration            # needs Docker; both NiFi versions
```

Or by hand with the 2.x container from `docs/dev/nifi2-load-experiment.md`,
then `POST /nifi-api/access/token` (form fields `username`, `password`;
send `Accept: */*`, the token is `text/plain`) and a multipart upload with
fields `groupName`, `positionX`, `positionY`, `clientId`,
`disconnectedNodeAcknowledged` and `file`.
