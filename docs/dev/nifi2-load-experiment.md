# What NiFi 2.12.0 does with a 1.28.1 flow

Date: 2026-09-16. The fixture `tests/fixtures/nifi-1.28.1/flow.json.gz` was
placed as `conf/flow.json.gz` in an unmodified `apache/nifi:2.12.0` container
(single-user HTTPS, `NIFI_SENSITIVE_PROPS_KEY` set). Observations come from
`logs/nifi-app.log`, the REST API after startup, and the `flow.json.gz` that
2.12.0 wrote back. These observations set the severities in the rule catalog.

## Startup

| Situation | Result |
|---|---|
| Any processor with `schedulingStrategy: EVENT_DRIVEN` | **NiFi does not start.** `IllegalArgumentException: No enum constant org.apache.nifi.scheduling.SchedulingStrategy.EVENT_DRIVEN` while loading the flow. |
| `nifi.sensitive.props.key` blank while a flow exists | NiFi does not start (`Migration Required for blank Sensitive Properties Key`). Configuration issue, out of scope for flow analysis, but worth a note in the docs. |
| Everything else in the fixture | NiFi starts; problems show up per component. |

## Components

| Situation | Result on 2.12.0 |
|---|---|
| Processor type that no longer exists (GetHTTP, PostHTTP, HashContent, HashAttribute, Base64EncodeContent, ListenTCPRecord, ParseCEF, ListenRELP, PutJMS, GetJMSQueue, ConvertJSONToSQL, EncryptContent, PutSlack, ConsumeKafka_2_6, PutElasticsearchHttp, PutAzureBlobStorage) | Loaded as a **ghost**: `extensionMissing: true`, `validationStatus: INVALID`, bundle stays at `1.28.1`. Log: `Could not create Processor of type ... creating "Ghost" implementation`. |
| Controller service that no longer exists (DistributedMapCacheServer, DistributedMapCacheClientService) | Ghost. NiFi does **not** map the old names to MapCacheServer / MapCacheClientService. |
| Reporting task that no longer exists (PrometheusReportingTask) | Ghost. |
| Type whose class name and bundle changed (`org.apache.nifi.processors.standard.JoltTransformJSON` in `nifi-standard-nar` -> `org.apache.nifi.processors.jolt.JoltTransformJSON` in `nifi-jolt-nar`) | Ghost. No automatic mapping. |
| Component from a third-party bundle (`com.example:example-custom-nar`) | Ghost (`Unable to find bundle for coordinate`). |
| Same type, same bundle artifact, only the version differs (`1.28.1` -> `2.12.0`) | Resolved to the 2.12.0 bundle automatically. |

## Scripting

| Component and engine | Result |
|---|---|
| ExecuteScript with `python`, `ruby`, `lua`, `ECMAScript` | INVALID: `Given value not found in allowed set 'Clojure, Groovy'` |
| InvokeScriptedProcessor with `python` | INVALID: allowed set `'Groovy'` |
| ScriptedLookupService with `python` | INVALID: allowed set `'Groovy'` |
| ExecuteScript / ScriptedTransformRecord / ScriptedReader with `Groovy` | Engine accepted (other validation errors come from the placeholder scripts). |

The allowed set differs per component, so the catalog must record the
allowable values of the `Script Engine` property per component type.

## Variables

- Every `variables` entry is **silently dropped**; the flow written back by
  2.12.0 has no `variables` key on any group.
- Property values are left as they are (`Listening Port: ${port}` survives).
  The expression now evaluates against FlowFile attributes / environment
  only, so the value changes without any validation error. Nothing in the UI
  or log points at the problem.

## Templates

- The top-level `templates` array is **silently dropped** from the flow.
  Templates that were not exported before the upgrade are lost.

## Scheduling

| Cron expression | Result |
|---|---|
| `0 0 12 * * ? 2030` (seventh field, year) | INVALID: `Scheduling Period is not a valid cron expression` |
| `0 0 6 ? * 1` (numeric day of week) | VALID, but the meaning changed: Quartz `1` = Sunday, Spring `1` = Monday (NIFI-12290). |
| `0 0 6 ? * MON-FRI` | VALID, unchanged. |

## Deprecated properties handled by NiFi itself

InvokeHTTP with `Proxy Host` / `Proxy Port` set: on load, 2.12.0 created a
`StandardProxyConfigurationService` in the same group, pointed the new
`Proxy Configuration Service` property at it and left the processor VALID.
The old properties stay in the flow as unused entries. This is NiFi's own
property migration (`migrateProperties`), so the analyzer should report this
case as INFO at most, not as something the user has to fix.

## Other

- Parameter contexts, inheritance and `#{param}` references load unchanged.
- The 1.x top-level key `maxEventDrivenThreadCount` is dropped without error.
- The flow is rewritten with `encodingVersion` 2.0 and new top-level keys
  (`flowAnalysisRules`, `connectors`).

## Consequences for the catalog

| Rule | Severity |
|---|---|
| Event-driven scheduling | BLOCKER (instance does not start) |
| Removed / renamed / moved component types, third-party bundles | BLOCKER (ghost, invalid) |
| Script engine not shipped for that component | BLOCKER (invalid) |
| Variable referenced from a property | BLOCKER (silent behavior change) |
| Unused variable | INFO |
| Template in the flow | MANUAL (silent data loss; export first) |
| Cron with a year field | BLOCKER (invalid) |
| Cron with numeric day of week | MANUAL (silent meaning change) |
| Deprecated property that NiFi migrates on load | INFO |

## How to reproduce

The 2.x image has no HTTP mode; use HTTPS with single-user credentials:

```
docker create --name nifi2 -p 8443:8443 \
  -e NIFI_WEB_HTTPS_HOST=0.0.0.0 \
  -e SINGLE_USER_CREDENTIALS_USERNAME=flowport \
  -e SINGLE_USER_CREDENTIALS_PASSWORD=flowport-password-12 \
  -e NIFI_SENSITIVE_PROPS_KEY=flowport-fixtures-key \
  apache/nifi:2.12.0
# copy flow.json.gz into conf/ owned by uid 1000 (docker cp writes as root, which NiFi refuses)
docker start nifi2
# POST /nifi-api/access/token with username/password, then use the Bearer token
```
