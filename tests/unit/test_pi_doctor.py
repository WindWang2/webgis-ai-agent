from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import pi_doctor


def test_vendor_floor_failure_overrides_gitlink_drift(monkeypatch, capsys):
    from app.services.chat import pi_compat

    ident = SimpleNamespace(
        head_commit="drifted",
        describe="v1.4.0",
        expected_pin="recorded",
        entry_kind=None,
        node_version=None,
        matches_expected_pin=False,
        meets_floor=False,
    )
    monkeypatch.setattr(pi_compat, "pi_vendor_identity", lambda: ident)
    monkeypatch.setattr(pi_doctor, "CHECKS", [("vendor", pi_doctor.check_vendor)])
    monkeypatch.setattr(
        pi_doctor, "_runtime_mode_block", lambda: ("unavailable", "vendor_entry_unbuilt")
    )
    monkeypatch.setattr(sys, "argv", ["pi_doctor.py", "--offline", "--json"])

    assert pi_doctor.main() == 1
    payload, _ = json.JSONDecoder().raw_decode(capsys.readouterr().out)
    assert payload["domains"][0]["status"] == pi_doctor.FAIL


def test_rpc_terminates_stub_when_ready_timeout_expires(monkeypatch):
    from app.services.chat import pi_compat, pi_native_surface

    class Stub:
        terminated = False

        def terminate(self):
            self.terminated = True

    stub = Stub()
    monkeypatch.setattr(pi_doctor.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(
        pi_compat,
        "pi_vendor_identity",
        lambda: SimpleNamespace(entry_built=True, entry_path="/probe-entry"),
    )
    monkeypatch.setattr(pi_doctor, "_live_registry", lambda: object())
    monkeypatch.setattr(pi_native_surface, "pi_surface_for_spawn", lambda _registry: {})
    monkeypatch.setattr(pi_doctor.subprocess, "Popen", lambda *_args, **_kwargs: stub)
    monkeypatch.setattr(pi_doctor.time, "monotonic", iter([0.0, 10.1]).__next__)
    monkeypatch.setattr(pi_doctor.time, "sleep", lambda _seconds: None)

    with pytest.raises(AssertionError, match="doctor stub LLM failed to boot"):
        pi_doctor.check_rpc()

    assert stub.terminated
