# Component replacements: what was verified

Date: 2026-09-16. The `Replacements` and `Scheduling` groups of the fixture
flow were migrated with `flowport migrate components` and uploaded into an
unmodified `apache/nifi:2.12.0` container through the endpoint behind
"Upload flow definition". The checks live in
`tests/integration/test_nifi2_import.py` and run in CI on every push to
`main`.

## Sources

| Replacement | Source |
|---|---|
| Base64EncodeContent -> EncodeContent (`Encoding: base64`) | [Migrating Deprecated Components and Features for 2.0.0](https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0), section Base64EncodeContent; [NIFI-11174](https://issues.apache.org/jira/browse/NIFI-11174) |
| GetHTTP -> InvokeHTTP (`HTTP Method: GET`, `HTTP URL`, `Response FlowFile Naming Strategy: URL_PATH`) | same page, section GetHTTP |
| PostHTTP -> InvokeHTTP (`HTTP Method: POST`, `HTTP URL`) | same page, section PostHTTP |
| JoltTransformJSON, JoltTransformRecord -> `org.apache.nifi.processors.jolt.*` in `nifi-jolt-nar` | [NIFI-12554](https://issues.apache.org/jira/browse/NIFI-12554) |
| DistributedMapCacheServer/ClientService, DistributedSetCacheServer/ClientService -> MapCache*/SetCache* | [NIFI-13596](https://issues.apache.org/jira/browse/NIFI-13596) |
| EVENT_DRIVEN -> TIMER_DRIVEN | [NIFI-11813](https://issues.apache.org/jira/browse/NIFI-11813); the [Deprecated Components and Features](https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features) table names "Timer Driven Scheduling Strategy" as the alternative |

The wiki documents the essential properties of each replacement. The other
property names (`Socket Read Timeout`, `Request User-Agent`, `Request
Content-Encoding`, the Jolt and cache property renames) come from the
extension manifests of the two releases (`catalog/generated/properties/`),
and `tests/unit/test_replacements.py` checks every mapping against them:
each old name exists on the source type, each new name on the target type,
every source property is either renamed, dropped or kept under the same
name, and every relationship of the target is reached or auto-terminated.

## Observations on 2.12.0

| Situation | Result |
|---|---|
| GetHTTP replaced by InvokeHTTP with the mapped properties, `success` connection remapped to `Response`, `Original`/`Retry`/`No Retry`/`Failure` auto-terminated | Imported under the new type, **valid**; the properties show the mapped values (`Response Redirects Enabled: True`, `Request User-Agent`). |
| PostHTTP replaced by InvokeHTTP, `success` -> `Original`, `failure` -> `Failure, Retry, No Retry`, `Response` auto-terminated, `Compression Level: 6` -> `Request Content-Encoding: GZIP` | Imported, valid; connections carry the remapped relationships. |
| PostHTTP with `Send as FlowFile: true` | Left as is (guard) and reported `NIFI2-REPLACEMENT-SKIPPED`; imports as a ghost, as the report says. |
| Base64EncodeContent (`Mode: Decode`) -> EncodeContent | Valid with `Mode: Decode`, `Encoding: base64`. |
| JoltTransformJSON with renamed properties (`Jolt Specification`, `Jolt Transform`) | Valid under `org.apache.nifi.processors.jolt.JoltTransformJSON`. |
| JoltTransformRecord referencing a CSVReader and a JsonRecordSetWriter | Imported under the new type; the only property errors are "Controller Service ... is disabled", so the references were remapped. |
| DistributedMapCacheClientService -> MapCacheClientService, referenced by DetectDuplicate | Both services valid; DetectDuplicate's only property error is that the service is disabled: the reference survived the rename. |
| Set cache server and client | Valid under the new types. |
| Processor scheduled EVENT_DRIVEN, switched to TIMER_DRIVEN / 0 sec | Imported and valid. (Left as EVENT_DRIVEN, NiFi 2.x refuses to start with the flow, see `nifi2-load-experiment.md`.) |
| Bundle version set to the target release (`2.12.0`) | Resolved; the same happens when NiFi loads a `1.28.1` bundle version of an existing type. |

Not replaced on purpose (the guide documents no 1:1 mapping): HashContent
(the attribute name changes and needs an UpdateAttribute), HashAttribute
(needs an Expression Language rewrite), GetJMSQueue/GetJMSTopic/PutJMS (need
a JMSConnectionFactoryProvider and client libraries), PutSlack/PostSlack,
the Kafka `_1_0`/`_2_0` processors (their documented successors `_2_6` are
themselves gone in 2.x), Hive, Azure `_v12` and Elasticsearch successors
(different property sets). They remain `NIFI2-REMOVED-COMPONENT` findings.

## How to reproduce

```
pytest -m integration            # needs Docker; both NiFi versions
```
