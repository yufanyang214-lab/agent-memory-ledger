"""Build version-matched wheel/sdist artifacts using the project's build backend."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    core = (root / "src/agent_memory_ledger/__init__.py").read_text(encoding="utf-8")
    if re.search(r'^__version__ = "([^"]+)"$', core, re.M).group(1) != version:
        raise ValueError("core and package versions differ")
    for relative in ("integrations/codex-plugin/.codex-plugin/plugin.json", "integrations/kimi-plugin/plugin.json"):
        if json.loads((root / relative).read_text(encoding="utf-8"))["version"] != version:
            raise ValueError(f"plugin version differs: {relative}")
    if project.get("dependencies"):
        raise ValueError("base package unexpectedly declares runtime dependencies")
    # setuptools is already the declared build dependency, not a runtime one.
    try:
        from setuptools.build_meta import build_sdist, build_wheel
    except ModuleNotFoundError as exc:
        raise RuntimeError('Install build dependencies first: python -m pip install "setuptools>=61" wheel') from exc

    destination = root / "dist" / version
    destination.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    try:
        os.chdir(root)
        names = [build_wheel(str(destination)), build_sdist(str(destination))]
    finally:
        os.chdir(previous)
    checksums = "".join(
        f"{hashlib.sha256((destination / name).read_bytes()).hexdigest()}  {name}\n"
        for name in sorted(names)
    )
    (destination / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    print(json.dumps({"version": version, "artifacts": names, "output": str(destination)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
