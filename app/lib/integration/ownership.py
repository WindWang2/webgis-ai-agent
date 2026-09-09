"""Shared-file ownership 元契约（Quality V3 W2，ADR 见 01-architecture §A）。

10 个大型 worktree 并发开发下，共享文件（migrations / ADR / CHANGELOG /
生成物 / 快照 / registries）的协调此前纯靠约定——ADR 已发生 6×0118 撞号。
本模块把"谁负责生成/修改、按什么策略修改"变成机器可读规则
（``docs/integration/ownership.json``，手工权威），并提供：

- 规则校验：pattern 可匹配、owner/policy 属封闭词表、pattern 两两不相交
  （顺序敏感的 first-match 归属不允许歧义）；
- ``classify(path)``：路径 → 规则（integration manifest / merge sim 的
  归属底座）；
- 与 ``app/lib/quality/artifact_graph.DECLARED`` 的双向 parity（m-3）：
  每个生成物恰好命中一个 regenerate-dont-edit pattern；每个该类 pattern
  至少命中一个登记生成物。**单一事实源仍是 DECLARED**；本文件禁止复制
  generator/inputs 字段。

匹配语义：``fnmatch``（``*`` 跨 ``/``，目录 pattern 递归覆盖）。
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
OWNERSHIP_PATH = "docs/integration/ownership.json"

#: policy 词表（封闭）
POLICY_ADDITIVE_ONLY = "additive-only"
POLICY_APPEND_ONLY = "append-only"
POLICY_ALLOCATOR = "allocator"
POLICY_REGENERATE = "regenerate-dont-edit"
POLICY_SINGLE_WRITER = "single-writer"
POLICY_COORDINATED = "coordinated"
POLICY_VOCABULARY: FrozenSet[str] = frozenset({
    POLICY_ADDITIVE_ONLY, POLICY_APPEND_ONLY, POLICY_ALLOCATOR,
    POLICY_REGENERATE, POLICY_SINGLE_WRITER, POLICY_COORDINATED,
})

#: owner 域词表（封闭；与仓库实际 domain 对齐——registries 域 + 协调面域）
OWNER_VOCABULARY: FrozenSet[str] = frozenset({
    "core", "tools", "algorithms", "capabilities", "api", "frontend",
    "data", "geocompute", "lakehouse", "cartography", "workbench",
    "workflow", "harness", "science", "extensions", "quality",
    "integration", "observability", "security", "infra", "docs",
})

#: runner / CI 车道词表（suites 字段的合法值；与 quality_runner LANES 对齐）
SUITE_VOCABULARY: FrozenSet[str] = frozenset({
    "quick", "backend", "frontend", "science", "cartography", "data",
    "security", "quality", "perf", "integration", "real",
})

#: 单文件遍历硬上限（资源纪律：disjoint 校验不无限扫描）
_MAX_WALK_FILES = 60000
#: PR 风险评估时视为"必须协调"的策略集合
_CONFLICT_POLICIES: FrozenSet[str] = frozenset({
    POLICY_APPEND_ONLY, POLICY_ALLOCATOR, POLICY_REGENERATE,
    POLICY_SINGLE_WRITER,
})


@dataclass(frozen=True)
class OwnershipRule:
    pattern: str
    owner: str
    policy: str
    suites: Tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatchcase(path, self.pattern)

    def as_dict(self) -> Dict[str, object]:
        d: Dict[str, object] = {
            "pattern": self.pattern, "owner": self.owner,
            "policy": self.policy,
        }
        if self.suites:
            d["suites"] = list(self.suites)
        return d


@dataclass(frozen=True)
class OwnershipDocument:
    version: int
    adr_watermark: int
    migration_watermark: int
    rules: Tuple[OwnershipRule, ...]

    def classify(self, path: str) -> Optional[OwnershipRule]:
        """路径 → 第一条命中规则（规则已保证两两不相交，无歧义）。"""
        for rule in self.rules:
            if rule.matches(path):
                return rule
        return None

    def rules_by_policy(self, policy: str) -> List[OwnershipRule]:
        return [r for r in self.rules if r.policy == policy]

    @property
    def conflicting_policies(self) -> FrozenSet[str]:
        return _CONFLICT_POLICIES


def load_document(repo_root: Optional[Path] = None) -> OwnershipDocument:
    path = (repo_root or REPO_ROOT) / OWNERSHIP_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = tuple(
        OwnershipRule(
            pattern=r["pattern"], owner=r["owner"], policy=r["policy"],
            suites=tuple(r.get("suites", ())),
        )
        for r in raw["rules"]
    )
    return OwnershipDocument(
        version=int(raw["version"]),
        adr_watermark=int(raw["adr_watermark"]),
        migration_watermark=int(raw["migration_watermark"]),
        rules=rules,
    )


def validate_document(doc: OwnershipDocument,
                      repo_root: Optional[Path] = None) -> List[str]:
    """结构校验。返回错误清单（空 = 通过）。"""
    errors: List[str] = []
    root = repo_root or REPO_ROOT
    if doc.version != 1:
        errors.append(f"不支持的 ownership 版本: {doc.version}")
    seen_patterns: List[str] = []
    for rule in doc.rules:
        if rule.owner not in OWNER_VOCABULARY:
            errors.append(f"pattern {rule.pattern!r}: owner {rule.owner!r} 不在封闭词表")
        if rule.policy not in POLICY_VOCABULARY:
            errors.append(f"pattern {rule.pattern!r}: policy {rule.policy!r} 不在封闭词表")
        for suite in rule.suites:
            if suite not in SUITE_VOCABULARY:
                errors.append(f"pattern {rule.pattern!r}: suite {suite!r} 不在车道词表")
        seen_patterns.append(rule.pattern)
    # 两两不相交：对仓库真实文件采样判定（glob 交集判定无精确闭式解；
    # 真实文件采样对 manifest/merge-sim 的归属语义恰好是充分条件）
    files = _walk_repo_bounded(root)
    for i, path in enumerate(files):
        hits = [p for p in seen_patterns if fnmatch.fnmatchcase(path, p)]
        if len(hits) > 1:
            errors.append(f"文件 {path} 同时命中 {len(hits)} 条 pattern: {hits}")
        if len(errors) > 20:  # 有界：错误风暴时提前截断
            errors.append("…（错误超过 20 条，截断）")
            break
    # 自指防护（m-3）：ownership.json 自身不得命中任何 regenerate pattern
    for rule in doc.rules_by_policy(POLICY_REGENERATE):
        if rule.matches(OWNERSHIP_PATH):
            errors.append(f"pattern {rule.pattern!r} 覆盖 ownership.json 自身（自指）")
    return errors


def parity_with_artifact_graph(
    doc: OwnershipDocument, declared_artifacts: List[str],
) -> List[str]:
    """与 artifact_graph.DECLARED 双向 parity（m-3）。

    - 每个 DECLARED 生成物恰好命中一个 regenerate-dont-edit pattern；
    - 每个 regenerate-dont-edit pattern 至少命中一个 DECLARED 生成物。
    """
    errors: List[str] = []
    regen_rules = doc.rules_by_policy(POLICY_REGENERATE)
    for artifact in declared_artifacts:
        hits = [r.pattern for r in regen_rules if r.matches(artifact)]
        if len(hits) != 1:
            errors.append(
                f"生成物 {artifact} 命中 regenerate pattern 数={len(hits)}（须恰为 1）: {hits}")
    for rule in regen_rules:
        covered = [a for a in declared_artifacts if rule.matches(a)]
        if not covered:
            errors.append(f"regenerate pattern {rule.pattern!r} 未覆盖任何登记生成物")
    return errors


def risk_of_path(doc: OwnershipDocument, path: str) -> Tuple[str, Optional[OwnershipRule]]:
    """manifest/merge-sim 的风险归约：路径 → (high|medium|none, rule)。"""
    rule = doc.classify(path)
    if rule is None:
        return "none", None
    if rule.policy in _CONFLICT_POLICIES:
        return "high", rule
    if rule.policy == POLICY_ADDITIVE_ONLY:
        return "medium", rule
    return "none", rule


def _walk_repo_bounded(root: Path) -> List[str]:
    """有界遍历仓库文件（排除 VCS/依赖/构建产物），返回 repo 相对 posix 路径。"""
    exclude_parts = {".git", "node_modules", ".next", "__pycache__",
                     ".pytest_cache", "coverage", ".venv", "venv",
                     ".agent-work", ".zcode", ".agents"}
    out: List[str] = []
    stack = [root]
    while stack and len(out) < _MAX_WALK_FILES:
        current = stack.pop()
        for child in sorted(current.iterdir()):
            if len(out) >= _MAX_WALK_FILES:
                break
            if child.is_dir():
                if child.name in exclude_parts or child.name.startswith(".git"):
                    continue
                stack.append(child)
            else:
                out.append(child.relative_to(root).as_posix())
    return out
