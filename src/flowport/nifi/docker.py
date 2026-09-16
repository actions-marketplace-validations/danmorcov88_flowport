"""Start a throwaway NiFi in Docker for ``flowport validate --docker`` and the tests.

Facts the code relies on, learned against the official images:

- NiFi 1.x runs plain HTTP (``NIFI_WEB_HTTP_PORT``); NiFi 2.x is HTTPS only,
  with single-user credentials (``SINGLE_USER_CREDENTIALS_*``).
- Both check the ``Host`` header against their own port, so the host port is
  always the same as the container port.
- ``docker cp`` writes files as root and NiFi (uid 1000) then refuses to
  start; files are copied in from a tar whose entries are owned by uid 1000.
- A flow needs ``nifi.sensitive.props.key`` set (``NIFI_SENSITIVE_PROPS_KEY``).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass

from flowport.nifi import NiFiClient, NiFiError

CONF_DIR = "/opt/nifi/nifi-current/conf"
DEFAULT_IMAGE = "apache/nifi:2.12.0"
DEFAULT_PORT = 18443
USERNAME = "flowport"
PASSWORD = "flowport-password-12"
SENSITIVE_PROPS_KEY = "flowport-validation-key"


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker(*args: str, stdin: bytes | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=False, text=stdin is None
    )
    if check and completed.returncode != 0:
        err = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode()
        raise NiFiError(f"docker {' '.join(args)} failed: {err.strip()}")
    out = completed.stdout
    return out if isinstance(out, str) else out.decode()


def owned_tar(files: dict[str, bytes], directories: tuple[str, ...] = ()) -> bytes:
    """A tar of files and directories owned by uid/gid 1000, the user NiFi runs as."""
    buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for directory in directories:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.uid = info.gid = 1000
            info.mode = 0o755
            info.mtime = now
            tar.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.uid = info.gid = 1000
            info.mode = 0o644
            info.mtime = now
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass
class Container:
    name: str
    port: int
    https: bool
    sensitive_props_key: str = SENSITIVE_PROPS_KEY

    @property
    def url(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://localhost:{self.port}/nifi-api"

    def client(self) -> NiFiClient:
        if self.https:
            return NiFiClient(self.url, username=USERNAME, password=PASSWORD, verify_tls=False)
        return NiFiClient(self.url)

    def exited(self) -> None:
        """Raise when the container stopped, with the tail of its log."""
        status = docker("ps", "-a", "--filter", f"name={self.name}", "--format", "{{.Status}}")
        if "Exited" in status:
            raise NiFiError(f"{self.name} exited:\n{docker('logs', '--tail', '50', self.name)}")

    def wait_ready(self, timeout: float = 600) -> NiFiClient:
        client = self.client()
        client.wait_ready(timeout, probe=self.exited)
        return client

    def logs(self, tail: int = 200) -> str:
        return docker("logs", "--tail", str(tail), self.name, check=False)

    def remove(self) -> None:
        docker("rm", "-f", self.name, check=False)


def start_container(
    name: str,
    image: str = DEFAULT_IMAGE,
    port: int = DEFAULT_PORT,
    *,
    https: bool = True,
    flow_gz: bytes | None = None,
    directories: tuple[str, ...] = (),
    sensitive_props_key: str = SENSITIVE_PROPS_KEY,
) -> Container:
    """Create and start a NiFi container, optionally with a ``flow.json.gz`` in ``conf/``."""
    if not docker_available():
        raise NiFiError("docker is not installed or not on PATH")
    docker("rm", "-f", name, check=False)
    env = ["-e", f"NIFI_SENSITIVE_PROPS_KEY={sensitive_props_key}"]
    if https:
        env += [
            "-e",
            f"NIFI_WEB_HTTPS_PORT={port}",
            "-e",
            "NIFI_WEB_HTTPS_HOST=0.0.0.0",
            "-e",
            f"SINGLE_USER_CREDENTIALS_USERNAME={USERNAME}",
            "-e",
            f"SINGLE_USER_CREDENTIALS_PASSWORD={PASSWORD}",
        ]
    else:
        env += ["-e", f"NIFI_WEB_HTTP_PORT={port}", "-e", "NIFI_WEB_HTTP_HOST=0.0.0.0"]
    docker("create", "--name", name, "-p", f"{port}:{port}", *env, image)
    if flow_gz is not None:
        docker("cp", "-", f"{name}:{CONF_DIR}", stdin=owned_tar({"flow.json.gz": flow_gz}))
    if directories:
        docker("cp", "-", f"{name}:/", stdin=owned_tar({}, directories))
    docker("start", name)
    return Container(name=name, port=port, https=https, sensitive_props_key=sensitive_props_key)
