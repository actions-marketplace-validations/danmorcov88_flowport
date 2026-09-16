"""Rule catalog: generated component inventory plus hand-written rule definitions.

All files ship inside the package; nothing is fetched at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

DEFAULT_SOURCE_VERSION = "1.28.1"
DEFAULT_TARGET_VERSION = "2.12.0"
JIRA_URL = "https://issues.apache.org/jira/browse/"


class CatalogError(Exception):
    """A catalog file is missing or malformed."""


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    severity: str
    category: str
    title: str
    message: str
    suggestion: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class RemovedType:
    type: str
    kind: str
    bundle_artifact: str
    deprecated_in_source: bool
    deprecation_reason: str | None
    alternatives: tuple[str, ...]
    jira: tuple[str, ...]


@dataclass(frozen=True)
class RenamedType:
    type: str
    kind: str
    to_type: str
    to_bundle_group: str
    to_bundle_artifact: str
    jira: tuple[str, ...]


@dataclass(frozen=True)
class OptionalType:
    type: str
    kind: str
    bundle_artifact: str
    profile: str


@dataclass(frozen=True)
class DeprecatedType:
    type: str
    kind: str
    reason: str | None
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class TypeProperties:
    kind: str
    properties: dict[str, str]  # property name -> EL scope
    dynamic: str | None  # EL scope of dynamic properties, None if unsupported
    relationships: tuple[str, ...] = ()


@dataclass(frozen=True)
class Condition:
    """A property value under which a replacement must not be applied."""

    property: str
    equals: str
    reason: str


@dataclass(frozen=True)
class Replacement:
    """A documented 1:1 component replacement (see replacements.yaml)."""

    from_type: str
    to_type: str
    kind: str
    bundle_group: str
    bundle_artifact: str
    sources: tuple[str, ...]
    properties: dict[str, str]  # old name -> new name
    values: dict[str, dict[str, str]]  # new name -> {old value: new value}
    set: dict[str, str]  # new name -> value
    drop: dict[str, str | None]  # old name -> source default (reported only when different)
    relationships: dict[str, tuple[str, ...]]  # old -> new relationships
    terminate: tuple[str, ...]
    unless: tuple[Condition, ...]
    note: str


@dataclass
class Catalog:
    source_version: str
    target_version: str
    removed: dict[str, RemovedType] = field(default_factory=dict)
    renamed: dict[str, RenamedType] = field(default_factory=dict)
    optional: dict[str, OptionalType] = field(default_factory=dict)
    deprecated_in_target: dict[str, DeprecatedType] = field(default_factory=dict)
    script_engines: dict[str, tuple[str, ...]] = field(default_factory=dict)  # allowed in target
    properties: dict[str, TypeProperties] = field(default_factory=dict)  # source release types
    target_properties: dict[str, TypeProperties] = field(default_factory=dict)
    replacements: dict[str, Replacement] = field(default_factory=dict)  # by source type
    rules: dict[str, RuleSpec] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    # -- lookups ------------------------------------------------------------

    def knows_type(self, type_name: str) -> bool:
        """True if the type shipped with the source release."""
        return type_name in self.properties

    def knows_target_type(self, type_name: str) -> bool:
        """True if the type ships with the target release."""
        return type_name in self.target_properties

    def property_scope(self, type_name: str, property_name: str) -> str | None:
        """Expression Language scope of a property, or None when the type is unknown.

        A property that is not declared by the type is a dynamic property and gets
        the dynamic scope, or NONE when the type does not support dynamic properties.
        """
        info = self.properties.get(type_name)
        if info is None:
            return None
        scope = info.properties.get(property_name)
        if scope is not None:
            return scope
        return info.dynamic or "NONE"

    def rule(self, rule_id: str) -> RuleSpec:
        try:
            return self.rules[rule_id]
        except KeyError:
            raise CatalogError(f"rule {rule_id} is not defined in manual.yaml") from None

    def resolve_source(self, ref: str) -> str:
        if ref.startswith("http://") or ref.startswith("https://"):
            return ref
        if ref.startswith("NIFI-"):
            return JIRA_URL + ref
        try:
            return self.sources[ref]
        except KeyError:
            raise CatalogError(f"unknown source reference {ref!r}") from None


def _read_yaml(name: str) -> Any:
    package = resources.files("flowport.catalog")
    try:
        with package.joinpath(name).open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        raise CatalogError(f"catalog file {name} is not available") from None


def load_catalog(
    source_version: str = DEFAULT_SOURCE_VERSION,
    target_version: str = DEFAULT_TARGET_VERSION,
) -> Catalog:
    generated = _read_yaml(f"generated/{source_version}__{target_version}.yaml")
    properties = _read_yaml(f"generated/properties/{source_version}.yaml")
    target_properties = _read_yaml(f"generated/properties/{target_version}.yaml")
    manual = _read_yaml("manual.yaml")
    replacements = _read_yaml("replacements.yaml")

    catalog = Catalog(source_version=source_version, target_version=target_version)
    catalog.sources = {str(k): str(v) for k, v in (manual.get("sources") or {}).items()}

    for rule_id, spec in (manual.get("rules") or {}).items():
        sources = tuple(catalog.resolve_source(str(s)) for s in spec.get("sources") or [])
        if not sources:
            raise CatalogError(f"rule {rule_id} has no source")
        catalog.rules[rule_id] = RuleSpec(
            rule_id=str(rule_id),
            severity=str(spec["severity"]),
            category=str(spec["category"]),
            title=str(spec["title"]),
            message=str(spec["message"]),
            suggestion=str(spec.get("suggestion", "")),
            sources=sources,
        )

    for entry in generated.get("removed") or []:
        catalog.removed[entry["type"]] = RemovedType(
            type=entry["type"],
            kind=entry["kind"],
            bundle_artifact=entry["bundle"]["artifact"],
            deprecated_in_source=bool(entry.get("deprecated_in_source", False)),
            deprecation_reason=entry.get("deprecation_reason"),
            alternatives=tuple(entry.get("alternatives") or []),
            jira=tuple(entry.get("jira") or []),
        )
    for entry in generated.get("renamed") or []:
        catalog.renamed[entry["type"]] = RenamedType(
            type=entry["type"],
            kind=entry["kind"],
            to_type=entry["to_type"],
            to_bundle_group=entry["to_bundle"]["group"],
            to_bundle_artifact=entry["to_bundle"]["artifact"],
            jira=tuple(entry.get("jira") or []),
        )
    for entry in generated.get("optional") or []:
        catalog.optional[entry["type"]] = OptionalType(
            type=entry["type"],
            kind=entry["kind"],
            bundle_artifact=entry["bundle"]["artifact"],
            profile=entry["profile"],
        )
    for entry in generated.get("deprecated_in_target") or []:
        catalog.deprecated_in_target[entry["type"]] = DeprecatedType(
            type=entry["type"],
            kind=entry["kind"],
            reason=entry.get("deprecation_reason"),
            alternatives=tuple(entry.get("alternatives") or []),
        )
    for type_name, engines in (generated.get("script_engines") or {}).items():
        catalog.script_engines[type_name] = tuple(engines.get("target") or [])
    catalog.properties = _type_properties(properties)
    catalog.target_properties = _type_properties(target_properties)
    for entry in replacements.get("replacements") or []:
        replacement = _replacement(entry, catalog)
        catalog.replacements[replacement.from_type] = replacement
    return catalog


def _type_properties(document: Any) -> dict[str, TypeProperties]:
    result: dict[str, TypeProperties] = {}
    for type_name, info in (document.get("types") or {}).items():
        result[type_name] = TypeProperties(
            kind=str(info.get("kind", "")),
            properties={str(k): str(v) for k, v in (info.get("properties") or {}).items()},
            dynamic=info.get("dynamic"),
            relationships=tuple(str(r) for r in info.get("relationships") or []),
        )
    return result


def _replacement(entry: dict[str, Any], catalog: Catalog) -> Replacement:
    try:
        from_type = str(entry["from"])
        to_type = str(entry["to"])
        bundle = entry["bundle"]
        sources = tuple(catalog.resolve_source(str(s)) for s in entry.get("sources") or [])
        if not sources:
            raise KeyError("sources")
        return Replacement(
            from_type=from_type,
            to_type=to_type,
            kind=str(entry.get("kind", "PROCESSOR")),
            bundle_group=str(bundle["group"]),
            bundle_artifact=str(bundle["artifact"]),
            sources=sources,
            properties={str(k): str(v) for k, v in (entry.get("properties") or {}).items()},
            values={
                str(k): {str(a): str(b) for a, b in (v or {}).items()}
                for k, v in (entry.get("values") or {}).items()
            },
            set={str(k): str(v) for k, v in (entry.get("set") or {}).items()},
            drop={
                str(k): (None if v is None else str(v))
                for k, v in (entry.get("drop") or {}).items()
            },
            relationships={
                str(k): tuple(str(r) for r in (v or []))
                for k, v in (entry.get("relationships") or {}).items()
            },
            terminate=tuple(str(r) for r in entry.get("terminate") or []),
            unless=tuple(
                Condition(
                    property=str(c["property"]), equals=str(c["equals"]), reason=str(c["reason"])
                )
                for c in entry.get("unless") or []
            ),
            note=str(entry.get("note") or ""),
        )
    except KeyError as exc:
        raise CatalogError(
            f"replacement {entry.get('from', '?')} in replacements.yaml lacks {exc}"
        ) from None
