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
        description="Install Agent Memory Ledger core into an OpenClaw workspace-local venv."
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
    bridge = Path(__file__).resolve().with_name("kimi_bridge.py")
    ledger_dir = (workspace / args.ledger).resolve()

    try:
        if not bridge.is_file():
            raise FileNotFoundError(f"Kimi bridge missing beside installer: {bridge}")
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
                    "bridge": str(bridge),
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
