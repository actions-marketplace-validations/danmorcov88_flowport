"""Every replacement in replacements.yaml is consistent with the extension manifests.

The generated property tables of both releases (``catalog/generated/properties``)
come from the NARs' extension manifests, so a mapping that names a property or
relationship that does not exist fails here, before any container test.
"""

import pytest

from flowport.catalog import Catalog, Replacement, load_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def replacements(catalog: Catalog) -> list[Replacement]:
    return sorted(catalog.replacements.values(), key=lambda r: r.from_type)


def test_replacements_are_loaded(catalog: Catalog) -> None:
    assert len(catalog.replacements) >= 8
    for replacement in replacements(catalog):
        assert replacement.sources, replacement.from_type
        assert all(s.startswith("https://") for s in replacement.sources)
        assert replacement.note


@pytest.mark.parametrize(
    "from_type", sorted(load_catalog().replacements), ids=lambda t: t.rsplit(".", 1)[-1]
)
def test_replacement_matches_the_manifests(catalog: Catalog, from_type: str) -> None:
    replacement = catalog.replacements[from_type]
    source = catalog.properties[from_type]
    assert from_type in catalog.removed or from_type in catalog.renamed, "source type still exists"
    target = catalog.target_properties[replacement.to_type]
    assert source.kind == target.kind == replacement.kind

    # Old names must exist on the source type; new names on the target type.
    for old, new in replacement.properties.items():
        assert old in source.properties, (from_type, old)
        assert new in target.properties, (replacement.to_type, new)
    for name in replacement.set:
        assert name in target.properties, (replacement.to_type, name)
    for name in replacement.values:
        assert name in target.properties or name in replacement.set, name
    for name in replacement.drop:
        assert name in source.properties, (from_type, name)
    for condition in replacement.unless:
        assert condition.property in source.properties, condition

    # Every declared source property is handled: renamed, dropped, or kept with
    # the same name on the target. Nothing may disappear by accident.
    for name in source.properties:
        handled = (
            name in replacement.properties or name in replacement.drop or name in target.properties
        )
        assert handled, f"{from_type}: property {name!r} is neither mapped, dropped nor kept"

    # Relationships: every source relationship maps somewhere, every target
    # relationship is reached or auto-terminated.
    if source.kind == "PROCESSOR":
        for old, new in replacement.relationships.items():
            assert old in source.relationships, (from_type, old)
            for name in new:
                assert name in target.relationships, (replacement.to_type, name)
        for name in replacement.terminate:
            assert name in target.relationships, (replacement.to_type, name)
        reached = {r for new in replacement.relationships.values() for r in new}
        reached |= set(replacement.terminate)
        for old in source.relationships:
            assert old in replacement.relationships or old in target.relationships, (
                f"{from_type}: relationship {old!r} has no mapping"
            )
            if old not in replacement.relationships:
                reached.add(old)
        for new in target.relationships:
            assert new in reached, f"{replacement.to_type}: relationship {new!r} is never reached"


def test_replacement_types_carry_the_target_bundle(catalog: Catalog) -> None:
    for replacement in replacements(catalog):
        assert replacement.bundle_group == "org.apache.nifi"
        assert replacement.bundle_artifact.endswith("-nar")
