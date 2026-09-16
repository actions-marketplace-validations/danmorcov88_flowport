# Catalog generator spike

Date: 2026-09-16. Releases checked: NiFi 1.28.1 (last 1.x) and NiFi 2.12.0.

## Question

Can the removed/deprecated component inventory be generated from the NAR
artifacts of two releases instead of being written by hand?

## Result: yes

### Manifest location and format

Every NAR built with the NiFi NAR Maven plugin contains
`META-INF/docs/extension-manifest.xml`. Confirmed on:

| NAR | Release | Manifest | Extensions |
|---|---|---|---|
| nifi-standard-nar | 1.28.1 | yes | 126 processors, 4 reporting tasks |
| nifi-standard-nar | 2.12.0 | yes | 114 processors, 3 reporting tasks, 4 parameter providers, 5 flow analysis rules |
| nifi-update-attribute-nar | 1.28.1 | yes | 1 processor |
| nifi-scripting-nar | 1.28.1 | yes | 6 processors, 7 controller services (incl. reporting task) |
| nifi-standard-services-api-nar | 1.28.1 | yes | empty `<extensions/>` (API-only NAR) |

Structure (root element `extensionManifest`):

```
groupId, artifactId, version, parentNar{groupId,artifactId,version},
systemApiVersion, buildInfo,
extensions/extension*:
    name                 fully qualified class name
    type                 PROCESSOR | CONTROLLER_SERVICE | REPORTING_TASK |
                         PARAMETER_PROVIDER | FLOW_ANALYSIS_RULE | ...
    deprecationNotice?   reason, alternatives/alternative* (class names)
    description, tags
    properties/property*:
        name, displayName, description, required, sensitive,
        expressionLanguageSupported, expressionLanguageScope
        (NONE | VARIABLE_REGISTRY | FLOWFILE_ATTRIBUTES in 1.x;
         NONE | ENVIRONMENT | FLOWFILE_ATTRIBUTES in 2.x),
        allowableValues/allowableValue*{value,displayName,description}?,
        controllerServiceDefinition?{className,groupId,artifactId,version},
        dynamic, dynamicallyModifiesClasspath
    dynamicProperties, relationships, restricted, stateful, ...
```

Two things beyond the component inventory come for free:

- `expressionLanguageScope` per property. Phase 2 needs this to decide whether
  `${var}` may be rewritten to `#{var}` without attribute shadowing.
- `allowableValues` of `ExecuteScript` / `Script Engine`, which lists the
  script engines shipped in each release (1.28.1: Clojure, ECMAScript, Groovy,
  lua, python, ruby).

### Fallback

If a NAR had no manifest, the component classes are listed in
`META-INF/services/org.apache.nifi.processor.Processor`,
`.../org.apache.nifi.controller.ControllerService` and
`.../org.apache.nifi.reporting.ReportingTask` inside the jars under
`META-INF/bundled-dependencies/`. Confirmed on the three NARs above. The
fallback gives class names only (no deprecation notice, no properties). Not
needed for 1.28.1 or 2.12.0, but the generator implements it for safety.

### Enumerating the NARs of a release

- The Maven Central search API is not reliable for this: it returned an
  incomplete list for 1.28.1 and nothing for 2.12.0 three days after the
  release (index lag).
- `org.apache.nifi:nifi-assembly:<version>:pom` on Maven Central lists every
  NAR of the distribution as a `<dependency>` with `<type>nar</type>`,
  including the optional ones under `include-*` profiles
  (1.28.1: 167 NARs, 2.12.0: 135 NARs). This is the source of truth.
- Each NAR is downloadable from
  `https://repo1.maven.org/maven2/org/apache/nifi/<artifactId>/<version>/<artifactId>-<version>.nar`.
  Sizes range from 0.5 MB to 100 MB; the total for one release is a few GB, so
  the generator caches downloads under `tools/.cache/` (git-ignored) and only
  reads the manifest entry from each archive.

Note for the catalog: a NAR listed only under an optional profile is not in the
default binary distribution. The generator records the profile so the analyzer
can say "exists in 2.x but is not in the default distribution" instead of
"removed".

## Decision

Build `tools/build_catalog.py` on the extension manifest, with the
`META-INF/services` fallback. Output
`src/flowport/catalog/generated/1.28.1__2.12.0.yaml`.
