# flowport

A migration tool for Apache NiFi: analyze NiFi 1.x flows for NiFi 2.x
incompatibilities and apply the changes that can be made safely.

> Status: early development. Nothing is released yet.

## What it will do

- `flowport analyze flow.json.gz` — report every component, variable, template,
  scheduling strategy or script engine in a 1.x flow that NiFi 2.x no longer
  supports, with the exact location and a link to the official Apache source.
- `flowport migrate ...` — apply safe, mechanical changes (variables to
  parameters, templates to flow definitions, documented 1:1 component
  replacements) to a copy of the flow, with a change log of every edit.

The tool works offline on files. It never modifies its input.

## Requirements

Python 3.11 or newer.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy
pytest
```

Integration tests need Docker and are skipped by default:

```bash
pytest -m integration
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. This project is not affiliated with or endorsed by the
Apache Software Foundation. Apache NiFi is a trademark of the Apache Software
Foundation.
