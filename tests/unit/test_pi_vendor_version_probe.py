"""WP1 版本握手单测：runtime probe（``node rpc-entry --version``）与能力表。

覆盖：
  * probe 解析（成功 / 非版本输出 / 超时 / entry 缺失）；
  * probe 结果按 ``(entry_path, mtime_ns)`` 缓存（产物不变不重复 spawn，
    mtime 变化即失效——rebuild 的可观测签名）；
  * ``PI_VENDOR_CAPABILITIES`` 能力表（版本 → 能力位的纯函数推断）；
  * ``attach_runtime_version`` 的 opt-in 语义（identity 本体保持 stat-only）。

不依赖 vendor 构建状态、不依赖长 sleep（超时预算经 monkeypatch 收紧）。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from app.services.chat import pi_compat
from app.services.chat.pi_compat import (
    PI_VENDOR_CAPABILITIES,
    PiVendorIdentity,
    attach_runtime_version,
    parse_vendor_version,
    probe_vendor_runtime_version,
    vendor_capabilities,
)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """模块级 mtime 缓存按 entry 路径键控；tmp_path 各测试互不重叠，仍显式
    清空以保证测试间无隐式顺序依赖。"""
    pi_compat._RUNTIME_VERSION_CACHE.clear()
    yield
    pi_compat._RUNTIME_VERSION_CACHE.clear()


def _identity(entry_path: str | None, entry_kind: str | None) -> PiVendorIdentity:
    return PiVendorIdentity(
        head_commit=None,
        head_commit_source="unknown",
        describe=None,
        expected_pin=None,
        expected_pin_source="unknown",
        entry_path=entry_path,
        entry_kind=entry_kind,
        node_version=None,
    )


# ── probe 解析 ───────────────────────────────────────────────────────────────


def test_probe_parses_version_from_entry_output(tmp_path):
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("9.9.9")\n', encoding="utf-8")
    assert probe_vendor_runtime_version(entry) == "9.9.9"


def test_probe_returns_none_on_non_version_output(tmp_path):
    entry = tmp_path / "rpc-entry.js"
    # exit 0 但输出无 semver —— 诚实返回 None，绝不猜。
    entry.write_text('console.log("hello")\n', encoding="utf-8")
    assert probe_vendor_runtime_version(entry) is None


def test_probe_returns_none_on_timeout(tmp_path, monkeypatch):
    entry = tmp_path / "rpc-entry.js"
    # 挂起的 timer 让 node 进程存活；预算收紧到 0.3s → TimeoutExpired → None。
    entry.write_text(
        "setTimeout(() => console.log('1.0.0'), 5000)\n", encoding="utf-8"
    )
    monkeypatch.setattr(pi_compat, "_RUNTIME_PROBE_TIMEOUT_S", 0.3)
    assert probe_vendor_runtime_version(entry) is None


def test_probe_returns_none_on_missing_entry(tmp_path):
    assert probe_vendor_runtime_version(tmp_path / "no-such-entry.js") is None


# ── 缓存语义 ─────────────────────────────────────────────────────────────────


def test_probe_cached_until_entry_mtime_changes(tmp_path, monkeypatch):
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("7.7.7")\n', encoding="utf-8")

    calls = {"n": 0}
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(pi_compat.subprocess, "run", counting_run)

    assert probe_vendor_runtime_version(entry) == "7.7.7"
    assert probe_vendor_runtime_version(entry) == "7.7.7"
    assert calls["n"] == 1, "产物未变（mtime 不变）不得重复 spawn"

    # rebuild 的可观测签名 = mtime 变化 → 缓存失效 → 重新 spawn。
    stat = entry.stat()
    bumped = stat.st_mtime_ns + 10_000_000
    os.utime(entry, ns=(bumped, bumped))
    assert probe_vendor_runtime_version(entry) == "7.7.7"
    assert calls["n"] == 2


def test_probe_caches_failures_by_mtime_too(tmp_path, monkeypatch):
    """失败（无 semver）也缓存：诊断循环不反复白付 spawn/超时成本。"""
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("hello")\n', encoding="utf-8")

    calls = {"n": 0}
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(pi_compat.subprocess, "run", counting_run)

    assert probe_vendor_runtime_version(entry) is None
    assert probe_vendor_runtime_version(entry) is None
    assert calls["n"] == 1


# ── 能力表（纯函数推断）─────────────────────────────────────────────────────


def test_capabilities_table_keys_are_closed():
    assert set(PI_VENDOR_CAPABILITIES) == {
        "clear_queue_ordered_shutdown",
        "prompt_disposition",
        "nested_parent_tool_call_id",
        "abort_batch_tool_results",
    }


def test_capabilities_at_v160_all_true():
    caps = vendor_capabilities("1.6.0")
    assert caps == {
        "clear_queue_ordered_shutdown": True,
        "prompt_disposition": True,
        "nested_parent_tool_call_id": True,
        "abort_batch_tool_results": True,
    }


def test_capabilities_at_v152_exclude_v160_only():
    caps = vendor_capabilities("v1.5.2")
    assert caps["clear_queue_ordered_shutdown"] is True
    assert caps["prompt_disposition"] is True
    assert caps["nested_parent_tool_call_id"] is True
    # 上游 #9986：abort 批量终态完整是 v1.6.0 行为
    assert caps["abort_batch_tool_results"] is False


def test_capabilities_unknown_version_all_false():
    assert set(vendor_capabilities(None).values()) == {False}
    assert set(vendor_capabilities("garbage").values()) == {False}


def test_parse_vendor_version_accepts_describe_forms():
    assert parse_vendor_version("1.6.0") == (1, 6, 0)
    assert parse_vendor_version("v1.6.0") == (1, 6, 0)
    assert parse_vendor_version("v1.6.0-3-gabc1234") == (1, 6, 0)
    assert parse_vendor_version("") is None
    assert parse_vendor_version(None) is None


# ── attach_runtime_version（identity 保持 stat-only 的 opt-in 语义）─────────


def test_identity_default_has_no_runtime_version():
    ident = _identity(None, None)
    assert ident.runtime_version is None
    assert ident.runtime_version_source == "unavailable"


def test_attach_runtime_version_skips_unbuilt_identity(tmp_path, monkeypatch):
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("2.0.0")\n', encoding="utf-8")
    probed = {"hit": False}

    def _fail_probe(_entry):
        probed["hit"] = True
        return "2.0.0"

    monkeypatch.setattr(pi_compat, "probe_vendor_runtime_version", _fail_probe)
    ident = _identity(str(entry), None)  # entry_kind None = 未构建
    attached = attach_runtime_version(ident)
    assert probed["hit"] is False, "未构建 entry 不值得 spawn 探针"
    assert attached.runtime_version is None
    assert attached.runtime_version_source == "unavailable"


def test_attach_runtime_version_fills_probe_fields(tmp_path, monkeypatch):
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("1.6.0")\n', encoding="utf-8")
    ident = _identity(str(entry), "bundle")
    attached = attach_runtime_version(ident)
    assert attached.runtime_version == "1.6.0"
    assert attached.runtime_version_source == "runtime_probe"
    # frozen dataclass：原对象不被原地改写
    assert ident.runtime_version is None
    # summary_line 携带 runtime version
    assert "runtime=1.6.0(runtime_probe)" in attached.summary_line()


def test_attach_runtime_version_keeps_unavailable_on_probe_failure(tmp_path):
    entry = tmp_path / "rpc-entry.js"
    entry.write_text('console.log("no version here")\n', encoding="utf-8")
    ident = _identity(str(entry), "bundle")
    attached = attach_runtime_version(ident)
    assert attached.runtime_version is None
    assert attached.runtime_version_source == "unavailable"
