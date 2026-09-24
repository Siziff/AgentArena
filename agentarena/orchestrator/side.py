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


def treasure_file_names(count: int, base: str = "treasure.txt") -> list[str]:
    """File names for `count` treasures: treasure.txt, treasure_2.txt, ...

    The first treasure keeps the canonical base name so single-treasure
    matches behave exactly as before.
    """
    names = [base]
    stem, dot, suffix = base.partition(".")
    for i in range(2, max(1, count) + 1):
        names.append(f"{stem}_{i}{dot}{suffix}" if dot else f"{base}_{i}")
    return names


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
    # All treasure files (treasure_path is always the first one).
    treasure_paths: list[Path] = field(default_factory=list)


class LocalSideProvisioner:
    """Dev/test backend: per-side directories under a root. Not real isolation."""

    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def provision(
        self,
        side: Side,
        treasures: str | list[str],
        treasure_name: str = "treasure.txt",
    ) -> SideHandle:
        if isinstance(treasures, str):
            treasures = [treasures]
        workspace = (self.root / side.value / "workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        names = treasure_file_names(len(treasures), treasure_name)
        treasure_paths = []
        for name, treasure in zip(names, treasures):
            path = workspace / name
            path.write_text(treasure, encoding="utf-8")
            treasure_paths.append(path)
        try:
            # Best-effort privacy on POSIX systems.
            workspace.chmod(0o700)
            for path in treasure_paths:
                path.chmod(0o600)
        except OSError:
            pass
        return SideHandle(
            side=side,
            workspace=workspace,
            treasure_path=treasure_paths[0],
            backend=self.backend,
            exposure_hint=f"exposed directory at {workspace}",
            treasure_paths=treasure_paths,
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

    def provision(
        self,
        side: Side,
        treasures: str | list[str],
        treasure_name: str = "treasure.txt",
    ) -> SideHandle:
        if isinstance(treasures, str):
            treasures = [treasures]
        name = f"agentarena-{side.value}"
        container_id = self._run(
            "run", "-d", "--name", name,
            "--network", self.network,
            "--cpus", self.cpu, "--memory", self.memory,
            self.image, "sleep", "infinity",
        )
        # Write the treasures inside the container's workspace (via stdin, not argv).
        workspace = Path("/side/workspace")
        self._run("exec", name, "mkdir", "-p", str(workspace))
        names = treasure_file_names(len(treasures), treasure_name)
        treasure_paths = []
        for file_name, treasure in zip(names, treasures):
            self._run(
                "exec", "-i", name, "sh", "-c", f"cat > {workspace / file_name}",
                input_text=treasure,
            )
            treasure_paths.append(workspace / file_name)
        return SideHandle(
            side=side,
            workspace=workspace,
            treasure_path=treasure_paths[0],
            backend=self.backend,
            reference=container_id,
            exposure_hint=f"container {name} on docker network {self.network}",
            treasure_paths=treasure_paths,
        )

    def teardown(self, handle: SideHandle) -> None:
        if handle.reference:
            subprocess.run(
                ["docker", "rm", "-f", handle.reference],
                capture_output=True, check=False,
            )
