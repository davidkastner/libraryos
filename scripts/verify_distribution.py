"""Verify that LibraryOS release artifacts contain their promised interfaces."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

REQUIRED_SOURCE_SUFFIXES = {
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "docs/agent-operations.md",
    "docs/backup-and-recovery.md",
    "docs/product-specification.md",
    "docs/release-policy.md",
    "docs/threat-model.md",
    "skills/libraryos/SKILL.md",
}
REQUIRED_WHEEL_SUFFIXES = {
    "libraryos/__init__.py",
    "libraryos/operations.py",
    "libraryos/schemas/work-v1.json",
}


def _assert_suffixes(names: set[str], required: set[str], artifact: Path) -> None:
    missing = sorted(
        suffix
        for suffix in required
        if not any(name == suffix or name.endswith(f"/{suffix}") for name in names)
    )
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"{artifact.name} is missing required content: {joined}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", nargs="?", default="dist", type=Path)
    arguments = parser.parse_args()

    sdists = sorted(arguments.dist.glob("libraryos-*.tar.gz"))
    wheels = sorted(arguments.dist.glob("libraryos-*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise SystemExit("Expected exactly one LibraryOS sdist and one wheel")

    with tarfile.open(sdists[0], "r:gz") as archive:
        _assert_suffixes(set(archive.getnames()), REQUIRED_SOURCE_SUFFIXES, sdists[0])
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        _assert_suffixes(names, REQUIRED_WHEEL_SUFFIXES, wheels[0])
        schema_count = sum(
            name.startswith("libraryos/schemas/") and name.endswith(".json")
            for name in names
        )
        if schema_count != 14:
            raise SystemExit(f"{wheels[0].name} contains {schema_count} schemas, expected 14")

    print(
        f"verified {sdists[0].name} (docs and skill) and "
        f"{wheels[0].name} (package and 14 schemas)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
