"""Side provisioning.

A `SideProvisioner` prepares one participant's environment: a workspace
containing the treasure, plus whatever isolation the backend provides.

- `LocalSideProvisioner`: development/test backend. Creates per-side
  directories with owner-only permissions. NOT strong isolation.
- `DockerSideProvisioner`: reference backend for real matches. Runs each side
  in its own container with its own network namespace and identical CPU/memory
  limits. Shells out to the `docker` CLI (requires a Docker daemon).

The `SideHandle` is the uniform object the orchestrator uses regardless of
backend.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..core.types import Side


@dataclass
class SideHandle:
    """A provisioned side."""

    side: Side
    workspace: Path
    treasure_path: Path
    backend: str = "local"
    # Opaque backend-specific reference (e.g. container id) for teardown.
    reference: str | None = None
    # Where the opponent can reach this side (human-readable hint for prompts).
    exposure_hint: str = ""


class LocalSideProvisioner:
    """Dev/test backend: per-side directories under a root. Not real isolation."""

    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def provision(self, side: Side, treasure: str, treasure_name: str = "treasure.txt") -> SideHandle:
        workspace = (self.root / side.value / "workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        treasure_path = workspace / treasure_name
        treasure_path.write_text(treasure, encoding="utf-8")
        try:
            # Best-effort privacy on POSIX systems.
            workspace.chmod(0o700)
            treasure_path.chmod(0o600)
        except OSError:
            pass
        return SideHandle(
            side=side,
            workspace=workspace,
            treasure_path=treasure_path,
            backend=self.backend,
            exposure_hint=f"exposed directory at {workspace}",
        )

    def teardown(self, handle: SideHandle) -> None:
        # Leave artifacts in place for inspection by default; callers may wipe.
        pass

    def wipe(self, handle: SideHandle) -> None:
        shutil.rmtree(handle.workspace.parent, ignore_errors=True)


class DockerSideProvisioner:
    """Reference backend for real matches (requires a Docker daemon).

    Each side runs in its own container with its own network namespace and
    identical CPU/memory limits. Only ports the defender exposes are reachable
    by the opponent. This class shells out to the `docker` CLI.
    """

    backend = "docker"

    def __init__(
        self,
        image: str = "agentarena-side:latest",
        network: str = "agentarena",
        cpu: str = "1.0",
        memory: str = "1g",
    ) -> None:
        self.image = image
        self.network = network
        self.cpu = cpu
        self.memory = memory

    def _run(self, *args: str, input_text: str | None = None) -> str:
        proc = subprocess.run(
            ["docker", *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def provision(self, side: Side, treasure: str, treasure_name: str = "treasure.txt") -> SideHandle:
        name = f"agentarena-{side.value}"
        container_id = self._run(
            "run", "-d", "--name", name,
            "--network", self.network,
            "--cpus", self.cpu, "--memory", self.memory,
            self.image, "sleep", "infinity",
        )
        # Write the treasure inside the container's workspace (via stdin, not argv).
        workspace = Path("/side/workspace")
        self._run("exec", name, "mkdir", "-p", str(workspace))
        self._run(
            "exec", "-i", name, "sh", "-c", f"cat > {workspace / treasure_name}",
            input_text=treasure,
        )
        return SideHandle(
            side=side,
            workspace=workspace,
            treasure_path=workspace / treasure_name,
            backend=self.backend,
            reference=container_id,
            exposure_hint=f"container {name} on docker network {self.network}",
        )

    def teardown(self, handle: SideHandle) -> None:
        if handle.reference:
            subprocess.run(
                ["docker", "rm", "-f", handle.reference],
                capture_output=True, check=False,
            )
