"""Rules driven by the generated component inventory."""

from __future__ import annotations

from collections.abc import Iterator

from flowport.rules import Context, Finding, Rule, component_location

APACHE_GROUP = "org.apache.nifi"
SCRIPT_ENGINE_PROPERTY = "Script Engine"


def _alternatives_text(alternatives: tuple[str, ...]) -> str:
    if not alternatives:
        return ""
    names = ", ".join(f"{a.rsplit('.', 1)[-1]} ({a})" for a in alternatives)
    return f" Documented alternatives: {names}."


class ComponentInventoryRule(Rule):
    """Removed, renamed, optional, deprecated, unknown and third-party component types."""

    rule_ids = (
        "NIFI2-REMOVED-COMPONENT",
        "NIFI2-RENAMED-COMPONENT",
        "NIFI2-OPTIONAL-BUNDLE",
        "NIFI2-DEPRECATED-IN-TARGET",
        "NIFI2-UNKNOWN-COMPONENT",
        "NIFI2-THIRD-PARTY-BUNDLE",
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        catalog = ctx.catalog
        for component in ctx.flow.components():
            location = component_location(component)
            type_name = component.type
            if component.bundle.group != APACHE_GROUP:
                yield ctx.finding(
                    "NIFI2-THIRD-PARTY-BUNDLE",
                    location,
                    bundle=component.bundle.coordinate,
                )
                continue
            removed = catalog.removed.get(type_name)
            if removed is not None:
                yield ctx.finding(
                    "NIFI2-REMOVED-COMPONENT",
                    location,
                    extra_sources=removed.jira,
                    alternatives=_alternatives_text(removed.alternatives),
                )
                continue
            renamed = catalog.renamed.get(type_name)
            if renamed is not None:
                yield ctx.finding(
                    "NIFI2-RENAMED-COMPONENT",
                    location,
                    extra_sources=renamed.jira,
                    to_type=renamed.to_type,
                    to_bundle=f"{renamed.to_bundle_group}:{renamed.to_bundle_artifact}",
                )
                continue
            if catalog.knows_target_type(type_name) and not catalog.knows_type(type_name):
                continue  # already a type of the target release (for example after a replacement)
            if not catalog.knows_type(type_name):
                yield ctx.finding(
                    "NIFI2-UNKNOWN-COMPONENT",
                    location,
                    bundle=component.bundle.coordinate,
                )
                continue
            optional = catalog.optional.get(type_name)
            if optional is not None:
                yield ctx.finding(
                    "NIFI2-OPTIONAL-BUNDLE",
                    location,
                    artifact=optional.bundle_artifact,
                    profile=optional.profile,
                )
            deprecated = catalog.deprecated_in_target.get(type_name)
            if deprecated is not None:
                yield ctx.finding(
                    "NIFI2-DEPRECATED-IN-TARGET",
                    location,
                    reason=deprecated.reason or "no reason given in the component documentation",
                    alternatives=_alternatives_text(deprecated.alternatives),
                )


class ScriptEngineRule(Rule):
    """Scripted components configured with an engine the target release does not ship."""

    rule_ids = ("NIFI2-SCRIPT-ENGINE",)

    def run(self, ctx: Context) -> Iterator[Finding]:
        for component in ctx.flow.components():
            allowed = ctx.catalog.script_engines.get(component.type)
            if allowed is None:
                continue
            engine = component.properties.get(SCRIPT_ENGINE_PROPERTY)
            if engine is None or engine in allowed:
                continue
            yield ctx.finding(
                "NIFI2-SCRIPT-ENGINE",
                component_location(component),
                engine=engine,
                allowed=", ".join(allowed),
            )
