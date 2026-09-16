"""Render the rule reference (``docs/rules.md``) from the catalog.

Every finding links to its rule's section, so the page must match the
catalog; ``tests/unit/test_rule_reference.py`` fails when it does not, and
``pytest --update-golden`` (or ``python tools/build_rule_reference.py``)
regenerates it.
"""

from __future__ import annotations

from flowport.catalog import Catalog, RuleSpec

RULE_REFERENCE_URL = "https://github.com/danmorcov88/flowport/blob/main/docs/rules.md"
SEVERITY_ORDER = ("BLOCKER", "MANUAL", "AUTO_FIXABLE", "INFO")
SEVERITY_TEXT = {
    "BLOCKER": "NiFi 2.x does not start, or the component loads as an invalid ghost.",
    "MANUAL": "Needs a human decision; flowport does not change it.",
    "AUTO_FIXABLE": "A `flowport migrate` command fixes it.",
    "INFO": "Works, but worth knowing.",
}
# Which command produces a rule that the analyzer itself never emits.
REPORTED_BY = {
    "NIFI2-VARIABLE-PARAMETER-COLLISION": "migrate variables",
    "NIFI2-VARIABLE-REFERENCE-NOT-VISIBLE": "migrate variables",
    "NIFI2-COMPONENT-REPLACED": "migrate components",
    "NIFI2-REPLACEMENT-SKIPPED": "migrate components",
    "NIFI2-REPLACED-PROPERTY-DROPPED": "migrate components",
    "NIFI2-SCHEDULING-CHANGED": "migrate components",
}


def anchor(rule_id: str) -> str:
    return rule_id.lower()


def reference_url(rule_id: str) -> str:
    return f"{RULE_REFERENCE_URL}#{anchor(rule_id)}"


def render_rule_reference(catalog: Catalog) -> str:
    lines: list[str] = [
        "# Rule reference",
        "",
        "Generated from `src/flowport/catalog/manual.yaml` and "
        "`src/flowport/catalog/replacements.yaml` by `tools/build_rule_reference.py`; "
        "do not edit by hand. Every finding carries a `reference` link to its section here.",
        "",
        f"Catalog: NiFi {catalog.source_version} to {catalog.target_version}.",
        "",
        "## Severities",
        "",
        "| Severity | Meaning |",
        "|---|---|",
    ]
    lines += [f"| {s} | {SEVERITY_TEXT[s]} |" for s in SEVERITY_ORDER]
    lines += ["", "## Rules", "", "| Rule | Severity | Category | Title |", "|---|---|---|---|"]
    rules = sorted(catalog.rules.values(), key=_rule_order)
    for rule in rules:
        lines.append(
            f"| [{rule.rule_id}](#{anchor(rule.rule_id)}) | {rule.severity} | "
            f"{rule.category} | {rule.title} |"
        )
    for rule in rules:
        lines += ["", f"### {rule.rule_id}", ""]
        lines.append(f"**{rule.title}** — severity {rule.severity}, category `{rule.category}`.")
        reported_by = REPORTED_BY.get(rule.rule_id, "analyze")
        lines.append(f"Reported by `flowport {reported_by}`.")
        lines += ["", f"Message: {_template(rule.message)}", ""]
        if rule.suggestion:
            lines += [f"Suggestion: {_template(rule.suggestion)}", ""]
        lines.append("Sources:")
        lines += [f"- <{source}>" for source in rule.sources]
    lines += [
        "",
        "## Component replacements",
        "",
        "Applied by `flowport migrate components`; defined in `replacements.yaml`.",
        "",
        "| From | To | Bundle | Sources |",
        "|---|---|---|---|",
    ]
    for replacement in sorted(catalog.replacements.values(), key=lambda r: r.from_type):
        sources = ", ".join(f"<{s}>" for s in replacement.sources)
        lines.append(
            f"| `{replacement.from_type}` | `{replacement.to_type}` | "
            f"`{replacement.bundle_artifact}` | {sources} |"
        )
    for replacement in sorted(catalog.replacements.values(), key=lambda r: r.from_type):
        short = replacement.from_type.rsplit(".", 1)[-1]
        lines += ["", f"### {short}", "", replacement.note, ""]
        if replacement.properties:
            lines.append(
                "Properties renamed: "
                + ", ".join(f"`{old}` to `{new}`" for old, new in replacement.properties.items())
            )
        if replacement.set:
            lines.append(
                "Properties set: "
                + ", ".join(f"`{name}` = `{value}`" for name, value in replacement.set.items())
            )
        if replacement.drop:
            lines.append("Properties dropped: " + ", ".join(f"`{n}`" for n in replacement.drop))
        if replacement.relationships:
            lines.append(
                "Relationships: "
                + ", ".join(
                    f"`{old}` to {', '.join(f'`{n}`' for n in new)}"
                    for old, new in replacement.relationships.items()
                )
            )
        if replacement.terminate:
            lines.append("Auto-terminated: " + ", ".join(f"`{n}`" for n in replacement.terminate))
        for condition in replacement.unless:
            lines.append(
                f"Not applied when `{condition.property}` is `{condition.equals}`: "
                f"{condition.reason}."
            )
    return "\n".join(lines) + "\n"


def _rule_order(rule: RuleSpec) -> tuple[int, str]:
    return (SEVERITY_ORDER.index(rule.severity), rule.rule_id)


class _Placeholders(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "<" + key + ">"


def _template(text: str) -> str:
    """Render a message template with its fields shown as `<field>`."""
    return text.format_map(_Placeholders()).replace("|", "\\|")
