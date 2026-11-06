"""#1346（audit SEC 系列）回归测试。

- ISSUE-002：s3/minio 无点裸主机名不再绕过私网门控；
- ISSUE-001/003：send 时 DNS pinning——解析与 connect 之间不再有
  rebinding 窗口；发送时解析到私网即拒绝；
- ISSUE-004：map/upload 写路径依赖 get_current_user_with_version；
- ISSUE-006：owner_token 轮换端点存在且对认证会话 409。
"""
import socket

import pytest

from app.services.data_fabric.security import (
    DataFabricSecurity,
    DataFabricSecurityError,
    _safe_pool_classes,
)


class TestS3BareHostGate:
    """ISSUE-002：s3://minio-server/bucket 这类无点主机名此前整体绕过。"""

    def test_bare_host_resolving_to_private_blocked(self, monkeypatch):
        monkeypatch.setattr(
            DataFabricSecurity, "_resolve_all",
            staticmethod(lambda h: ["10.0.0.9"]),
        )
        with pytest.raises(DataFabricSecurityError):
            DataFabricSecurity.validate_url("s3://minio-server/bucket")

    def test_bucket_name_unresolvable_still_allowed(self, monkeypatch):
        monkeypatch.setattr(
            DataFabricSecurity, "_resolve_all",
            staticmethod(lambda h: []),
        )
        url = "s3://my-bucket-name"
        assert DataFabricSecurity.validate_url(url) == url

    def test_bare_host_resolving_public_allowed(self, monkeypatch):
        monkeypatch.setattr(
            DataFabricSecurity, "_resolve_all",
            staticmethod(lambda h: ["8.8.8.8"]),
        )
        url = "s3://endpoint-host/bucket"
        assert DataFabricSecurity.validate_url(url) == url


class TestSendTimeDnsPinning:
    """Connection-local validated addresses preserve global DNS and TLS hostname."""

    def test_pinning_preserves_global_resolver(self, monkeypatch):
        import urllib3.connection

        def resolve(host, port, *args):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        seen = []
        sock = object()
        monkeypatch.setattr(urllib3.connection.connection, "create_connection",
                            lambda address, *args, **kwargs: seen.append(address) or sock)
        conn = _safe_pool_classes()["http"].ConnectionCls("example.test", port=8080)
        assert conn._new_conn() is sock
        assert seen == [("8.8.8.8", 8080)]
        assert socket.getaddrinfo is resolve
        assert conn._dns_host == "example.test"

    def test_connect_blocks_when_resolution_flips_private(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port)),
        ])
        conn = _safe_pool_classes()["http"].ConnectionCls("flipping.example", port=80)
        with pytest.raises(DataFabricSecurityError):
            conn._new_conn()


class TestWritePathAuth:
    """ISSUE-004：导出/上传写路径必须走 DB 校验的依赖。"""

    def test_map_routes_use_versioned_auth(self):
        import inspect

        import app.api.routes.map as map_routes

        src = inspect.getsource(map_routes)
        assert "Depends(get_current_user)" not in src
        # map 导出所有权是用户级设计（owner == user_id），保持严格 Bearer；
        # 匿名放行只给 upload 数据面（SEC-08 owner_token 管道）。
        assert "get_current_user_with_version" in src

    def test_upload_routes_use_versioned_auth(self):
        import inspect

        import app.api.routes.upload as upload_routes

        src = inspect.getsource(upload_routes)
        assert "Depends(get_current_user)" not in src
        assert "get_current_user_optional_with_version" in src


class TestOwnerTokenRotation:
    """ISSUE-006：轮换端点已注册。"""

    def test_rotate_route_registered(self):
        from app.api.routes.chat import router

        paths = {
            getattr(r, "path", "") for r in router.routes
        }
        assert "/chat/sessions/{session_id}/rotate-owner-token" in paths

    def test_rotate_route_declares_response_model(self):
        """契约门禁回归：轮换端点必须声明 response_model。

        初版漏声明导致 response_model 覆盖门 / 字段契约 / OpenAPI 快照 /
        api-docs drift 四条契约线同时红（contract-gate 现已进 release DAG）。
        """
        from app.api.routes.chat import router
        from app.schemas.chat_schema import RotateOwnerTokenResponse

        route = next(
            r
            for r in router.routes
            if getattr(r, "path", "")
            == "/chat/sessions/{session_id}/rotate-owner-token"
        )
        assert getattr(route, "response_model", None) is RotateOwnerTokenResponse
        assert "owner_token" in RotateOwnerTokenResponse.model_fields
