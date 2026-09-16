# flowport: analyze and migrate Apache NiFi 1.x flows for NiFi 2.x.
#
#   docker run --rm -v "$PWD:/work" ghcr.io/danmorcov88/flowport analyze /work/flow.json.gz
#
# The image has no Docker client, so `flowport validate --docker` does not
# work inside it; use `--nifi-url` against a reachable NiFi instead.
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /dist

FROM python:3.13-slim
LABEL org.opencontainers.image.title="flowport" \
      org.opencontainers.image.description="Analyze and migrate Apache NiFi 1.x flows for NiFi 2.x" \
      org.opencontainers.image.source="https://github.com/danmorcov88/flowport" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl \
    && useradd --create-home --uid 1000 flowport
USER flowport
WORKDIR /work
ENTRYPOINT ["flowport"]
CMD ["--help"]
