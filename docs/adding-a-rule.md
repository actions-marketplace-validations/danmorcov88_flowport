# Adding a rule, a replacement or a migration

The catalog is data: `src/flowport/catalog/manual.yaml` holds what a
finding says, `replacements.yaml` what `migrate components` may change,
and `generated/` the component inventory built from the NAR manifests.
Detection and transformation logic lives in `src/flowport/rules/` and
`src/flowport/transforms/`.

## Adding a rule (analyzer)

1. Find the official Apache source (wiki page or JIRA issue) that documents
   the change. No source, no rule.
2. Add the rule's metadata (severity, message, suggestion, sources) to
   `src/flowport/catalog/manual.yaml`.
3. Add the detection logic in `src/flowport/rules/` and register the rule in
   `all_rules()`.
4. Add a case to `tools/make_fixtures.py` so that a real NiFi 1.x instance
   produces the situation, regenerate the fixtures, and refresh the golden
   files with `pytest --update-golden`. Add a focused test in
   `tests/unit/test_rules.py`.
5. Document the rule in the README table.

The component inventory (`src/flowport/catalog/generated/`) is not edited by
hand; run `python tools/build_catalog.py <from> <to>` to regenerate it.

## Adding a component replacement

`migrate components` applies only what `src/flowport/catalog/replacements.yaml`
lists. To add one:

1. Find the official Apache source that documents the replacement (the
   "Migrating Deprecated Components and Features for 2.0.0" page or a NiFi
   JIRA). No source, no mapping.
2. Add an entry with `from`, `to`, `kind`, `bundle`, `sources`, the property
   mapping (`properties`, `values`, `set`, `drop`), the relationship mapping
   (`relationships`, `terminate`) and `unless` guards for configurations that
   have no equivalent. The field reference is at the top of the file.
3. Run `pytest tests/unit/test_replacements.py`: it checks every name against
   the extension manifests of both releases (`catalog/generated/properties/`),
   so a typo or a forgotten property fails here.
4. Add the component to the `Replacements` group in `tools/make_fixtures.py`,
   wired to something, regenerate the fixtures and refresh the golden files.
5. Extend `tests/integration/test_nifi2_import.py` so that the migrated
   component is asserted valid on NiFi 2.x, and run `pytest -m integration`.
6. Add the row to the README table.

## Changing a migration

Transforms live in `src/flowport/transforms/`. They edit the raw document
through the model (`node.raw` is the parsed JSON object itself), record every
edit as a `Change`, and leave everything they cannot fix as a finding. When
you change what a migration produces:

1. Refresh the golden files under `tests/golden/nifi-1.28.1/migrate-*/` with
   `pytest --update-golden` and read the diff; the migrated flow, the change
   log and the post-migration report are all golden.
2. Add a synthetic case to `tests/unit/test_migrate_variables.py` for the new
   behavior.
3. Run `pytest -m integration` (Docker) to load the result into a real NiFi:
   the migrated flow into 1.x (no component may gain a validation error) and
   the converted templates into 2.x (every component must be created). If
   NiFi's behavior is the reason for the change, write it down in `docs/dev/`.

## Regenerating the catalog

```bash
python tools/build_catalog.py 1.28.1 2.12.0   # inventory diff + property tables of both releases
python tools/build_rule_reference.py          # docs/rules.md
```

The catalog builder reads the extension manifests of every NAR of both
releases from Maven Central (cached under `tools/.cache/`) and the
"Deprecated Components and Features" wiki page for JIRA ids. Commit the
generated files; nothing is fetched at runtime.

## Verifying against a real NiFi

`tests/integration/` starts the official Docker images: NiFi 1.28.1 with the
original and the migrated fixture flow, NiFi 2.12.0 with the fully migrated
flow in `conf/` and with definitions uploaded through the REST API. The
helpers live in `src/flowport/nifi/` (the same client `flowport validate`
uses). Write down what you learn in `docs/dev/`; the existing notes there
are the record of why the rules say what they say.
