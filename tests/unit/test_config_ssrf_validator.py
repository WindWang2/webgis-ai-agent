"""SSRF 启动校验器回归测试（full-master-audit-2026-09 #1204 / #1214）。

覆盖三层修复：
1. fake-IP DNS 豁免清单含 Settings 默认与 conftest 基线所用的 OSM 域（#1204）；
2. 私有/保留 IP 字面量第一层防线真正抛错（#1214 哨兵修复 + not-is_global 收紧，
   覆盖 100.64/10 CGNAT 与 IPv6 ULA 等词表外段）；
3. fcntl 裸 import 守卫（Windows import 链，#1221 D-7）。
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from app.core.config import Settings


class TestPrivateIPLiterals:
    """第一层防线：字面量 IP 直接拒绝（不依赖 DNS）。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.5/x",
            "http://192.168.1.1/x",
            "http://172.16.0.1/x",
            "http://127.0.0.1/x",
            "http://169.254.169.254/x",
            # #1214：is_private 词表外的保留段（CGNAT / benchmark / IPv6 ULA）
            "http://100.64.1.1/x",
            "http://198.18.0.5/x",
            "http://[fd00::1]/x",
        ],
    )
    def test_private_or_reserved_literal_rejected(self, url: str) -> None:
        with pytest.raises(ValueError):
            Settings._validate_no_ssrf(url, field="TEST")

    def test_public_literal_allowed(self) -> None:
        Settings._validate_no_ssrf("http://8.8.8.8/x", field="TEST")

    def test_localhost_blocked(self) -> None:
        with pytest.raises(ValueError):
            Settings._validate_no_ssrf("http://localhost/x", field="TEST")

    def test_metadata_hostname_blocked(self) -> None:
        with pytest.raises(ValueError):
            Settings._validate_no_ssrf(
                "http://metadata.google.internal./x", field="TEST"
            )

    def test_disallowed_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="disallowed scheme"):
            Settings._validate_no_ssrf("file:///etc/passwd", field="TEST")


class TestFakeIpDnsAllowlist:
    """fake-IP DNS 环境（#1204）：豁免清单内域名解析到保留段不阻断启动。"""

    @pytest.mark.parametrize(
        "url",
        [
            "https://overpass-api.de/api/interpreter",       # conftest 基线
            "https://overpass.openstreetmap.fr/api/interpreter",  # Settings 默认
            "https://nominatim.openstreetmap.org/search",
        ],
    )
    def test_allowlisted_hostname_passes_on_reserved_resolution(
        self, url: str
    ) -> None:
        # 不 mock 真实 DNS：豁免逻辑只在「解析到非 global」时才介入，
        # 公网解析下同样必须放行 —— 两类环境都合法。
        Settings._validate_no_ssrf(url, field="TEST")

    def test_settings_constructs_when_dns_maps_to_reserved(self) -> None:
        """所有域名解析进 198.18.0.0/15（Clash/VpnKit fake-IP）时 Settings 仍可构造。"""

        class _FakeAddrInfo(list):
            pass

        def fake_getaddrinfo(host: str, port: None) -> list:
            info = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.84", 0))
            return [info]

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            s = Settings()
        assert s.OVERPASS_API_URL  # 构造成功即回归通过

    def test_unlisted_hostname_blocked_on_reserved_resolution(self) -> None:
        def fake_getaddrinfo(host: str, port: None) -> list:
            info = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.99", 0))
            return [info]

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with pytest.raises(ValueError, match="Blocked \\(SSRF\\)"):
                Settings._validate_no_ssrf(
                    "https://evil-not-allowlisted.example.com/x", field="TEST"
                )


class TestFcntlGuardedImports:
    """#1221 D-7：fcntl 守卫后的模块在任意平台可 import（Windows 回归）。"""

    def test_rag_faiss_store_imports_with_guard(self) -> None:
        import app.services.rag.faiss_store as fs

        assert hasattr(fs, "_HAS_FCNTL")
        assert hasattr(fs, "_write_lock")  # 调用面不因平台变化

    def test_bridge_secret_imports_with_guard(self) -> None:
        import app.core.bridge_secret as bs

        assert hasattr(bs, "_HAS_FCNTL")
        assert callable(bs.get_bridge_secret)
