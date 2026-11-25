"""ADR-0301 供应链与能力快照契约测试。

覆盖四组事实：
1. 能力快照（feature → min-version 唯一映射点、边界、证据诚实性）；
2. vendor 树形态（gitlink vs symlink 回归类）与 pin/mode 三元组；
3. 构建清单对账状态机（consistent/manifest_only/pin_mismatch/entry_drift/
   unparsed/absent）；
4. PI_VENDOR_DIR 开发 override（合法/非法路径）与身份来源标注。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app.services.chat import pi_compat
from app.services.chat.pi_compat import (
    BUILD_MANIFEST_NAME,
    GITLINK_MODE,
    PiCapability,
    PiVendorIdentity,
    _expected_pin,
    _manifest_facts,
    _vendor_root,
    capabilities_for_version,
)


# ── 1. 能力快照 ──────────────────────────────────────────────────────────────

def test_capability_table_is_total():
    """封闭词表的每个成员都必须有 min-version 映射（新增成员忘登记 = 测试红）。"""
    for cap in PiCapability:
        assert cap in pi_compat._CAPABILITY_MIN_VERSION, f"missing mapping: {cap}"


def test_snapshot_v151_floor_features_only():
    caps = capabilities_for_version("1.5.1", "entry_probe")
    assert caps.has(PiCapability.NATIVE_TOOL_DISCOVERY)
    assert caps.has(PiCapability.CODEMODE_SANDBOX)
    assert caps.has(PiCapability.NESTED_TOOL_EVENTS)
    assert caps.has(PiCapability.ORDERED_SHUTDOWN)
    assert not caps.has(PiCapability.PROVIDER_FAILURE_EVENTS)
    assert not caps.has(PiCapability.EXTENSION_ERROR_EVENTS)
    assert not caps.has(PiCapability.DECISION_MODEL_CLASSIFY)


def test_snapshot_v160_and_v161_boundaries():
    v160 = capabilities_for_version("v1.6.0", "entry_probe")
    assert v160.has(PiCapability.EXTENSION_ERROR_EVENTS)
    assert v160.has(PiCapability.ABORT_BATCH_TERMINAL)
    assert v160.has(PiCapability.RPC_SESSION_LIFECYCLE_FIX)
    assert v160.has(PiCapability.UNREPORTED_DIAGNOSTICS)
    assert not v160.has(PiCapability.PROVIDER_FAILURE_EVENTS)

    v161 = capabilities_for_version("1.6.1", "entry_probe")
    assert v161.has(PiCapability.PROVIDER_FAILURE_EVENTS)
    assert v161.has(PiCapability.DECISION_MODEL_CLASSIFY)
    assert v161.has(PiCapability.RPC_LINE_SCAN_FIX)
    assert v161.unsupported_names() == []


def test_snapshot_unknown_version_supports_nothing():
    """版本事实缺失 → 一律 unsupported（不谎报）；证据标 unknown。"""
    caps = capabilities_for_version(None, "unknown")
    assert caps.version_source == "unknown"
    assert caps.supported_names() == []
    assert caps.unsupported_names() == sorted(c.value for c in PiCapability)
    assert caps.evidence[PiCapability.CODEMODE_SANDBOX.value] == "unknown"


def test_snapshot_evidence_names_its_source():
    caps = capabilities_for_version("1.6.1", "entry_probe")
    assert caps.evidence[PiCapability.PROVIDER_FAILURE_EVENTS.value] == "entry_probe>=v1.6.1"
    described = capabilities_for_version("1.6.1", "describe")
    assert described.evidence[PiCapability.PROVIDER_FAILURE_EVENTS.value] == "describe>=v1.6.1"


def test_snapshot_prerelease_version_parses_prefix():
    """上游 prerelease（如 1.7.0-rc.1）按主版本判定，不抛异常。"""
    caps = capabilities_for_version("1.7.0-rc.1", "entry_probe")
    assert caps.has(PiCapability.RPC_LINE_SCAN_FIX)


# ── 2. vendor 树形态（gitlink vs symlink 回归类）────────────────────────────

def _git_repo_with_vendor_entry(tmp_path, mode_pin):
    """建一个含 vendor/pi 树条目的临时 git 仓库，返回 (repo, pin_sha)。"""
    repo = tmp_path / "super"
    repo.mkdir()
    def git(*args):
        res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "README.md").write_text("x", encoding="utf-8")
    if mode_pin == "symlink":
        target = tmp_path / "elsewhere" / "pi"
        target.mkdir(parents=True)
        (repo / "vendor").mkdir()
        (repo / "vendor" / "pi").symlink_to(target)
        git("add", "vendor/pi")  # symlink 进入提交（#2169 回归现场同型）
    else:
        # gitlink：index 中直接写入 160000 条目（无工作树内容）。
        (repo / "vendor").mkdir()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"160000,{mode_pin},vendor/pi"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
    git("add", "README.md")
    git("commit", "-qm", "init")
    return repo


def test_expected_pin_reports_symlink_mode_explicitly(tmp_path, monkeypatch):
    """#2169 回归类：120000 形态必须显式暴露 mode，而不是静默 unknown。"""
    repo = _git_repo_with_vendor_entry(tmp_path, "symlink")
    monkeypatch.setattr(pi_compat, "REPO_ROOT", repo)
    pin, source, mode = _expected_pin()
    assert pin is None  # symlink blob 不是 40-hex pin
    assert mode == "120000"
    assert source == "unknown"


def test_expected_pin_gitlink_mode(tmp_path, monkeypatch):
    sha = "a" * 40
    repo = _git_repo_with_vendor_entry(tmp_path, sha)
    monkeypatch.setattr(pi_compat, "REPO_ROOT", repo)
    pin, source, mode = _expected_pin()
    assert pin == sha
    assert mode == GITLINK_MODE == "160000"
    assert source == "git"


def test_identity_flags_non_gitlink_tree():
    sha = "b" * 40
    healthy = PiVendorIdentity(
        head_commit=sha,
        head_commit_source="git",
        describe="v1.6.1",
        expected_pin=sha,
        expected_pin_source="git",
        entry_path=None,
        entry_kind=None,
        node_version=None,
        tree_mode="160000",
        tree_mode_source="git",
    )
    assert healthy.vendor_tree_not_gitlink is False
    # #2169 回归形态：tree_mode=120000（symlink）
    broken = PiVendorIdentity(
        head_commit=None,
        head_commit_source="unknown",
        describe=None,
        expected_pin=None,
        expected_pin_source="unknown",
        entry_path=None,
        entry_kind=None,
        node_version=None,
        tree_mode="120000",
        tree_mode_source="git",
    )
    assert broken.vendor_tree_not_gitlink is True


def test_identity_gitless_image_reports_unknown_tree():
    ident = PiVendorIdentity(
        head_commit=None,
        head_commit_source="unknown",
        describe=None,
        expected_pin=None,
        expected_pin_source="unknown",
        entry_path="/x",
        entry_kind="bundle",
        node_version=None,
    )
    assert ident.vendor_tree_not_gitlink is None  # 无 git 事实 → 不可判定


# ── 3. 构建清单对账 ──────────────────────────────────────────────────────────

def _write_manifest(vendor_dir, pin="c" * 40, entry_digest="d" * 16):
    (vendor_dir / BUILD_MANIFEST_NAME).write_text(json.dumps({
        "pin": pin,
        "describe": "v1.6.1",
        "node_version": "v24.0.0",
        "lockfile_sha256_16": "f" * 16,
        "entry": "packages/coding-agent/dist/bundle/rpc-entry.js",
        "entry_sha256_16": entry_digest,
        "built_at": "2026-11-26T00:00:00Z",
        "builder": "test",
    }), encoding="utf-8")


def test_manifest_absent_when_not_built(tmp_path):
    assert _manifest_facts(tmp_path, None, None) == ("absent", None)


def test_manifest_unparsed_on_corrupt_content(tmp_path):
    (tmp_path / BUILD_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert _manifest_facts(tmp_path, None, None) == ("unparsed", None)


def test_manifest_pin_mismatch_fails_loud(tmp_path, monkeypatch):
    """清单 pin ≠ 子模块 checkout → pin_mismatch（重建即可修复）。"""
    vendor = tmp_path
    _write_manifest(vendor, pin="e" * 40)
    # HEAD = c*40（_run_git 在非 git 目录返回 None → head unknown → 无法对账）
    # 这里 monkeypatch _head_commit 给出真实 git 事实。
    monkeypatch.setattr(pi_compat, "_head_commit", lambda _d: ("c" * 40, "git"))
    state, pin = _manifest_facts(vendor, None, None)
    assert state == "pin_mismatch"
    assert pin == "e" * 40


def test_manifest_entry_drift_is_observable_but_not_pin_failure(tmp_path, monkeypatch):
    """入口指纹漂移（上游 esbuild 非字节级可复现 / 或被替换）→ entry_drift，
    pin 对账不受影响。"""
    vendor = tmp_path
    _write_manifest(vendor, pin="c" * 40, entry_digest="aaaa")
    monkeypatch.setattr(pi_compat, "_head_commit", lambda _d: ("c" * 40, "git"))
    state, _pin = _manifest_facts(vendor, "/some/rpc-entry.js", entry_digest="bbbb")
    assert state == "entry_drift"


def test_manifest_consistent(tmp_path, monkeypatch):
    vendor = tmp_path
    _write_manifest(vendor, pin="c" * 40, entry_digest="aaaa")
    monkeypatch.setattr(pi_compat, "_head_commit", lambda _d: ("c" * 40, "git"))
    state, pin = _manifest_facts(vendor, "/some/rpc-entry.js", entry_digest="aaaa")
    assert state == "consistent"
    assert pin == "c" * 40


def test_manifest_only_when_gitless_image(tmp_path, monkeypatch):
    """镜像内无 .git：清单是唯一 pin 证据，状态不冒充已对账。"""
    vendor = tmp_path
    _write_manifest(vendor, pin="c" * 40)
    monkeypatch.setattr(pi_compat, "_head_commit", lambda _d: (None, "unknown"))
    state, pin = _manifest_facts(vendor, None, None)
    assert state == "manifest_only"
    assert pin == "c" * 40


# ── 4. PI_VENDOR_DIR 开发 override ───────────────────────────────────────────

def test_vendor_root_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("PI_VENDOR_DIR", raising=False)
    path, source = _vendor_root()
    assert source == "default"
    assert path == pi_compat.VENDOR_PI_DIR


def test_vendor_root_honors_valid_override(tmp_path, monkeypatch):
    override = tmp_path / "pi-dev"
    override.mkdir()
    (override / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PI_VENDOR_DIR", str(override))
    path, source = _vendor_root()
    assert source == "env_override"
    assert path == override


def test_vendor_root_ignores_invalid_override(tmp_path, monkeypatch):
    bogus = tmp_path / "not-a-pi"
    bogus.mkdir()
    monkeypatch.setenv("PI_VENDOR_DIR", str(bogus))
    path, source = _vendor_root()
    assert source == "default"
    assert path == pi_compat.VENDOR_PI_DIR


# ── live 本 checkout 的真相（gitlink 已恢复 @v1.6.1）─────────────────────────

def test_this_checkout_is_gitlink_at_v161():
    """本仓库 checkout 自身：vendor/pi 必须已是 gitlink 且 describe=v1.6.1。

    这是 #2169 symlink 回归的长期哨兵：谁再把 gitlink 换回 symlink/其他形态，
    单测车道立即红，不必等 doctor/CI 镜像构建暴露。
    """
    pin, source, mode = _expected_pin()
    assert source == "git"
    assert mode == GITLINK_MODE
    assert pin == "ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe"
    ident = pi_compat.pi_vendor_identity()
    assert ident.vendor_tree_not_gitlink is False
    assert ident.observed_version == "1.6.1"
    assert ident.observed_version_source == "entry_probe"
    assert ident.manifest_state in ("consistent", "manifest_only", "absent")
