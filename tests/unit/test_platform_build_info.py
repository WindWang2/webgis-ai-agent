"""构建身份与版本端点测试（ADR-0131 D7）。

app.main 在部分环境不可 import（fcntl 平台限制），路由用函数级直测——
端点本体是纯函数，语义由 build_info 契约保证。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.build_info import (
    build_info,
    reset_build_info_cache_for_tests,
    version_string,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_build_info_cache_for_tests()
    yield
    reset_build_info_cache_for_tests()


# ── build_info ───────────────────────────────────────────────────────────


def test_version_matches_version_file():
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    expected = version_file.read_text(encoding="utf-8").strip()
    assert version_string() == expected


def test_build_info_fields_are_non_empty_and_non_sensitive():
    info = build_info()
    assert info["version"] not in ("", "unknown")  # VERSION 文件存在且可读
    assert info["python"]
    # commit：本机是 git 仓库 → 应解析出短 SHA；即便失败也必须是 unknown 占位
    assert info["commit"]
    payload = json.dumps(info)
    for secret_hint in ("key", "token", "secret", "password"):
        assert secret_hint not in payload.lower()


def test_env_override_wins_for_commit(monkeypatch):
    monkeypatch.setenv("WEBGIS_BUILD_SHA", "deadbeef")
    reset_build_info_cache_for_tests()
    assert build_info()["commit"] == "deadbeef"


def test_health_version_no_longer_hardcoded():
    """/health 的 version 必须来自 build_info（漂移修复的回归锚）。"""
    pytest.importorskip(
        "fcntl", reason="routes 包 __init__ 级联导入 fcntl（POSIX-only）"
    )
    from app.api.routes.health import health_check

    body = health_check()
    assert body["version"] == version_string()


# ── /version 端点 ────────────────────────────────────────────────────────


def test_version_endpoint_shape():
    pytest.importorskip(
        "fcntl", reason="routes 包 __init__ 级联导入 fcntl（POSIX-only）"
    )
    from app.api.routes.version import version

    body = version()
    assert body["version"] == version_string()
    assert body["extensions_api"]
    assert "timestamp" in body
    # 防侦察纪律：无环境细节、无内部路径
    blob = json.dumps(body).lower()
    for leak in ("windows", "linux", "darwin", "c:\\", "/home/", "env"):
        assert leak not in blob


# ── 生成物字节闸 ─────────────────────────────────────────────────────────


def test_config_schema_artifact_matches(settings_generator=None):
    """config schema 生成物与当前 Settings 一致（--check 语义）。"""
    import scripts.gen_config_schema as gcs

    rendered = gcs.generate_schema()
    assert gcs.OUTPUT.exists(), "config.schema.json 未提交"
    assert gcs.OUTPUT.read_text(encoding="utf-8") == rendered


def test_app_inventory_artifact_matches():
    """依赖 inventory（声明名集合）与 requirements.txt 一致。"""
    import scripts.gen_app_inventory as gai

    rendered = gai.generate_inventory()
    assert gai.OUTPUT.exists(), "app-inventory.json 未提交"
    assert gai.OUTPUT.read_text(encoding="utf-8") == rendered


def test_app_inventory_is_deterministic():
    """声明名集合跨机器一致（inventory 闸的字节稳定性前提）。"""
    import scripts.gen_app_inventory as gai

    assert gai.declared_direct_deps() == gai.declared_direct_deps()
    assert gai.generate_inventory() == gai.generate_inventory()
