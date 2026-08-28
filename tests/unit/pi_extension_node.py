"""Shared runner for node-subprocess tests against the webgis-tools extension.

The extension is plain ESM JavaScript — its behavior is exercised by spawning
node with an inline module snippet that imports the shipped artifact
(index.mjs; index.ts is the documented dead copy). Each test gets a fresh
module evaluation: env reads and the turn-token memo are per-process.
"""
import json
import os
import subprocess
from pathlib import Path

EXTENSION = Path(__file__).parents[2] / "app" / "extensions" / "webgis-tools" / "index.mjs"


def run_node(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a node ESM snippet; fail with stderr context instead of a bare
    CalledProcessError so snippet bugs are debuggable from the pytest log."""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, **(env or {})},
    )
    assert result.returncode == 0, f"node snippet failed:\n{result.stderr}"
    return result


def ext_uri() -> str:
    """The extension as a JSON-quoted file URI, for embedding in snippets."""
    return json.dumps(EXTENSION.as_uri())
