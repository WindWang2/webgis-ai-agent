"""Dynamic Provider —— induced 技能资产的隔离装载面（ADR-0191 D3/D5）。

与审定 core 库（``gis_harness.skills.loader`` 的 fail-loud 单例）的
边界红线：induced 资产走**独立目录、独立 fail-closed 装载**，本模块
对 core 单例零写入（运行期不自修改技能，ADR-0182 §2.5）。

装载即校验：``yaml.safe_load``（绝不执行文档对象）→
``SkillContract.model_validate``（extra=forbid）→ 去毒扫描；任何一环
失败即进 quarantine、不进索引（fail-closed）。id 做路径安全守卫
（拒绝分隔符与 ``..``，防目录穿越）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import yaml

from app.services.gis_harness.skills.contract import SkillContract

from app.services.gis_skills.induction.sandbox_validator import (
    iter_strings,
    scan_for_injection,
)

_QUARANTINE_DIR = "quarantine"


def _safe_skill_filename(skill_id: str) -> Optional[str]:
    """路径安全守卫：只放行字母/数字/点/下划线/连字符，且不含 ``..``。"""
    if not skill_id or len(skill_id) > 160:
        return None
    if "/" in skill_id or "\\" in skill_id or ".." in skill_id:
        return None
    if not all(ch.isalnum() or ch in "._-" for ch in skill_id):
        return None
    return f"{skill_id}.yaml"


class InducedSkillStore:
    """induced 技能草案资产的目录库（fail-closed 装载 + 隔离区）。"""

    def __init__(self, root) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 写入（引擎专用；写入前产物已过沙盒三门）─────────────────────
    def save(self, compiled) -> Path:
        skill_id = compiled.contract.id
        filename = _safe_skill_filename(skill_id)
        if filename is None:
            raise ValueError(f"induced skill id 不安全: {skill_id!r}")
        target = self.root / filename
        target.write_text(compiled.yaml_text, encoding="utf-8")
        (self.root / f"{skill_id}.report.json").write_text(
            json.dumps({"provenance": compiled.provenance,
                        "violations": compiled.violations,
                        "parameters": [p.model_dump()
                                       for p in compiled.parameters]},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        return target

    # ── 读取（装载即校验；fail-closed）───────────────────────────────
    def load(self, skill_id: str) -> Optional[SkillContract]:
        filename = _safe_skill_filename(skill_id)
        if filename is None:
            return None
        path = self.root / filename
        if not path.exists():
            return None
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            contract = SkillContract.model_validate(doc)
        except Exception:  # noqa: BLE001 - 形态非法 fail-closed
            self.quarantine(skill_id, reason="invalid_schema")
            return None
        findings = [f"{path}: {tag}" for _, text in
                    iter_strings(contract.model_dump())
                    if (tag := scan_for_injection(text))]
        if findings:
            self.quarantine(skill_id, reason="detox:" + findings[0])
            return None
        return contract

    # ── 索引与隔离 ───────────────────────────────────────────────────
    def list_ids(self) -> List[str]:
        return sorted(p.stem for p in self.root.glob("*.yaml")
                      if not p.name.endswith(".report.json"))

    def quarantine(self, skill_id: str, *, reason: str = "") -> Optional[Path]:
        filename = _safe_skill_filename(skill_id)
        if filename is None:
            return None
        src = self.root / filename
        if not src.exists():
            return None
        qdir = self.root / _QUARANTINE_DIR
        qdir.mkdir(exist_ok=True)
        target = qdir / filename
        src.replace(target)
        meta = self.root / f"{skill_id}.report.json"
        if meta.exists():
            meta.replace(qdir / f"{skill_id}.report.json")
        (qdir / f"{skill_id}.quarantine.txt").write_text(
            reason, encoding="utf-8")
        return target

    def quarantine_ids(self) -> List[str]:
        qdir = self.root / _QUARANTINE_DIR
        if not qdir.exists():
            return []
        return sorted(p.stem for p in qdir.glob("*.yaml"))


def register_dynamic_capability(registry, descriptor) -> bool:
    """幂等登记：已存在返回 False；新登记经 ``register_dynamic`` 纪律。"""
    if registry.has(descriptor.id):
        return False
    registry.register_dynamic(descriptor)
    return True


__all__ = [
    "InducedSkillStore",
    "register_dynamic_capability",
]
