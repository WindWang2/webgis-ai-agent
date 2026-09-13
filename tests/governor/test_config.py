"""Governor 配置与 manifest 单测：env 旋钮、封闭词表、provisional 语义。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.governor.config import (
    GovernorConfig,
    GovernorMode,
    backpressure_subsystems,
    load_manifest,
)
from app.services.governor.contract import Dimension


@pytest.fixture()
def manifest_path(tmp_path: Path) -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


class TestRepoManifest:
    def test_repo_manifest_loads_with_all_scopes(self, manifest_path):
        budgets = load_manifest(manifest_path)
        for scope in ("global", "session", "goal", "turn"):
            assert scope in budgets, scope
        sb = budgets["session"]
        assert sb.limits[Dimension.MEMORY_BYTES] > 0
        assert sb.limits[Dimension.CONTEXT_TOKENS] > 0
        assert sb.provisional is True  # 校准前绝不硬拒

    def test_repo_manifest_valid_json_shape(self, manifest_path):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw["version"] == "rg.v1"
        assert isinstance(raw["budgets"], list) and raw["budgets"]


class TestManifestParsing:
    def test_unknown_dimension_rejected(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"version": "rg.v1", "budgets": [
            {"scope": "session", "limits": {"not_a_dim": 1}}]}), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown dimension"):
            load_manifest(p)

    def test_nonpositive_limit_rejected(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"version": "rg.v1", "budgets": [
            {"scope": "session", "limits": {"memory_bytes": 0}}]}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_manifest(p)

    def test_missing_scope_rejected(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"version": "rg.v1", "budgets": [
            {"limits": {}}]}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing scope"):
            load_manifest(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "absent.json")


class TestGovernorConfig:
    def test_default_mode_is_enforce(self, monkeypatch):
        monkeypatch.delenv("GOVERNOR_MODE", raising=False)
        assert GovernorConfig().mode is GovernorMode.ENFORCE

    def test_observe_mode_via_env(self, monkeypatch):
        monkeypatch.setenv("GOVERNOR_MODE", "observe")
        assert GovernorConfig().mode is GovernorMode.OBSERVE

    def test_invalid_mode_falls_back_to_enforce_is_rejected_by_enum(self, monkeypatch):
        # 无效值必须显式可辨（不静默成功）—— 这里选择抛错路径之外的保守行为：
        # GovernorMode() 对未知字符串抛 ValueError，调用方（GovernorConfig）
        # 不吞 —— 让配置错误在启动即暴露。
        monkeypatch.setenv("GOVERNOR_MODE", "yolo")
        with pytest.raises(ValueError):
            GovernorConfig()

    def test_env_int_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("GOVERNOR_SESSION_HEAVY", "abc")
        assert GovernorConfig().session_heavy_concurrency == 1

    def test_backpressure_vocab_is_closed(self):
        assert backpressure_subsystems() == ("raster", "browser", "export", "external", "llm")
