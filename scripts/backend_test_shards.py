"""Exhaustive six-shard module manifests using the project's pytest discovery config.

Sorted round-robin gives deterministic file-count balance (at most one module of
skew), not a promise of equal runtime. Complete modules stay together for xdist
loadfile. Marker filtering and coverage remain pytest/CI responsibilities.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fnmatch
import os
import shlex
from pathlib import Path
from typing import Iterable

import pytest

SHARD_COUNT = 6
ROOT = Path(__file__).resolve().parents[1]


def discover_modules(root: Path) -> list[str]:
    """Use native configured filename/norecurse/ignore rules; never import tests."""
    root = root.resolve()
    # fromdictargs reads native ini defaults but deliberately does not apply
    # addopts. Do NOT apply plugin flags such as --cov here: discovery must not
    # start coverage or load product test modules before the actual pytest run.
    config = pytest.Config.fromdictargs({}, [
        "--rootdir", str(root), "-c", str(root / "pytest.ini"), "--noconftest",
    ])
    options = config.getini("addopts") + shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    ignores = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    ignores.add_argument("--ignore", action="append", default=[])
    ignores.add_argument("--ignore-glob", action="append", default=[])
    known, _ = ignores.parse_known_args(options)
    patterns = config.getini("python_files")
    norecurse = config.getini("norecursedirs")
    ignored = [(root / value).resolve() for value in known.ignore]
    ignore_globs = known.ignore_glob

    def excluded(path: Path) -> bool:
        return (any(path == other or other in path.parents for other in ignored)
                or any(fnmatch.fnmatch(str(path), str(root / pattern))
                       for pattern in ignore_globs))

    modules = []
    for directory, dirs, files in os.walk(root / "tests"):
        parent = Path(directory)
        dirs[:] = sorted(name for name in dirs
                         if not any(fnmatch.fnmatch(name, rule) for rule in norecurse)
                         and not excluded(parent / name))
        for name in sorted(files):
            path = parent / name
            if not excluded(path) and any(fnmatch.fnmatch(name, rule) for rule in patterns):
                relative = path.relative_to(root).as_posix()
                if "\n" in relative or "\r" in relative:
                    raise ValueError("Test paths cannot contain argument-file line separators")
                modules.append(relative)
    return sorted(modules)


def partition_modules(modules: Iterable[str]) -> list[list[str]]:
    ordered = sorted(modules)
    if len(set(ordered)) != len(ordered):
        raise ValueError("Duplicate test module input")
    shards = [ordered[index::SHARD_COUNT] for index in range(SHARD_COUNT)]
    assert Counter(path for shard in shards for path in shard) == Counter(ordered)
    return shards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--shard", type=int, choices=range(1, SHARD_COUNT + 1), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    modules = partition_modules(discover_modules(args.root))[args.shard - 1]
    if not modules:
        parser.error("Empty shard: refusing pytest's implicit full-suite fallback")
    manifest = "\n".join(modules) + "\n"
    if args.output:
        args.output.write_text(manifest)
    else:
        print(manifest, end="")


if __name__ == "__main__":
    main()
