"""A noisy extension cannot block the worker handshake or its RPC pipe."""
import json
import os
import time

import pytest

from app.extensions_platform.discovery import compute_fingerprint
from app.extensions_platform.worker.client import WorkerProcess
from app.extensions_platform.worker.stderr_capture import StderrCapture


def _worker(tmp_path):
    manifest = {"id": "acme.noisy", "name": "noisy", "namespace": "acme", "version": "1.0.0",
                "api_version": "1.1.0", "entry_point": "main", "description": "stderr fixture",
                "execution": {"mode": "worker"},
                "tools": [{"name": "flood", "description": "flood"}, {"name": "hang", "description": "hang"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "main.py").write_text('''
import os
from app.extensions_platform.sdk import ToolExtensionSpec

def flood():
    for _ in range(192):
        os.write(2, b"x" * 16384)
    os.write(2, b"TAIL-DIAGNOSTIC\\n")
    return {"bytes": 192 * 16384}

def hang():
    while True:
        os.write(2, b"z" * 16384)

def activate(ctx):
    flood()
    for name, func in [("flood", flood), ("hang", hang)]:
        ctx.register_tool(ToolExtensionSpec(name=name, description=name, func=func,
            side_effect="pure", deterministic=True, parameters={"type": "object", "properties": {}}))
''')
    fingerprint, diagnostic = compute_fingerprint(tmp_path)
    assert diagnostic is None and fingerprint
    return WorkerProcess(pack_dir=tmp_path, extension_id="acme.noisy", namespace="acme", name="noisy",
                         fingerprint=fingerprint, grants=[], settings={}, startup_timeout_s=5, call_timeout_s=3)


def test_megabytes_of_stderr_do_not_stall_handshake_or_call_and_tail_is_bounded(tmp_path):
    worker = _worker(tmp_path)
    try:
        worker.start()
        capture = worker._stderr_capture
        assert capture is not None
        assert worker.call("flood", {}) == {"bytes": 192 * 16384}
        deadline = time.monotonic() + 1
        while "TAIL-DIAGNOSTIC" not in capture.snapshot() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert capture.snapshot().endswith("TAIL-DIAGNOSTIC")
        assert len(capture.snapshot(1_000_000).encode()) <= 65536
        worker.shutdown()
        assert not capture.alive
    finally:
        worker.kill()


def test_timeout_kill_joins_stderr_drainer_and_restart_gets_new_capture(tmp_path):
    worker = _worker(tmp_path)
    try:
        worker.start()
        capture = worker._stderr_capture
        proc = worker._proc
        with pytest.raises(Exception, match="worker_call_timeout"):
            worker.call("hang", {}, timeout=0.1)
        worker.kill()
        assert proc is not None and proc.poll() is not None
        assert capture is not None and not capture.alive
        worker.start()
        assert worker._stderr_capture is not capture
        assert worker.call("flood", {})["bytes"] > 1_000_000
    finally:
        final_capture = worker._stderr_capture
        worker.kill()
        assert final_capture is None or not final_capture.alive


def test_drainer_finish_is_bounded_even_when_pipe_writer_stays_open():
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb") as stream:
        capture = StderrCapture(stream)
        try:
            started = time.monotonic()
            capture.finish()
            assert time.monotonic() - started < 1
            assert not capture.alive
        finally:
            os.close(writer)
