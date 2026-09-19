"""Exercise the installed LibraryOS wheel through its public dispatcher."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def _run(cli: Path, *arguments: str) -> dict:
    completed = subprocess.run(
        [str(cli), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--executable",
        type=Path,
        default=Path("libraryos"),
        help="Installed LibraryOS console script to verify",
    )
    arguments = parser.parse_args()
    cli = arguments.executable.absolute()

    version = subprocess.run(
        [str(cli), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    schemas = _run(cli, "schemas")["schemas"]
    operations = _run(cli, "operations")["operations"]

    with tempfile.TemporaryDirectory(prefix="libraryos-installed-verify-") as temporary:
        root = Path(temporary) / "library"
        initialized = _run(
            cli,
            "call",
            "library.initialize",
            "--arguments",
            json.dumps({"path": str(root), "title": "Installed artifact verification"}),
        )
        created = _run(
            cli,
            "call",
            "work.create",
            "--arguments",
            json.dumps(
                {
                    "library": str(root),
                    "work_type": "report",
                    "title": "Clean install",
                }
            ),
        )
        rebuilt = _run(
            cli,
            "call",
            "library.rebuild",
            "--arguments",
            json.dumps({"library": str(root)}),
        )
        searched = _run(
            cli,
            "call",
            "search",
            "--arguments",
            json.dumps({"library": str(root), "query": "Clean install"}),
        )
        validated = _run(
            cli,
            "call",
            "library.validate",
            "--arguments",
            json.dumps({"path": str(root)}),
        )

    assert version == "libraryos 0.1.0.dev0"
    assert len(schemas) == 14
    assert len(operations) == 60
    assert initialized["operation"] == "libraryos.library.initialize"
    assert created["result"]["work"]["title"] == "Clean install"
    assert rebuilt["result"]["works"] == 1
    assert len(searched["result"]) == 1
    assert searched["result"][0]["scientific_support"] == "not_assessed"
    assert validated["result"]["valid"] is True
    assert validated["result"]["verified_hashes"] is True

    print(
        json.dumps(
            {
                "version": version.rsplit(" ", 1)[1],
                "schemas": len(schemas),
                "operations": len(operations),
                "initialized_via_dispatcher": True,
                "created_works": rebuilt["result"]["works"],
                "search_results": len(searched["result"]),
                "scientific_support": searched["result"][0]["scientific_support"],
                "validation_findings": len(validated["result"]["findings"]),
                "hash_verification_enabled": validated["result"]["verified_hashes"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
