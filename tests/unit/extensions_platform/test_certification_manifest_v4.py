"""V4 manifest 声明节的对抗性解析测试（ADR-0201）。

fail closed 矩阵：
- 认证探针引用未声明能力（跨节孤儿）→ 拒绝；
- 重复 skill_id → 拒绝；skill 契约超体量 / 不可序列化 → 拒绝；
- 探针 args 超体量 / 不可序列化 → 拒绝；
- V4 节出现在旧 api_version manifest → 拒绝（版本门控）；
- V4 节在 api 1.3.0 manifest 正常解析。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extensions_platform.manifest import manifest_from_dict


def _base_manifest(**overrides):
    base = {
        "schema_version": 1,
        "id": "certv4.pack",
        "name": "pack",
        "namespace": "certv4",
        "version": "1.0.0",
        "api_version": "1.3.0",
        "minimum_core_version": "0.1.0",
        "entry_point": "main",
        "tools": [
            {"name": "rect_area", "description": "d", "tier": 1, "side_effect": "pure"}
        ],
        "algorithms": [{"id": "rect_area_algo", "description": "d"}],
    }
    base.update(overrides)
    return base


class TestCertificationSection:
    def test_valid_section_parses(self):
        manifest, err = manifest_from_dict(_base_manifest(
            certification={
                "tools": {"rect_area": {"args": {"a": 1}, "expect_key": "area",
                                        "expect_value": 6.0, "tolerance": 1e-6}},
                "algorithms": {"rect_area_algo": {"args": {}, "replay": False}},
            }
        ))
        assert manifest is not None, err
        assert manifest.certification.tools["rect_area"].replay is True
        assert manifest.certification.algorithms["rect_area_algo"].replay is False

    def test_probe_referencing_undeclared_tool_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            certification={"tools": {"ghost": {"args": {}}}}
        ))
        assert manifest is None
        assert "undeclared" in (err or "")

    def test_probe_referencing_undeclared_algorithm_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            certification={"algorithms": {"ghost_algo": {"args": {}}}}
        ))
        assert manifest is None
        assert "undeclared" in (err or "")

    def test_oversized_probe_args_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            certification={"tools": {"rect_area": {"args": {"blob": "x" * 20000}}}}
        ))
        assert manifest is None
        assert "exceed" in (err or "")

    def test_unserializable_probe_args_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            certification={"tools": {"rect_area": {"args": {"bad": float("inf")}}}}
        ))
        assert manifest is None

    def test_section_under_old_api_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            api_version="1.2.0",
            certification={"tools": {"rect_area": {"args": {}}}},
        ))
        assert manifest is None
        assert "1.3.0" in (err or "")


class TestSkillsSection:
    def test_valid_skill_parses(self):
        manifest, err = manifest_from_dict(_base_manifest(
            skills=[{"skill_id": "area_skill", "contract": {"name": "n", "domain": "general"}}]
        ))
        assert manifest is not None, err
        assert manifest.namespaced_skill_id("area_skill") == "certv4.area_skill"

    def test_duplicate_skill_ids_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            skills=[
                {"skill_id": "dup", "contract": {"name": "n"}},
                {"skill_id": "dup", "contract": {"name": "n"}},
            ]
        ))
        assert manifest is None
        assert "duplicate ids in skills" in (err or "")

    def test_empty_contract_rejected(self):
        with pytest.raises(ValidationError):
            from app.extensions_platform.manifest import SkillDeclaration

            SkillDeclaration(skill_id="x", contract={})

    def test_oversized_contract_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            skills=[{"skill_id": "big", "contract": {"blob": "x" * 40000}}]
        ))
        assert manifest is None
        assert "exceed" in (err or "")

    def test_skills_under_old_api_rejected(self):
        manifest, err = manifest_from_dict(_base_manifest(
            api_version="1.1.0",
            skills=[{"skill_id": "skill_one", "contract": {"name": "n"}}],
        ))
        assert manifest is None
        assert "1.3.0" in (err or "")
