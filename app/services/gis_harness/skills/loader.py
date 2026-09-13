"""Skill 库装载器 —— 版本化 YAML 资产 → 契约对象（ADR-0182 §2.5）。

- 资产位于 ``app/services/gis_harness/skills/library/``：技能 YAML
  （``<pack>/*.yaml``，多文档或列表）+ 组合 YAML（``compositions.yaml``）；
- 装载严格：未知字段拒绝（extra=forbid 语义由 pydantic 模型边界保证）、
  校验违规 **fail loud**（SkillLibraryError）—— 知识库不完整宁可拒绝
  启动，不允许静默悬空引用；
- 单例 + reset（测试隔离），与 get_recipe_registry 同模式。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from app.services.gis_harness.skills.bridges import default_validation_predicates
from app.services.gis_harness.skills.composition import (
    CompositionMember,
    SkillComposition,
)
from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.packs import SkillPackRegistry
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.validation import validate_skill_library

logger = logging.getLogger(__name__)

#: 资产根目录（app/services/gis_harness/skills/library）。
LIBRARY_DIR = Path(__file__).resolve().parent / "library"


class SkillLibraryError(RuntimeError):
    """技能库装载/校验失败（fail loud）。"""


def _load_documents(path: Path) -> list:
    try:
        with path.open("r", encoding="utf-8") as fh:
            docs = list(yaml.safe_load_all(fh))
    except (OSError, yaml.YAMLError) as exc:
        raise SkillLibraryError(f"skill library: 无法读取 {path.name}: {exc}") from exc
    payload: list = []
    for doc in docs:
        if doc is None:
            continue
        if isinstance(doc, list):
            payload.extend(doc)
        elif isinstance(doc, dict):
            if "skills" in doc and isinstance(doc["skills"], list):
                payload.extend(doc["skills"])
            else:
                payload.append(doc)
        else:
            raise SkillLibraryError(
                f"skill library: {path.name} 存在非法文档形态（{type(doc).__name__}）")
    return payload


def load_skill_library(
    library_dir: Optional[Path] = None,
) -> Tuple[List[SkillContract], List[SkillComposition], List[str]]:
    """从资产目录装载技能与组合。返回 (skills, compositions, violations)。

    纯装载 + 校验，不注册单例；violations 非空由调用方决定 fail-loud
    （单例装载函数 ``get_skill_library`` 会抛 SkillLibraryError）。
    """
    root = library_dir or LIBRARY_DIR
    if not root.is_dir():
        raise SkillLibraryError(f"skill library: 资产目录缺失 {root}")

    skills: List[SkillContract] = []
    for path in sorted(root.glob("core/*.yaml")):
        for entry in _load_documents(path):
            if "composition_id" in entry:
                continue
            try:
                skills.append(SkillContract.model_validate(entry))
            except Exception as exc:  # noqa: BLE001 - 装载错误必须带资产名
                raise SkillLibraryError(
                    f"skill library: {path.relative_to(root)} 技能装载失败: {exc}"
                ) from exc

    compositions: List[SkillComposition] = []
    comp_path = root / "compositions.yaml"
    if comp_path.exists():
        for entry in _load_documents(comp_path):
            if "compositions" in entry and isinstance(entry["compositions"], list):
                entry_list = entry["compositions"]
            elif "composition_id" in entry:
                entry_list = [entry]
            else:
                continue
            for item in entry_list:
                members = [CompositionMember.model_validate(m)
                           for m in item.get("members", [])]
                data = {k: v for k, v in item.items() if k != "members"}
                compositions.append(SkillComposition(members=members, **data))

    predicates = default_validation_predicates()
    violations = validate_skill_library(
        skills,
        compositions=compositions,
        capability_exists=predicates["capability_exists"],
        recipe_exists=predicates["recipe_exists"],
        ontology_task_exists=predicates["ontology_task_exists"],
        artifact_type_exists=predicates["artifact_type_exists"],
        precondition_exists=predicates["precondition_exists"],
    )
    return skills, compositions, violations


class SkillLibrary:
    """技能库只读门面：契约集合 + resolver + 组合 + 包登记表。"""

    def __init__(
        self,
        skills: List[SkillContract],
        compositions: List[SkillComposition],
        *,
        capability_exists=None,
    ) -> None:
        self.skills = list(skills)
        self.compositions = list(compositions)
        self.packs = SkillPackRegistry()
        self.resolver = SkillResolver(
            self.skills, capability_exists=capability_exists)

    def get(self, skill_id: str) -> Optional[SkillContract]:
        return self.resolver.get(skill_id)

    @property
    def skill_count(self) -> int:
        return len(self.skills)

    def composition(self, composition_id: str) -> Optional[SkillComposition]:
        for c in self.compositions:
            if c.composition_id == composition_id:
                return c
        return None

    def fingerprint(self) -> str:
        import hashlib
        import json
        payload = {
            "skills": sorted(s.fingerprint() for s in self.skills),
            "compositions": [c.model_dump() for c in self.compositions],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


_singleton: Optional[SkillLibrary] = None


def get_skill_library() -> SkillLibrary:
    """进程级单例（装载期校验违规 → SkillLibraryError，fail loud）。"""
    global _singleton
    if _singleton is None:
        skills, compositions, violations = load_skill_library()
        if violations:
            raise SkillLibraryError(
                "skill library 校验失败（fail loud）：\n- "
                + "\n- ".join(violations[:32]))
        _singleton = SkillLibrary(skills, compositions)
        logger.info("skill library loaded: %d skills / %d compositions",
                    len(skills), len(compositions))
    return _singleton


def reset_skill_library() -> None:
    global _singleton
    _singleton = None


__all__ = [
    "LIBRARY_DIR",
    "SkillLibraryError",
    "SkillLibrary",
    "load_skill_library",
    "get_skill_library",
    "reset_skill_library",
]
