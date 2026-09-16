"""Rules for framework features removed or changed in NiFi 2.x."""

from __future__ import annotations

import re
from collections.abc import Iterator

from flowport.model import ComponentKind
from flowport.rules import Context, Finding, Rule, component_location, template_location

EVENT_DRIVEN = "EVENT_DRIVEN"
CRON_DRIVEN = "CRON_DRIVEN"
INVOKE_HTTP = "org.apache.nifi.processors.standard.InvokeHTTP"
INVOKE_HTTP_PROXY_PROPERTIES = (
    "Proxy Host",
    "Proxy Port",
    "Proxy Type",
    "invokehttp-proxy-user",
    "invokehttp-proxy-password",
)
_DIGIT = re.compile(r"\d")


class EventDrivenRule(Rule):
    rule_ids = ("NIFI2-EVENT-DRIVEN",)

    def run(self, ctx: Context) -> Iterator[Finding]:
        for component in ctx.flow.components():
            if component.scheduling_strategy == EVENT_DRIVEN:
                yield ctx.finding("NIFI2-EVENT-DRIVEN", component_location(component))


class CronRule(Rule):
    """Quartz to Spring cron differences (NIFI-12290)."""

    rule_ids = ("NIFI2-CRON-YEAR-FIELD", "NIFI2-CRON-NUMERIC-DAY-OF-WEEK")

    def run(self, ctx: Context) -> Iterator[Finding]:
        for component in ctx.flow.components():
            if component.scheduling_strategy != CRON_DRIVEN or not component.scheduling_period:
                continue
            expression = component.scheduling_period.strip()
            fields = expression.split()
            location = component_location(component)
            if len(fields) == 7:
                yield ctx.finding("NIFI2-CRON-YEAR-FIELD", location, expression=expression)
            if len(fields) >= 6 and _DIGIT.search(fields[5]):
                yield ctx.finding(
                    "NIFI2-CRON-NUMERIC-DAY-OF-WEEK",
                    location,
                    expression=expression,
                    field=fields[5],
                )


class TemplateRule(Rule):
    rule_ids = ("NIFI2-TEMPLATE",)

    def run(self, ctx: Context) -> Iterator[Finding]:
        for template in ctx.flow.templates:
            group = ctx.group(str(template.raw.get("groupIdentifier", "")))
            path = group.path if group else template.path
            yield ctx.finding(
                "NIFI2-TEMPLATE", template_location(template, path), name=template.name
            )
        for group in ctx.flow.groups():
            for template in group.templates:
                yield ctx.finding(
                    "NIFI2-TEMPLATE", template_location(template, group.path), name=template.name
                )


class InvokeHttpProxyRule(Rule):
    rule_ids = ("NIFI2-INVOKEHTTP-PROXY-PROPERTIES",)

    def run(self, ctx: Context) -> Iterator[Finding]:
        for component in ctx.flow.components():
            if component.kind != ComponentKind.PROCESSOR or component.type != INVOKE_HTTP:
                continue
            # "Proxy Type" carries a default value, so only a configured host counts.
            if not component.properties.get("Proxy Host"):
                continue
            configured = [
                name for name in INVOKE_HTTP_PROXY_PROPERTIES if component.properties.get(name)
            ]
            if configured:
                yield ctx.finding(
                    "NIFI2-INVOKEHTTP-PROXY-PROPERTIES",
                    component_location(component),
                    properties=", ".join(configured),
                )
