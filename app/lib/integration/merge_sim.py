"""跨分支合并模拟（Quality V3 W8，架构 §G / Subagent-A M-5 修订）。

在**不真实 merge** 的前提下对比两个分支的 semantic integration
manifest，输出语义冲突报告。报告 schema 强制三段（诚实披露落到契约）：

- ``checked``：明确检测过的冲突轴 + 结果；
- ``unknown``：本工具无法判定的面 + 原因（如语义 ID 提取降级、DDL
  表名提取失败）；
- ``not_checked``：声明上就检不了的类别（跨分支语义冲突如"A 改签名
  B 旧调用"的完整判定需要语言级语义分析——本工具只输出**交叉依赖
  热点**作为线索，明确列在 unknown，不谎报为 checked）。

阻塞级冲突（BLOCKING）：migration 同文件/同序号/同 down fork、
registry ID 撞号、single-writer 共享文件双改、events 词表双改。
非阻塞（ADVISORY）：API 路由/前端契约同文件共改（需人工语义审查）、
regenerate-dont-edit 双输入变更（合并后统一再生成即可）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.lib.integration import migrations_coord
from app.lib.integration.manifest import IntegrationManifest
from app.lib.integration.ownership import (
    POLICY_APPEND_ONLY,
    POLICY_REGENERATE,
    POLICY_SINGLE_WRITER,
    OwnershipDocument,
    load_document,
)

BLOCKING_AXES = (
    "migration_same_file", "migration_sequence_collision",
    "migration_forked_down", "registry_id_collision",
    "shared_single_writer", "events_vocabulary",
)
ADVISORY_AXES = (
    "api_route_cochange", "frontend_contract_cochange",
    "shared_regenerate", "cross_dependency_hotspot",
)


@dataclass
class AxisResult:
    axis: str
    severity: str                    # blocking | advisory | clear | unknown
    items: List[Dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"axis": self.axis, "severity": self.severity,
                "items": self.items}


@dataclass
class MergeConflictReport:
    base: str
    branches: List[str]
    axes: List[AxisResult] = field(default_factory=list)

    @property
    def blocking(self) -> List[AxisResult]:
        return [a for a in self.axes if a.severity == "blocking"]

    @property
    def advisory(self) -> List[AxisResult]:
        return [a for a in self.axes if a.severity == "advisory"]

    @property
    def unknown(self) -> List[AxisResult]:
        return [a for a in self.axes if a.severity == "unknown"]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def as_dict(self) -> Dict[str, object]:
        return {
            "base": self.base,
            "branches": self.branches,
            "ok": self.ok,
            "checked": [a.as_dict() for a in self.axes
                        if a.severity != "unknown"],
            "unknown": [a.as_dict() for a in self.unknown],
            "not_checked": [dict(axis) for axis in _NOT_CHECKED_AXES],
        }


#: 声明上不检测的类别（诚实契约的常量段）
_NOT_CHECKED_AXES = (
    {"axis": "semantic_api_breaking",
     "reason": "跨分支函数签名/路由语义 breaking 需要语言级语义分析；"
               "V2 api_compat 快照在合并后 master 上兜底"},
    {"axis": "runtime_behavior_conflicts",
     "reason": "运行时行为冲突（算法数值/并发语义）只能靠合并后的 "
               "integration lane 捕获"},
)


def _branch_reader(branch: str, repo_root: Path):
    """path → 分支 commit 中的文件内容（git show 优先，工作树回退）。

    优先 git show：分支文件只存在于各自 commit，工作树通常在别的分支
    上，磁盘读会拿到**错误分支**的内容。
    """
    import subprocess

    def read(path: str) -> Optional[str]:
        proc = subprocess.run(
            ["git", "show", f"{branch}:{path}"], cwd=repo_root,
            capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return proc.stdout
        try:
            return (repo_root / path).read_text(encoding="utf-8",
                                                errors="ignore")
        except OSError:
            return None
    return read


def compare_pair(
    a: IntegrationManifest, b: IntegrationManifest,
    repo_root: Optional[Path] = None,
    doc: Optional[OwnershipDocument] = None,
) -> MergeConflictReport:
    """两个 manifest 的全轴对比（纯函数）。"""
    root = repo_root or Path.cwd()
    doc = doc or load_document(root)
    report = MergeConflictReport(
        base=a.base, branches=[a.branch, b.branch])

    def _add(axis: str, severity: str, items: Optional[List[Dict[str, str]]] = None):
        report.axes.append(AxisResult(axis, severity, items or []))

    # 1. migration 轴（分支 commit 内容解析器，工作树无关）
    collisions = migrations_coord.branch_collisions(
        a.migrations, b.migrations, repo_root=root,
        base_content=_branch_reader(a.branch, root),
        other_content=_branch_reader(b.branch, root))
    if collisions.get("same_revision"):
        _add("migration_same_file", "blocking", collisions["same_revision"])
    else:
        _add("migration_same_file", "clear")
    if collisions.get("sequence_collision"):
        _add("migration_sequence_collision", "blocking",
             collisions["sequence_collision"])
    else:
        _add("migration_sequence_collision", "clear")
    if collisions.get("forked_down_revision"):
        _add("migration_forked_down", "blocking",
             collisions["forked_down_revision"])
    else:
        _add("migration_forked_down", "clear")

    # 2. registry ID 轴（三类注册表；提取降级 → unknown 不比对）
    for kind, ids_a, ids_b in (
        ("tool", a.tools, b.tools),
        ("algorithm", a.algorithms, b.algorithms),
        ("capability", a.capabilities, b.capabilities),
    ):
        axis = f"{kind}_id_collision" if kind != "tool" else "registry_id_collision"
        if (not a.ids_extracted) or (not b.ids_extracted):
            _add(axis, "unknown",
                 [{"detail": f"分支语义 ID 提取降级（ids_extracted=False），"
                             f"{kind} 撞号未比对"}])
            continue
        dup = sorted(set(ids_a) & set(ids_b))
        if dup:
            _add(axis, "blocking",
                 [{"ids": ",".join(dup),
                   "detail": f"{kind} ID 被双方新增（同一注册表撞号）"}])
        else:
            _add(axis, "clear")

    # 3. 共享文件轴（按 ownership policy 分级）
    single = sorted(set(a.shared_files) & set(b.shared_files) & set(
        shared_paths_with_policy(doc, POLICY_SINGLE_WRITER,
                                 sorted(set(a.shared_files)
                                        | set(b.shared_files)))))
    if single:
        _add("shared_single_writer", "blocking",
             [{"file": f, "detail": "single-writer 共享文件被双方修改"}
              for f in single])
    else:
        _add("shared_single_writer", "clear")
    all_shared = sorted(set(a.shared_files) | set(b.shared_files))
    co_shared = set(a.shared_files) & set(b.shared_files)
    regen = sorted(co_shared & set(
        shared_paths_with_policy(doc, POLICY_REGENERATE, all_shared)))
    append = sorted(co_shared & set(
        shared_paths_with_policy(doc, POLICY_APPEND_ONLY, all_shared)))
    regen_items = [{"file": f, "detail": "生成物输入双方都变：合并后统一再生成"}
                   for f in regen]
    regen_items += [{"file": f, "detail": "append-only 文件双改：检查追加次序与语义"}
                    for f in append]
    if regen_items:
        _add("shared_regenerate", "advisory", regen_items)
    else:
        _add("shared_regenerate", "clear")

    # 4. events 词表轴
    if a.events and b.events:
        _add("events_vocabulary", "blocking",
             [{"detail": "双方都改 EVENT_CATALOG（词表收敛面），必须串行"}])
    else:
        _add("events_vocabulary", "clear")

    # 5. API / 前端契约共改（advisory：人工语义审查）
    api_co = sorted(set(a.api_routes) & set(b.api_routes))
    if api_co:
        _add("api_route_cochange", "advisory",
             [{"file": f, "detail": "路由文件共改（breaking 判定由 V2 api_compat 兜底）"}
              for f in api_co])
    else:
        _add("api_route_cochange", "clear")
    fe_co = sorted(set(a.frontend_contracts) & set(b.frontend_contracts))
    if fe_co:
        _add("frontend_contract_cochange", "advisory",
             [{"file": f, "detail": "前端契约文件共改"} for f in fe_co])
    else:
        _add("frontend_contract_cochange", "clear")

    # 6. 交叉依赖热点（M-5：unknown 段，不谎报为语义判定）
    hotspot = sorted(
        (set(a.files_changed) & set(b.files_changed))
        - set(api_co) - set(fe_co) - set(single) - set(regen))
    hotspot = [f for f in hotspot
               if f.startswith("app/") or f.startswith("frontend/src")]
    if hotspot:
        _add("cross_dependency_hotspot", "unknown",
             [{"file": f, "detail": "双方共改的应用代码文件：语义冲突可能性"
                                    "需人工/合并后 lane 判定"} for f in hotspot])
    return report


def shared_paths_with_policy(doc: OwnershipDocument, policy: str,
                             paths: List[str]) -> List[str]:
    """paths 中命中指定 policy 的文件（谓词式判定，无全仓遍历）。"""
    rules = doc.rules_by_policy(policy)
    return [p for p in paths if any(r.matches(p) for r in rules)]
