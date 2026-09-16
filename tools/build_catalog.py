"""Generate the component inventory diff between two NiFi releases.

For each release the script:

1. reads ``org.apache.nifi:nifi-assembly:<version>:pom`` from Maven Central to
   list every NAR of the distribution (including NARs under optional
   ``include-*`` profiles),
2. fetches ``META-INF/docs/extension-manifest.xml`` from each NAR with HTTP
   range requests (only the zip central directory and the manifest entry are
   downloaded, not the whole archive),
3. collects component types, bundles, deprecation notices and, for scripted
   components, the allowed script engines.

It then writes ``src/flowport/catalog/generated/<from>__<to>.yaml`` with the
types that were removed, moved to another bundle, or deprecated in the target
release. JIRA ids come from the Apache wiki page "Deprecated Components and
Features" when the class name matches.

Usage::

    python tools/build_catalog.py 1.28.1 2.12.0

Development-time only. Cached downloads live in ``tools/.cache/``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import struct
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MAVEN = "https://repo1.maven.org/maven2"
WIKI_DEPRECATED = (
    "https://cwiki.apache.org/confluence/display/NIFI/Deprecated+Components+and+Features"
)
MANIFEST_ENTRY = "META-INF/docs/extension-manifest.xml"
POM_NS = {"m": "http://maven.apache.org/POM/4.0.0"}
CACHE = Path(__file__).resolve().parent / ".cache"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "flowport" / "catalog" / "generated"
USER_AGENT = "flowport-build-catalog"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read(), {k.lower(): v for k, v in resp.headers.items()}


def cached(path: Path, fetch: Callable[[], bytes]) -> bytes:
    if path.exists():
        return path.read_bytes()
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


class RemoteZip:
    """Read single entries from a zip file over HTTP without downloading it."""

    def __init__(self, url: str) -> None:
        self.url = url
        tail, headers = http_get(url, {"Range": "bytes=-65536"})
        match = re.search(r"/(\d+)$", headers.get("content-range", ""))
        if match is None:
            raise RuntimeError(f"{url}: server does not support range requests")
        self.size = int(match.group(1))
        self.tail_offset = self.size - len(tail)
        self.entries = self._parse_central_directory(tail)

    def _range(self, start: int, length: int) -> bytes:
        data, _ = http_get(self.url, {"Range": f"bytes={start}-{start + length - 1}"})
        return data

    def _parse_central_directory(self, tail: bytes) -> dict[str, tuple[int, int, int, int]]:
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            raise RuntimeError(f"{self.url}: end of central directory not found")
        cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12 : eocd + 20])
        if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
            raise RuntimeError(f"{self.url}: zip64 archives are not supported")
        if cd_offset >= self.tail_offset:
            cd = tail[cd_offset - self.tail_offset : cd_offset - self.tail_offset + cd_size]
        else:
            cd = self._range(cd_offset, cd_size)
        entries: dict[str, tuple[int, int, int, int]] = {}
        pos = 0
        while pos + 46 <= len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
            (method, csize, usize, name_len, extra_len, comment_len, local_offset) = struct.unpack(
                "<H8xIIHHH8xI", cd[pos + 10 : pos + 46]
            )
            name = cd[pos + 46 : pos + 46 + name_len].decode("utf-8", errors="replace")
            entries[name] = (method, csize, usize, local_offset)
            pos += 46 + name_len + extra_len + comment_len
        return entries

    def read(self, name: str) -> bytes | None:
        entry = self.entries.get(name)
        if entry is None:
            return None
        method, csize, _usize, local_offset = entry
        header = self._range(local_offset, 30)
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        data = self._range(local_offset + 30 + name_len + extra_len, csize)
        if method == 0:
            return data
        if method == 8:
            return zlib.decompress(data, -15)
        raise RuntimeError(f"{self.url}: unsupported compression method {method} for {name}")


# ---------------------------------------------------------------------------
# Release inventory
# ---------------------------------------------------------------------------


@dataclass
class ExtensionType:
    name: str
    kind: str
    group: str
    artifact: str
    profile: str | None  # None = default distribution
    deprecated: bool = False
    deprecation_reason: str | None = None
    alternatives: list[str] = field(default_factory=list)
    script_engines: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)  # name -> EL scope
    dynamic_scope: str | None = None  # EL scope of dynamic properties, None if unsupported


@dataclass
class Release:
    version: str
    nars: dict[str, str | None]  # artifactId -> profile
    types: dict[str, ExtensionType]
    missing_manifests: list[str]


def assembly_nars(version: str) -> dict[str, str | None]:
    url = f"{MAVEN}/org/apache/nifi/nifi-assembly/{version}/nifi-assembly-{version}.pom"
    data = cached(CACHE / "poms" / f"nifi-assembly-{version}.pom", lambda: http_get(url)[0])
    root = ET.fromstring(data)
    nars: dict[str, str | None] = {}

    def collect(container: ET.Element, profile: str | None) -> None:
        for dep in container.findall("m:dependencies/m:dependency", POM_NS):
            if dep.findtext("m:type", default="", namespaces=POM_NS) != "nar":
                continue
            if dep.findtext("m:groupId", default="", namespaces=POM_NS) != "org.apache.nifi":
                continue
            artifact = dep.findtext("m:artifactId", default="", namespaces=POM_NS)
            if artifact and artifact not in nars:
                nars[artifact] = profile

    collect(root, None)
    for profile in root.findall("m:profiles/m:profile", POM_NS):
        profile_id = profile.findtext("m:id", default="", namespaces=POM_NS)
        if profile_id.startswith("include-") and profile_id != "include-all":
            collect(profile, profile_id)
    return nars


def fetch_manifest(artifact: str, version: str) -> bytes | None:
    path = CACHE / "manifests" / version / f"{artifact}.xml"
    if path.exists():
        data = path.read_bytes()
        return data or None
    url = f"{MAVEN}/org/apache/nifi/{artifact}/{version}/{artifact}-{version}.nar"
    try:
        data = RemoteZip(url).read(MANIFEST_ENTRY)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"  {artifact}-{version}.nar: not on Maven Central", file=sys.stderr)
            data = None
        else:
            raise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data or b"")
    return data


def parse_manifest(data: bytes, profile: str | None) -> list[ExtensionType]:
    root = ET.fromstring(data)
    group = root.findtext("groupId", default="")
    artifact = root.findtext("artifactId", default="")
    types: list[ExtensionType] = []
    for ext in root.findall("extensions/extension"):
        name = ext.findtext("name", default="")
        kind = ext.findtext("type", default="")
        notice = ext.find("deprecationNotice")
        engines: list[str] = []
        properties: dict[str, str] = {}
        for prop in ext.findall("properties/property"):
            prop_name = prop.findtext("name", default="")
            # The expressionLanguageSupported flag is unreliable in the manifest (always
            # false for dynamic properties); the scope element is authoritative.
            properties[prop_name] = prop.findtext("expressionLanguageScope", default="NONE")
            if prop_name == "Script Engine":
                engines = [
                    v.findtext("value", default="")
                    for v in prop.findall("allowableValues/allowableValue")
                ]
        dynamic_scope: str | None = None
        for dyn in ext.findall("dynamicProperties/dynamicProperty"):
            scope = dyn.findtext("expressionLanguageScope", default="NONE")
            if dynamic_scope is None or scope != "NONE":
                dynamic_scope = scope
        types.append(
            ExtensionType(
                name=name,
                kind=kind,
                group=group,
                artifact=artifact,
                profile=profile,
                deprecated=notice is not None,
                deprecation_reason=(notice.findtext("reason") if notice is not None else None),
                alternatives=(
                    [a.text or "" for a in notice.findall("alternatives/alternative")]
                    if notice is not None
                    else []
                ),
                script_engines=engines,
                properties=properties,
                dynamic_scope=dynamic_scope,
            )
        )
    return types


def load_release(version: str) -> Release:
    nars = assembly_nars(version)
    print(f"NiFi {version}: {len(nars)} NARs in the assembly", file=sys.stderr)
    types: dict[str, ExtensionType] = {}
    missing: list[str] = []
    for index, (artifact, profile) in enumerate(sorted(nars.items()), 1):
        data = fetch_manifest(artifact, version)
        if data is None:
            missing.append(artifact)
            continue
        for ext in parse_manifest(data, profile):
            existing = types.get(ext.name)
            # Prefer the default distribution when a type ships in several NARs.
            if existing is None or (existing.profile is not None and ext.profile is None):
                types[ext.name] = ext
        print(f"  [{index}/{len(nars)}] {artifact}", file=sys.stderr)
    print(f"NiFi {version}: {len(types)} extension types", file=sys.stderr)
    return Release(version=version, nars=nars, types=types, missing_manifests=missing)


# ---------------------------------------------------------------------------
# Wiki cross-reference
# ---------------------------------------------------------------------------


def wiki_jira_ids() -> dict[str, list[str]]:
    """Map component simple name -> JIRA ids from the deprecated components table."""
    data = cached(
        CACHE / "wiki" / "deprecated-components.html", lambda: http_get(WIKI_DEPRECATED)[0]
    )
    text = data.decode("utf-8", errors="replace")
    result: dict[str, list[str]] = {}
    for table in re.findall(r"<table.*?</table>", text, re.S):
        for row in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = re.findall(r"<t[dh].*?</t[dh]>", row, re.S)
            if len(cells) < 5:
                continue
            name = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
            jiras = sorted(set(re.findall(r"NIFI-\d+", cells[4])))
            if name and jiras and " " not in name:
                result.setdefault(name, [])
                for jira in jiras:
                    if jira not in result[name]:
                        result[name].append(jira)
    return result


# ---------------------------------------------------------------------------
# Diff and output
# ---------------------------------------------------------------------------


def build(source: Release, target: Release, jiras: dict[str, list[str]]) -> dict[str, object]:
    removed: list[dict[str, object]] = []
    renamed: list[dict[str, object]] = []
    moved: list[dict[str, object]] = []
    target_by_simple: dict[str, list[ExtensionType]] = {}
    for t in target.types.values():
        target_by_simple.setdefault(t.name.rsplit(".", 1)[-1], []).append(t)
    optional: list[dict[str, object]] = []
    deprecated: list[dict[str, object]] = []

    for name in sorted(source.types):
        old = source.types[name]
        new = target.types.get(name)
        simple = name.rsplit(".", 1)[-1]
        if new is None:
            entry: dict[str, object] = {
                "type": name,
                "kind": old.kind,
                "bundle": {"group": old.group, "artifact": old.artifact},
            }
            if old.deprecated:
                entry["deprecated_in_source"] = True
                if old.deprecation_reason:
                    entry["deprecation_reason"] = old.deprecation_reason
                if old.alternatives:
                    entry["alternatives"] = sorted(old.alternatives)
            if simple in jiras:
                entry["jira"] = jiras[simple]
            candidates = [
                t for t in target_by_simple.get(simple, []) if t.kind == old.kind and t.name != name
            ]
            if len(candidates) == 1 and name not in target.types:
                entry["to_type"] = candidates[0].name
                entry["to_bundle"] = {
                    "group": candidates[0].group,
                    "artifact": candidates[0].artifact,
                }
                renamed.append(entry)
            else:
                removed.append(entry)
            continue
        if new.artifact != old.artifact:
            moved.append(
                {
                    "type": name,
                    "kind": old.kind,
                    "from_bundle": {"group": old.group, "artifact": old.artifact},
                    "to_bundle": {"group": new.group, "artifact": new.artifact},
                }
            )
        if new.profile is not None and old.profile is None:
            optional.append(
                {
                    "type": name,
                    "kind": old.kind,
                    "bundle": {"group": new.group, "artifact": new.artifact},
                    "profile": new.profile,
                }
            )
        if new.deprecated:
            entry = {"type": name, "kind": old.kind}
            if new.deprecation_reason:
                entry["deprecation_reason"] = new.deprecation_reason
            if new.alternatives:
                entry["alternatives"] = sorted(new.alternatives)
            deprecated.append(entry)

    script_engines = {
        name: {"source": source.types[name].script_engines, "target": t.script_engines}
        for name, t in sorted(target.types.items())
        if t.script_engines and name in source.types
    }

    return {
        "source_version": source.version,
        "target_version": target.version,
        "generated": dt.date.today().isoformat(),
        "sources": [
            f"{MAVEN}/org/apache/nifi/nifi-assembly/{source.version}/nifi-assembly-{source.version}.pom",
            f"{MAVEN}/org/apache/nifi/nifi-assembly/{target.version}/nifi-assembly-{target.version}.pom",
            WIKI_DEPRECATED,
        ],
        "counts": {
            "source_types": len(source.types),
            "target_types": len(target.types),
            "removed": len(removed),
            "renamed": len(renamed),
            "moved": len(moved),
            "optional": len(optional),
            "deprecated_in_target": len(deprecated),
        },
        "nars_without_manifest": {
            source.version: sorted(source.missing_manifests),
            target.version: sorted(target.missing_manifests),
        },
        "removed": removed,
        "renamed": renamed,
        "moved": moved,
        "optional": optional,
        "deprecated_in_target": deprecated,
        "script_engines": script_engines,
    }


def build_properties(release: Release) -> dict[str, object]:
    """Per-type property table with Expression Language scopes (one release)."""
    types: dict[str, object] = {}
    for name in sorted(release.types):
        ext = release.types[name]
        entry: dict[str, object] = {
            "kind": ext.kind,
            "properties": dict(sorted(ext.properties.items())),
        }
        if ext.dynamic_scope is not None:
            entry["dynamic"] = ext.dynamic_scope
        types[name] = entry
    return {
        "version": release.version,
        "generated": dt.date.today().isoformat(),
        "sources": [
            f"{MAVEN}/org/apache/nifi/nifi-assembly/{release.version}/nifi-assembly-{release.version}.pom",
        ],
        "types": types,
    }


HEADER = """\
# Generated by tools/build_catalog.py. Do not edit by hand.
#
# Component inventory diff between Apache NiFi {source} and {target}, built from
# the extension manifests (META-INF/docs/extension-manifest.xml) of every NAR
# listed in the nifi-assembly POM of each release, including NARs under
# optional include-* profiles.
#
#   removed              type exists in {source} but in no {target} NAR
#   renamed              type exists in {source} but not in {target}, and exactly
#                        one {target} type of the same kind has the same simple
#                        class name in another package (candidate replacement)
#   moved                same type, different bundle artifact in {target}
#   optional             type is in {target} only under an optional profile
#                        (not in the default binary distribution)
#   deprecated_in_target type carries a deprecation notice in {target}
#   script_engines       allowed "Script Engine" values per scripted component
#
"""

PROPERTIES_HEADER = """# Generated by tools/build_catalog.py. Do not edit by hand.
#
# Every extension type of Apache NiFi {version} with its properties and the
# Expression Language scope of each (NONE, VARIABLE_REGISTRY, ENVIRONMENT or
# FLOWFILE_ATTRIBUTES). "dynamic" is the scope of dynamic properties when the
# type supports them.
#
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source_version")
    parser.add_argument("target_version")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = load_release(args.source_version)
    target = load_release(args.target_version)
    catalog = build(source, target, wiki_jira_ids())

    output = args.output or OUTPUT_DIR / f"{args.source_version}__{args.target_version}.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(HEADER.format(source=args.source_version, target=args.target_version))
        yaml.safe_dump(catalog, fh, sort_keys=False, allow_unicode=True, width=100)
    print(f"wrote {output}: {catalog['counts']}", file=sys.stderr)

    properties_output = output.parent / "properties" / f"{args.source_version}.yaml"
    properties_output.parent.mkdir(parents=True, exist_ok=True)
    with properties_output.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(PROPERTIES_HEADER.format(version=args.source_version))
        yaml.safe_dump(build_properties(source), fh, sort_keys=False, allow_unicode=True, width=100)
    print(f"wrote {properties_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
