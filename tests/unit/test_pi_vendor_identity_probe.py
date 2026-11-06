"""Unavailable Node identity remains unknown with bounded diagnostics."""
import logging
import subprocess

import pytest

from app.services.chat import pi_compat


@pytest.mark.parametrize("failure", [
    FileNotFoundError("private executable path " * 1000),
    subprocess.TimeoutExpired(["node", "--version"], 3, output="private output " * 1000),
])
def test_node_probe_failure_is_unknown_and_diagnostic_is_bounded(failure, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pi_compat.subprocess, "run", unavailable)
    with caplog.at_level(logging.WARNING, logger=pi_compat.__name__):
        assert pi_compat._node_version() is None
    assert len(caplog.records) == 1
    diagnostic = caplog.records[0].getMessage()
    assert type(failure).__name__ in diagnostic
    assert "private" not in diagnostic
    assert len(diagnostic) < 160


def test_node_probe_keeps_observed_version_and_bounded_subprocess(monkeypatch):
    calls = []

    def version(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "v24.21.0\n", "")

    monkeypatch.setattr(pi_compat.subprocess, "run", version)
    assert pi_compat._node_version() == "v24.21.0"
    assert calls[0][0] == ["node", "--version"]
    assert calls[0][1]["timeout"] == 3.0
