"""Capability Broker 测试（ADR-0105 Wave 4）。

矩阵：默认 deny / 授权放行 / allowlist / SSRF 权威 / artifact 根约束 /
secret 供给即授权 / 审计脱敏 / worker 端到端（真实子进程）。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.broker import (
    BrokerAuditLog,
    BrokerLimits,
    CapabilityBroker,
)
from app.extensions_platform.diagnostics import DiagnosticCode
from app.extensions_platform.permissions import Permission, PermissionGrantSet


def _broker(**overrides) -> CapabilityBroker:
    kwargs = dict(
        extension_id="acme.pack",
        grants=PermissionGrantSet(extension_id="acme.pack", granted=frozenset()),
    )
    kwargs.update(overrides)
    return CapabilityBroker(**kwargs)


def _denied(result):
    ok, value = result
    assert ok is False
    assert isinstance(value, dict) and "code" in value
    return value


class TestDefaultDeny:
    def test_no_grants_denies_network(self):
        value = _denied(_broker().handle("network_request", {"url": "https://example.com"}))
        assert value["code"] == DiagnosticCode.PERMISSION_NOT_GRANTED.value

    def test_no_grants_denies_artifact_read(self):
        value = _denied(_broker().handle("artifact_read", {"path": "a.txt"}))
        assert value["code"] == DiagnosticCode.PERMISSION_NOT_GRANTED.value

    def test_no_grants_denies_artifact_write(self):
        value = _denied(_broker().handle("artifact_write", {"path": "a.txt", "content_b64": ""}))
        assert value["code"] == DiagnosticCode.PERMISSION_NOT_GRANTED.value

    def test_unknown_op_denied(self):
        value = _denied(_broker().handle("spawn_missiles", {}))
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value

    def test_non_object_payload_denied(self):
        broker = _broker(grants=PermissionGrantSet("acme.pack", frozenset({"network"})))
        ok, value = broker.handle("network_request", "https://example.com")
        assert ok is False

    def test_unprovisioned_secret_denied(self):
        value = _denied(_broker().handle("secret_get", {"ref": "api_key"}))
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value


class TestNetworkRequest:
    def _granted(self, **kw):
        return _broker(
            grants=PermissionGrantSet("acme.pack", frozenset({Permission.NETWORK})),
            **kw,
        )

    def test_allowlist_miss_denies(self):
        value = _denied(
            self._granted(network_allow=frozenset({"api.example.com"})).handle(
                "network_request", {"url": "https://other.example.com/x"}
            )
        )
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value
        assert "EXTENSION_NETWORK_ALLOW" in value["message"]

    def test_allowlist_hit_with_fake_transport(self):
        class FakeResponse:
            status_code = 200
            content = b"hello"
            headers = {"content-type": "text/plain"}

        class FakeTransport:
            def request(self, method, url, headers=None, content=None, timeout=None):
                assert method == "GET"
                assert url == "https://api.example.com/x"
                return FakeResponse()

        ok, value = self._granted(
            network_allow=frozenset({"api.example.com"}),
            http_transport=FakeTransport(),
        ).handle("network_request", {"url": "https://api.example.com/x"})
        assert ok is True
        import base64

        assert base64.b64decode(value["body_b64"]) == b"hello"
        assert value["status"] == 200

    def test_wildcard_allowlist_still_blocks_ssrf(self):
        value = _denied(
            self._granted(network_allow=frozenset({"*"})).handle(
                "network_request", {"url": "http://127.0.0.1:8080/admin"}
            )
        )
        assert "SSRF" in value["message"]

    def test_private_ip_denied_even_if_allowlisted(self):
        value = _denied(
            self._granted(network_allow=frozenset({"169.254.169.254"})).handle(
                "network_request", {"url": "http://169.254.169.254/latest/meta-data"}
            )
        )
        assert "SSRF" in value["message"]

    def test_disallowed_method_denied(self):
        value = _denied(
            self._granted(network_allow=frozenset({"*"})).handle(
                "network_request", {"url": "https://api.example.com", "method": "DELETE"}
            )
        )
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value

    def test_denied_headers_stripped(self):
        captured = {}

        class FakeTransport:
            def request(self, method, url, headers=None, content=None, timeout=None):
                captured["headers"] = headers
                resp = type("R", (), {"status_code": 204, "content": b"", "headers": {}})()
                return resp

        self._granted(
            network_allow=frozenset({"api.example.com"}),
            http_transport=FakeTransport(),
        ).handle(
            "network_request",
            {
                "url": "https://api.example.com",
                "headers": {"X-Ok": "1", "Host": "evil", "Content-Length": "3"},
            },
        )
        assert "host" not in captured["headers"]
        assert "content-length" not in captured["headers"]
        assert captured["headers"]["X-Ok"] == "1"


class TestArtifacts:
    def _granted(self, tmp_path: Path, **kw):
        return _broker(
            grants=PermissionGrantSet(
                "acme.pack",
                frozenset({Permission.PROJECT_ARTIFACT_READ, Permission.PROJECT_ARTIFACT_WRITE}),
            ),
            artifact_roots=(tmp_path / "artifacts",),
            **kw,
        )

    def test_write_read_roundtrip(self, tmp_path):
        broker = self._granted(tmp_path)
        ok, written = broker.handle(
            "artifact_write", {"path": "runs/one.bin", "content_b64": "AAEC"}
        )
        assert ok and written["bytes_written"] == 3
        ok, read = broker.handle("artifact_read", {"path": "runs/one.bin"})
        assert ok
        import base64

        assert base64.b64decode(read["content_b64"]) == b"\x00\x01\x02"

    def test_traversal_denied(self, tmp_path):
        broker = self._granted(tmp_path)
        value = _denied(broker.handle("artifact_read", {"path": "../../etc/passwd"}))
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value
        assert "artifact root" in value["message"]

    def test_absolute_outside_root_denied(self, tmp_path):
        broker = self._granted(tmp_path)
        value = _denied(broker.handle("artifact_read", {"path": "/etc/passwd"}))
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value

    def test_no_roots_denies_everything(self):
        broker = _broker(
            grants=PermissionGrantSet(
                "acme.pack", frozenset({Permission.PROJECT_ARTIFACT_READ})
            ),
        )
        value = _denied(broker.handle("artifact_read", {"path": "a.txt"}))
        assert "artifact root" in value["message"]


class TestSecrets:
    def test_provisioned_secret_roundtrip(self):
        broker = _broker(secrets={"api_key": "sup3r-secret"})
        ok, value = broker.handle("secret_get", {"ref": "api_key"})
        assert ok and value["value"] == "sup3r-secret"

    def test_audit_never_contains_secret_value(self):
        broker = _broker(secrets={"api_key": "sup3r-secret"})
        broker.handle("secret_get", {"ref": "api_key"})
        snapshot = json.dumps(broker._audit.snapshot())
        assert "sup3r-secret" not in snapshot


class TestAudit:
    def test_ring_is_bounded(self):
        audit = BrokerAuditLog(max_entries=4)
        broker = CapabilityBroker(
            extension_id="acme.pack",
            grants=PermissionGrantSet("acme.pack", frozenset()),
            audit=audit,
        )
        for _ in range(10):
            broker.handle("secret_get", {"ref": "x"})
        assert len(audit.snapshot()) == 4

    def test_denials_recorded_with_op(self):
        audit = BrokerAuditLog()
        broker = CapabilityBroker(
            extension_id="acme.pack",
            grants=PermissionGrantSet("acme.pack", frozenset()),
            audit=audit,
        )
        broker.handle("network_request", {"url": "https://example.com"})
        entries = audit.snapshot()
        assert entries[0]["op"] == "network_request"
        assert entries[0]["ok"] is False


class TestBrokerLimits:
    def test_response_truncation_flag(self):
        class FakeResponse:
            status_code = 200
            content = b"x" * 64
            headers = {}

        class FakeTransport:
            def request(self, method, url, headers=None, content=None, timeout=None):
                return FakeResponse()

        broker = _broker(
            grants=PermissionGrantSet("acme.pack", frozenset({Permission.NETWORK})),
            network_allow=frozenset({"api.example.com"}),
            limits=BrokerLimits(max_http_response_bytes=16),
            http_transport=FakeTransport(),
        )
        ok, value = broker.handle("network_request", {"url": "https://api.example.com"})
        assert ok and value["truncated"] is True


# ---------------------------------------------------------------------------
# worker 端到端（真实子进程；经 host 的 broker_handler 往返）


BROKER_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _write_note(ctx):
        def _run(text: str) -> dict:
            n = ctx.broker.write_artifact("notes/run.txt", text.encode())
            data = ctx.broker.read_artifact("notes/run.txt")
            return {"written": n, "read_back": data.decode()}
        return _run


    def _peek_secret(ctx):
        def _run() -> dict:
            return {"value": ctx.broker.get_secret("demo_key")}
        return _run


    def _deny_probe(ctx):
        def _run() -> dict:
            try:
                ctx.broker.get_secret("not_provisioned")
            except Exception as exc:
                return {"denied": True, "code": getattr(getattr(exc, 'diagnostic', None), 'code', None).value
                        if hasattr(exc, 'diagnostic') else 'unknown'}
            return {"denied": False}
        return _run


    def activate(ctx):
        ctx.register_tool(ToolExtensionSpec(
            name="note", description="write+read artifact via broker",
            func=_write_note(ctx), side_effect="artifact_creation",
            deterministic=True,
            parameters={"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
        ))
        ctx.register_tool(ToolExtensionSpec(
            name="peek", description="provisioned secret", func=_peek_secret(ctx),
            side_effect="pure", deterministic=True,
            parameters={"type": "object", "properties": {}},
        ))
        ctx.register_tool(ToolExtensionSpec(
            name="deny_probe", description="unprovisioned secret", func=_deny_probe(ctx),
            side_effect="pure", deterministic=True,
            parameters={"type": "object", "properties": {}},
        ))
    """
)


class TestWorkerBrokerEndToEnd:
    @pytest.fixture()
    def worker_env(self, tmp_path):
        from app.extensions_platform.host import ExtensionHost, HostPolicy
        from app.tools.registry import ToolRegistry

        pack = tmp_path / "acme.pack.dir"
        pack.mkdir()
        manifest = {
            "schema_version": 1,
            "id": "acme.pack",
            "name": "pack",
            "namespace": "acme",
            "version": "1.0.0",
            "api_version": "1.1.0",
            "entry_point": "main",
            "description": "broker e2e",
            "execution": {"mode": "worker", "call_timeout_s": 10},
            "permissions": ["project_artifact_read", "project_artifact_write"],
            "tools": [
                {"name": "note", "description": "note"},
                {"name": "peek", "description": "peek"},
                {"name": "deny_probe", "description": "deny"},
            ],
        }
        (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (pack / "main.py").write_text(BROKER_MAIN, encoding="utf-8")
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        host = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(
                roots=(tmp_path,),
                grants={
                    "acme.pack": frozenset(
                        {"project_artifact_read", "project_artifact_write"}
                    )
                },
                secrets={"acme.pack": {"demo_key": "s3cret-value"}},
                artifact_roots=(artifacts,),
            ),
        )
        host.discover()
        diags = host.activate("acme.pack")
        assert not any(d.severity.value == "error" for d in diags), [
            d.message for d in diags
        ]
        return host, host.get_record("acme.pack"), artifacts

    def test_artifact_roundtrip_over_rpc(self, worker_env):
        host, record, artifacts = worker_env
        result = host._tool_registry._tools["acme_note"](text="hello broker")
        assert result == {"written": 12, "read_back": "hello broker"}
        assert (artifacts / "notes" / "run.txt").read_text() == "hello broker"
        host.reset()

    def test_secret_delivered_via_broker_only(self, worker_env):
        host, record, artifacts = worker_env
        assert host._tool_registry._tools["acme_peek"]() == {"value": "s3cret-value"}
        host.reset()

    def test_unprovisioned_secret_denied_end_to_end(self, worker_env):
        host, record, artifacts = worker_env
        result = host._tool_registry._tools["acme_deny_probe"]()
        assert result["denied"] is True
        assert result["code"] == "broker_denied"
        host.reset()

    def test_broker_audit_visible_on_host(self, worker_env):
        host, record, artifacts = worker_env
        host._tool_registry._tools["acme_peek"]()
        entries = host.broker_audit("acme.pack")
        assert entries and entries[-1]["op"] == "secret_get" and entries[-1]["ok"] is True
        assert "s3cret-value" not in json.dumps(entries)
        host.reset()
