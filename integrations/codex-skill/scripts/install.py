#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install Agent Memory Ledger and its Codex bridge into a repository."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="wheel, source checkout, sdist, or package spec accepted by pip",
    )
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--ledger", default=".portable-memory")
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    venv_dir = workspace / ".agent-memory-ledger-venv"
    adapter_dir = workspace / ".agent-memory-ledger-adapter"
    bridge_source = Path(__file__).resolve().with_name("codex_bridge.py")
    bridge_dest = adapter_dir / "codex_bridge.py"
    ledger_dir = (workspace / args.ledger).resolve()

    try:
        if not bridge_source.is_file():
            raise FileNotFoundError(f"Codex bridge missing beside installer: {bridge_source}")
        if args.recreate and venv_dir.exists():
            shutil.rmtree(venv_dir)
        if not venv_dir.exists():
            run([sys.executable, "-m", "venv", str(venv_dir)], workspace)

        bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
        python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
        aml = bin_dir / ("aml.exe" if sys.platform == "win32" else "aml")
        run(
            [str(python), "-m", "pip", "install", "--no-deps", args.source],
            workspace,
        )

        adapter_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bridge_source, bridge_dest)
        bridge_dest.chmod(0o755)

        initialized = json.loads(
            run([str(aml), "--workspace", str(ledger_dir), "init"], workspace)
        )
        version = run(
            [
                str(python),
                "-c",
                "import agent_memory_ledger as m; print(m.__version__)",
            ],
            workspace,
        ).strip()
        print(
            json.dumps(
                {
                    "status": "installed",
                    "version": version,
                    "workspace": str(workspace),
                    "venv": str(venv_dir),
                    "adapter": str(bridge_dest),
                    "ledger": str(ledger_dir),
                    "ledger_status": initialized.get("status"),
                    "platform_memory_modified": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
