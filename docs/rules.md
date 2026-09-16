# Rule reference

Generated from `src/flowport/catalog/manual.yaml` and `src/flowport/catalog/replacements.yaml` by `tools/build_rule_reference.py`; do not edit by hand. Every finding carries a `reference` link to its section here.

Catalog: NiFi 1.28.1 to 2.12.0.

## Severities

| Severity | Meaning |
|---|---|
| BLOCKER | NiFi 2.x does not start, or the component loads as an invalid ghost. |
| MANUAL | Needs a human decision; flowport does not change it. |
| AUTO_FIXABLE | A `flowport migrate` command fixes it. |
| INFO | Works, but worth knowing. |

## Rules

| Rule | Severity | Category | Title |
|---|---|---|---|
| [NIFI2-CRON-YEAR-FIELD](#nifi2-cron-year-field) | BLOCKER | scheduling | Cron expression with a year field |
| [NIFI2-EVENT-DRIVEN](#nifi2-event-driven) | BLOCKER | scheduling | Event-driven scheduling removed in NiFi 2.x |
| [NIFI2-REMOVED-COMPONENT](#nifi2-removed-component) | BLOCKER | removed-component | Component type removed in NiFi 2.x |
| [NIFI2-RENAMED-COMPONENT](#nifi2-renamed-component) | BLOCKER | removed-component | Component type renamed or moved in NiFi 2.x |
| [NIFI2-SCRIPT-ENGINE](#nifi2-script-engine) | BLOCKER | script-engine | Script engine removed in NiFi 2.x |
| [NIFI2-CRON-NUMERIC-DAY-OF-WEEK](#nifi2-cron-numeric-day-of-week) | MANUAL | scheduling | Cron expression with a numeric day of week |
| [NIFI2-OPTIONAL-BUNDLE](#nifi2-optional-bundle) | MANUAL | optional-bundle | Component not in the default NiFi 2.x distribution |
| [NIFI2-REPLACEMENT-SKIPPED](#nifi2-replacement-skipped) | MANUAL | replaced-component | Documented replacement not applied |
| [NIFI2-TEMPLATE](#nifi2-template) | MANUAL | templates | Template stored in the flow |
| [NIFI2-THIRD-PARTY-BUNDLE](#nifi2-third-party-bundle) | MANUAL | custom-nar | Component from a third-party or custom NAR |
| [NIFI2-VARIABLE-NAME](#nifi2-variable-name) | MANUAL | variables | Variable name is not a valid parameter name |
| [NIFI2-VARIABLE-PARAMETER-COLLISION](#nifi2-variable-parameter-collision) | MANUAL | variables | Variable name collides with an existing parameter |
| [NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE](#nifi2-variable-reference-attribute-scope) | MANUAL | variables | Variable reference in a property that also reads FlowFile attributes |
| [NIFI2-VARIABLE-REFERENCE-FUNCTION](#nifi2-variable-reference-function) | MANUAL | variables | Variable used with Expression Language functions |
| [NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE](#nifi2-variable-reference-not-visible) | MANUAL | variables | Parameter for the variable is not visible from this group |
| [NIFI2-VARIABLE-REFERENCE-SENSITIVE](#nifi2-variable-reference-sensitive) | MANUAL | variables | Variable reference in a sensitive property |
| [NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE](#nifi2-variable-reference-unknown-scope) | MANUAL | variables | Variable reference in a property of an unknown component type |
| [NIFI2-VARIABLE-REFERENCE](#nifi2-variable-reference) | AUTO_FIXABLE | variables | Property references a variable |
| [NIFI2-VARIABLES-DEFINED](#nifi2-variables-defined) | AUTO_FIXABLE | variables | Process group defines variables |
| [NIFI2-COMPONENT-REPLACED](#nifi2-component-replaced) | INFO | replaced-component | Component replaced by its documented successor |
| [NIFI2-DEPRECATED-IN-TARGET](#nifi2-deprecated-in-target) | INFO | deprecated-component | Component deprecated in NiFi 2.x |
| [NIFI2-INVOKEHTTP-PROXY-PROPERTIES](#nifi2-invokehttp-proxy-properties) | INFO | deprecated-property | InvokeHTTP proxy properties replaced by a controller service |
| [NIFI2-REPLACED-PROPERTY-DROPPED](#nifi2-replaced-property-dropped) | INFO | replaced-component | Property without an equivalent on the replacement |
| [NIFI2-SCHEDULING-CHANGED](#nifi2-scheduling-changed) | INFO | scheduling | Event-driven scheduling replaced by timer-driven |
| [NIFI2-UNKNOWN-COMPONENT](#nifi2-unknown-component) | INFO | unknown-component | Component type not in the catalog |
| [NIFI2-VARIABLE-REFERENCE-NO-EL](#nifi2-variable-reference-no-el) | INFO | variables | Variable-like text in a property without Expression Language support |
| [NIFI2-VARIABLE-UNUSED](#nifi2-variable-unused) | INFO | variables | Variable is never referenced |

### NIFI2-CRON-YEAR-FIELD

**Cron expression with a year field** — severity BLOCKER, category `scheduling`.
Reported by `flowport analyze`.

Message: Run schedule '<expression>' has seven fields. NiFi 2.x uses Spring cron expressions, which have no year field, so the component is invalid.

Suggestion: Remove the seventh field. Components can no longer be scheduled for a specific year.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12290>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-EVENT-DRIVEN

**Event-driven scheduling removed in NiFi 2.x** — severity BLOCKER, category `scheduling`.
Reported by `flowport analyze`.

Message: <short_type> uses the EVENT_DRIVEN scheduling strategy. NiFi 2.x does not recognize it and fails to start when the flow contains it.

Suggestion: Change the scheduling strategy to TIMER_DRIVEN (run schedule 0 sec) before upgrading.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-11813>
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-REMOVED-COMPONENT

**Component type removed in NiFi 2.x** — severity BLOCKER, category `removed-component`.
Reported by `flowport analyze`.

Message: <short_type> does not exist in NiFi <target_version>. NiFi loads it as a ghost component that is invalid and cannot run.

Suggestion: Replace it before upgrading.<alternatives>

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-RENAMED-COMPONENT

**Component type renamed or moved in NiFi 2.x** — severity BLOCKER, category `removed-component`.
Reported by `flowport analyze`.

Message: <short_type> does not exist under this class name in NiFi <target_version>; a component of the same name exists as <to_type> in bundle <to_bundle>. NiFi does not map the old name and loads a ghost component.

Suggestion: Change the component type to <to_type> and its bundle to <to_bundle>. Check that its properties still match.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-SCRIPT-ENGINE

**Script engine removed in NiFi 2.x** — severity BLOCKER, category `script-engine`.
Reported by `flowport analyze`.

Message: <short_type> uses the <engine> script engine, which NiFi <target_version> no longer ships for this component (allowed: <allowed>). The component is invalid on 2.x.

Suggestion: Rewrite the script in Groovy, or for Python use the native Python processor API of NiFi 2.x.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>
- <https://issues.apache.org/jira/browse/NIFI-11713>
- <https://issues.apache.org/jira/browse/NIFI-11753>
- <https://issues.apache.org/jira/browse/NIFI-12378>

### NIFI2-CRON-NUMERIC-DAY-OF-WEEK

**Cron expression with a numeric day of week** — severity MANUAL, category `scheduling`.
Reported by `flowport analyze`.

Message: Run schedule '<expression>' uses a number for the day of week ('<field>'). NiFi 1.x (Quartz) counts 1 as Sunday; NiFi 2.x (Spring) counts 1 as Monday, so the schedule shifts by one day without any error.

Suggestion: Use day names (SUN, MON, ...) instead of numbers.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12290>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-OPTIONAL-BUNDLE

**Component not in the default NiFi 2.x distribution** — severity MANUAL, category `optional-bundle`.
Reported by `flowport analyze`.

Message: <short_type> exists in NiFi <target_version> but its bundle <artifact> is not part of the default binary distribution (Maven profile <profile>).

Suggestion: Download <artifact> for NiFi <target_version> from Maven Central into the lib or extensions directory, or build NiFi with -P<profile>, before loading this flow.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance>

### NIFI2-REPLACEMENT-SKIPPED

**Documented replacement not applied** — severity MANUAL, category `replaced-component`.
Reported by `flowport migrate components`.

Message: <short_type> could be replaced by <to_type>, but property '<property>' is set to '<value>': <reason>.

Suggestion: Change the flow by hand, then replace the component with <to_type>.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-TEMPLATE

**Template stored in the flow** — severity MANUAL, category `templates`.
Reported by `flowport analyze`.

Message: Template '<name>' is stored in this flow. NiFi 2.x removed templates and silently drops them when the flow loads.

Suggestion: Export the template from the 1.x instance before upgrading, or convert it to a flow definition (flowport migrate templates).

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12006>
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-THIRD-PARTY-BUNDLE

**Component from a third-party or custom NAR** — severity MANUAL, category `custom-nar`.
Reported by `flowport analyze`.

Message: <short_type> comes from bundle <bundle>, which is not an Apache NiFi bundle. NARs built against the 1.x API do not load on 2.x; the component becomes a ghost.

Suggestion: Obtain or build a version of this NAR for the NiFi 2.x API and deploy it before loading the flow.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>
- <https://issues.apache.org/jira/browse/NIFI-12079>

### NIFI2-VARIABLE-NAME

**Variable name is not a valid parameter name** — severity MANUAL, category `variables`.
Reported by `flowport analyze`.

Message: Variable '<variable>' contains characters that are not allowed in a parameter name (letters, digits, '-', '_', '.' and space).

Suggestion: Choose a valid parameter name and update every reference.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#name-parameter-restriction>

### NIFI2-VARIABLE-PARAMETER-COLLISION

**Variable name collides with an existing parameter** — severity MANUAL, category `variables`.
Reported by `flowport migrate variables`.

Message: This process group defines variable '<variable>' and already uses parameter context '<context>', which resolves a parameter of the same name. Adding the variable as a parameter would change what #{<variable>} means for every group using that context, so flowport leaves this group's variables unchanged.

Suggestion: Rename the variable or the parameter, or drop the variable if the parameter value is the intended one, then run the migration again.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://issues.apache.org/jira/browse/NIFI-8490>
- <https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#assigning_parameter_context_to_PG>

### NIFI2-VARIABLE-REFERENCE-ATTRIBUTE-SCOPE

**Variable reference in a property that also reads FlowFile attributes** — severity MANUAL, category `variables`.
Reported by `flowport analyze`.

Message: Property '<property>' references variable ${<variable>} (defined in <defined_in>) and supports FlowFile attributes. On 1.x a FlowFile attribute named '<variable>' already takes priority over the variable (Expression Language hierarchy); on 2.x only the attribute remains.

Suggestion: If the value must come from configuration, replace the reference with a parameter #{<variable>}. If a FlowFile attribute is intended, no change is needed.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>
- <https://nifi.apache.org/docs/nifi-docs/html/expression-language-guide.html#expression-language-hierarchy>

### NIFI2-VARIABLE-REFERENCE-FUNCTION

**Variable used with Expression Language functions** — severity MANUAL, category `variables`.
Reported by `flowport analyze`.

Message: Property '<property>' applies Expression Language functions to variable ${<variable>} (defined in <defined_in>). The subject of the expression must change from the variable to a parameter reference.

Suggestion: Rewrite the subject as a parameter reference inside the expression, for example ${ #{<variable>}:toUpper() } (the parameter is substituted before the expression is evaluated), then test the property on 1.x.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#referencing-parameters>

### NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE

**Parameter for the variable is not visible from this group** — severity MANUAL, category `variables`.
Reported by `flowport migrate variables`.

Message: Property '<property>' references variable ${<variable>} (defined in <defined_in>). The parameter was created in context '<parameter_context>', but this group uses parameter context '<context>', which <reason>. A process group does not inherit its parent's parameter context, so the reference was left unchanged.

Suggestion: Make '<context>' inherit from '<parameter_context>' (or move the parameter) and replace the reference with #{<variable>}.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://issues.apache.org/jira/browse/NIFI-8490>
- <https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#assigning_parameter_context_to_PG>

### NIFI2-VARIABLE-REFERENCE-SENSITIVE

**Variable reference in a sensitive property** — severity MANUAL, category `variables`.
Reported by `flowport analyze`.

Message: Sensitive property '<property>' references variable ${<variable>} (defined in <defined_in>). Variables are never sensitive, but a sensitive property can only reference a sensitive parameter, so the reference cannot be rewritten to a non-sensitive parameter.

Suggestion: Create a sensitive parameter with the variable's value and reference it as #{<variable>}.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#using-parameters-with-sensitive-properties>

### NIFI2-VARIABLE-REFERENCE-UNKNOWN-SCOPE

**Variable reference in a property of an unknown component type** — severity MANUAL, category `variables`.
Reported by `flowport analyze`.

Message: Property '<property>' references variable ${<variable>} (defined in <defined_in>). The component type is not in the catalog, so flowport cannot tell whether the property evaluates Expression Language or which scope it uses.

Suggestion: Check the component's documentation. If the property supports Expression Language, replace the reference with a parameter #{<variable>}.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>

### NIFI2-VARIABLE-REFERENCE

**Property references a variable** — severity AUTO_FIXABLE, category `variables`.
Reported by `flowport analyze`.

Message: Property '<property>' references variable ${<variable>} (defined in <defined_in>). On NiFi 2.x the variable no longer exists, so the expression evaluates to empty unless a FlowFile attribute or environment variable of the same name exists. No validation error will point at this.

Suggestion: Replace the reference with a parameter: #{<variable>}.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-VARIABLES-DEFINED

**Process group defines variables** — severity AUTO_FIXABLE, category `variables`.
Reported by `flowport analyze`.

Message: This process group defines <count> variable(s): <names>. NiFi 2.x removed the Variable Registry and silently drops them when the flow loads.

Suggestion: Convert the variables to a parameter context (flowport migrate variables) and rewrite the references to #{name}.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-COMPONENT-REPLACED

**Component replaced by its documented successor** — severity INFO, category `replaced-component`.
Reported by `flowport migrate components`.

Message: <from_short> was replaced by <short_type> (<type>). <note>

Suggestion: Review the component's configuration on NiFi 1.x, then on 2.x.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-DEPRECATED-IN-TARGET

**Component deprecated in NiFi 2.x** — severity INFO, category `deprecated-component`.
Reported by `flowport analyze`.

Message: <short_type> still works in NiFi <target_version> but is deprecated: <reason>

Suggestion: Plan a replacement.<alternatives>

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-INVOKEHTTP-PROXY-PROPERTIES

**InvokeHTTP proxy properties replaced by a controller service** — severity INFO, category `deprecated-property`.
Reported by `flowport analyze`.

Message: InvokeHTTP is configured with the deprecated proxy properties <properties>. NiFi 2.x creates a StandardProxyConfigurationService from them when the flow loads.

Suggestion: No change needed before the upgrade. After the upgrade, check the generated proxy configuration service and remove the leftover properties.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>
- <https://issues.apache.org/jira/browse/NIFI-11174>

### NIFI2-REPLACED-PROPERTY-DROPPED

**Property without an equivalent on the replacement** — severity INFO, category `replaced-component`.
Reported by `flowport migrate components`.

Message: Property '<property>' of the former <from_short> was set to '<value>' and has no equivalent on <short_type>; it was dropped.

Suggestion: Check whether the behavior it controlled matters and configure <short_type> accordingly.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>

### NIFI2-SCHEDULING-CHANGED

**Event-driven scheduling replaced by timer-driven** — severity INFO, category `scheduling`.
Reported by `flowport migrate components`.

Message: <short_type> used the EVENT_DRIVEN scheduling strategy, which NiFi 2.x removed; it now runs TIMER_DRIVEN with a run schedule of 0 sec.

Suggestion: Check the run schedule and the number of concurrent tasks after the upgrade.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-11813>
- <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features>

### NIFI2-UNKNOWN-COMPONENT

**Component type not in the catalog** — severity INFO, category `unknown-component`.
Reported by `flowport analyze`.

Message: <short_type> from bundle <bundle> is not part of the Apache NiFi <source_version> distribution, so flowport cannot tell whether it exists in 2.x.

Suggestion: Check the bundle's own documentation for a NiFi 2.x release.

Sources:
- <https://cwiki.apache.org/confluence/display/NIFI/Migration+Guidance>

### NIFI2-VARIABLE-REFERENCE-NO-EL

**Variable-like text in a property without Expression Language support** — severity INFO, category `variables`.
Reported by `flowport analyze`.

Message: Property '<property>' contains ${<variable>} but does not support Expression Language, so the text was never evaluated on 1.x and nothing changes on 2.x.

Suggestion: No change needed. Check the value if it was meant to be dynamic.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>

### NIFI2-VARIABLE-UNUSED

**Variable is never referenced** — severity INFO, category `variables`.
Reported by `flowport analyze`.

Message: Variable '<variable>' is defined here but no property in this group or its children references it.

Suggestion: Drop it, or keep it as a parameter if other flows depend on it.

Sources:
- <https://issues.apache.org/jira/browse/NIFI-12079>

## Component replacements

Applied by `flowport migrate components`; defined in `replacements.yaml`.

| From | To | Bundle | Sources |
|---|---|---|---|
| `org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService` | `org.apache.nifi.distributed.cache.client.MapCacheClientService` | `nifi-distributed-cache-services-nar` | <https://issues.apache.org/jira/browse/NIFI-13596>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.distributed.cache.client.DistributedSetCacheClientService` | `org.apache.nifi.distributed.cache.client.SetCacheClientService` | `nifi-distributed-cache-services-nar` | <https://issues.apache.org/jira/browse/NIFI-13596>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.distributed.cache.server.DistributedSetCacheServer` | `org.apache.nifi.distributed.cache.server.SetCacheServer` | `nifi-distributed-cache-services-nar` | <https://issues.apache.org/jira/browse/NIFI-13596>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.distributed.cache.server.map.DistributedMapCacheServer` | `org.apache.nifi.distributed.cache.server.map.MapCacheServer` | `nifi-distributed-cache-services-nar` | <https://issues.apache.org/jira/browse/NIFI-13596>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.processors.jolt.record.JoltTransformRecord` | `org.apache.nifi.processors.jolt.JoltTransformRecord` | `nifi-jolt-nar` | <https://issues.apache.org/jira/browse/NIFI-12554>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.processors.standard.Base64EncodeContent` | `org.apache.nifi.processors.standard.EncodeContent` | `nifi-standard-nar` | <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>, <https://issues.apache.org/jira/browse/NIFI-11174> |
| `org.apache.nifi.processors.standard.GetHTTP` | `org.apache.nifi.processors.standard.InvokeHTTP` | `nifi-standard-nar` | <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>, <https://issues.apache.org/jira/browse/NIFI-11174> |
| `org.apache.nifi.processors.standard.JoltTransformJSON` | `org.apache.nifi.processors.jolt.JoltTransformJSON` | `nifi-jolt-nar` | <https://issues.apache.org/jira/browse/NIFI-12554>, <https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features> |
| `org.apache.nifi.processors.standard.PostHTTP` | `org.apache.nifi.processors.standard.InvokeHTTP` | `nifi-standard-nar` | <https://cwiki.apache.org/confluence/display/NIFI/Migrating+Deprecated+Components+and+Features+for+2.0.0>, <https://issues.apache.org/jira/browse/NIFI-11174> |

### DistributedMapCacheClientService

Renamed in NiFi 2.0. Processors keep referencing the same service instance.


### DistributedSetCacheClientService

Renamed in NiFi 2.0 together with the map cache services.


### DistributedSetCacheServer

Renamed in NiFi 2.0 together with the map cache services.

Properties renamed: `maximum-read-size` to `Maximum Read Size`

### DistributedMapCacheServer

Renamed in NiFi 2.0: nothing about the cache is distributed. Processors keep referencing the same service instance.

Properties renamed: `maximum-read-size` to `Maximum Read Size`

### JoltTransformRecord

Same processor in the nifi-jolt-nar bundle; property names changed with the move.

Properties renamed: `jolt-record-record-reader` to `Record Reader`, `jolt-record-record-writer` to `Record Writer`, `jolt-record-transform` to `Jolt Transform`, `jolt-record-spec` to `Jolt Specification`, `jolt-record-custom-class` to `Custom Transformation Class Name`, `jolt-record-custom-modules` to `Custom Module Directory`, `jolt-record-transform-cache-size` to `Transform Cache Size`

### Base64EncodeContent

EncodeContent is a direct replacement; Mode is kept and Encoding is set to base64.

Properties set: `Encoding` = `base64`

### GetHTTP

InvokeHTTP writes the response to the Response relationship; request-side relationships that GetHTTP did not have are auto-terminated. Unlike GetHTTP, no SSL Context Service is needed for public HTTPS locations.

Properties renamed: `URL` to `HTTP URL`, `Username` to `Request Username`, `Password` to `Request Password`, `Data Timeout` to `Socket Read Timeout`, `User Agent` to `Request User-Agent`, `Follow Redirects` to `Response Redirects Enabled`, `proxy-configuration-service` to `Proxy Configuration Service`
Properties set: `HTTP Method` = `GET`, `Response FlowFile Naming Strategy` = `URL_PATH`
Properties dropped: `Filename`, `Accept Content-Type`, `redirect-cookie-policy`, `Proxy Host`, `Proxy Port`
Relationships: `success` to `Response`
Auto-terminated: `Original`, `Retry`, `No Retry`, `Failure`

### JoltTransformJSON

Same processor in the nifi-jolt-nar bundle; property names changed with the move.

Properties renamed: `jolt-transform` to `Jolt Transform`, `jolt-spec` to `Jolt Specification`, `jolt-custom-class` to `Custom Transformation Class Name`, `jolt-custom-modules` to `Custom Module Directory`, `pretty_print` to `Pretty Print`

### PostHTTP

InvokeHTTP routes the sent FlowFile to Original on success and to Failure, Retry or No Retry otherwise; the HTTP response body (Response) is auto-terminated as PostHTTP discarded it.

Properties renamed: `URL` to `HTTP URL`, `Username` to `Request Username`, `Password` to `Request Password`, `Data Timeout` to `Socket Read Timeout`, `User Agent` to `Request User-Agent`, `Content-Type` to `Request Content-Type`, `Use Chunked Encoding` to `Request Chunked Transfer-Encoding Enabled`, `Attributes to Send as HTTP Headers (Regex)` to `Request Header Attributes Pattern`, `Compression Level` to `Request Content-Encoding`, `proxy-configuration-service` to `Proxy Configuration Service`
Properties set: `HTTP Method` = `POST`
Properties dropped: `Max Batch Size`, `Max Data to Post per Second`, `Send as FlowFile`, `Proxy Host`, `Proxy Port`
Relationships: `success` to `Original`, `failure` to `Failure`, `Retry`, `No Retry`
Auto-terminated: `Response`
Not applied when `Send as FlowFile` is `true`: the FlowFile packaging mode has no equivalent in InvokeHTTP; package with MergeContent (FlowFile Stream v3) before InvokeHTTP, or pass attributes as headers.
