"""Workflow Package & Versioning —— 编译产物的不可变包与 semver 兼容性。

把一次 V4 编译固化为**不可变、可校验、可重放**的工作流包：

    WorkflowPackage = package_id + semver + compiler_version
                      + methodology/recipe 指纹 + canonical compiled form
                      + sha256 fingerprint

红线：

- 包是编译产物的派生（纯函数 emit），不是第二事实源：recompile 同输入
  必得同指纹（reproducibility 红线）；
- compiled form 有界：只含语义契约（方法族/方法/DAG/义务链/完成契约/
  制图义务/获取声明/参数），不含证据倾倒；
- 兼容性裁决确定性：major 不同 → 不兼容（需 recompile/migrate）；minor
  更新向后兼容；patch 恒兼容。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_v4.compiler_v4 import (
    WORKFLOW_COMPILER_VERSION,
    WorkflowCompilationV4,
)

#: 包 schema 版本（compiled form 结构演进时递增 minor）。
WORKFLOW_PACKAGE_SCHEMA_VERSION = "1.0.0"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

_MAX_COMPILED_FORM_BYTES = 64_000


def parse_semver(version: str) -> Optional[Tuple[int, int, int]]:
    m = _SEMVER_RE.match(str(version or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


class WorkflowPackage(BaseModel):
    """不可变工作流包（emit 后不再变更；演进 = 发新版本）。"""
    package_id: str                    # recipe_id（工作流承载者）
    version: str = "1.0.0"             # 包 semver
    schema_version: str = WORKFLOW_PACKAGE_SCHEMA_VERSION
    compiler_version: str = WORKFLOW_COMPILER_VERSION
    methodology_family: str = ""
    recipe_fingerprint: str = ""       # recipe 内容指纹（workflow_schema）
    methodology_fingerprint: str = ""  # 方法论注册表指纹
    compiled_form: Dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = ""              # canonical compiled form sha256

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id[:64],
            "version": self.version,
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "methodology_family": self.methodology_family[:40],
            "recipe_fingerprint": self.recipe_fingerprint[:64],
            "methodology_fingerprint": self.methodology_fingerprint[:64],
            "fingerprint": self.fingerprint[:64],
            "compiled_form": self.compiled_form,
        }


class CompatibilityVerdict(BaseModel):
    """包 ↔ 当前编译器的兼容性裁决（确定性）。"""
    compatible: bool
    reason_code: str                   # OK / COMPILER_MAJOR_MISMATCH / MALFORMED_VERSION / SCHEMA_NEWER
    detail: str = ""
    migration_required: bool = False
    migration_hint: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "reason_code": self.reason_code[:48],
            "detail": self.detail[:200],
            "migration_required": self.migration_required,
            "migration_hint": self.migration_hint[:200],
        }


def _canonical_fingerprint(compiled: Dict[str, Any]) -> str:
    payload = json.dumps(
        compiled, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def next_version(
    previous: Optional[str], *, change: str,
) -> str:
    """确定性版本推进：change ∈ {major, minor, patch}（契约破坏/增补/文案）。"""
    cur = parse_semver(previous or "1.0.0") or (1, 0, 0)
    major, minor, patch = cur
    if change == "major":
        return f"{major + 1}.0.0"
    if change == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def emit_workflow_package(
    compilation: WorkflowCompilationV4,
    *,
    version: str = "1.0.0",
) -> WorkflowPackage:
    """编译产物 → 不可变包（纯函数；同输入同指纹）。"""
    base = compilation.base
    compiled: Dict[str, Any] = {
        "schema_version": WORKFLOW_PACKAGE_SCHEMA_VERSION,
        "recipe_id": base.recipe_id[:64],
        "query_normalized": base.query.strip()[:200],
        "methodology_family": compilation.methodology_family[:40],
        "method_qualification": compilation.method_qualification,
        "typed_dag": compilation.typed_dag,
        "completion_contract": base.completion_contract,
        "stage_sequence": compilation.full_stage_sequence,
    }
    recipe_fp = ""
    if base.recipe_id:
        from app.services.gis_harness.recipes import get_recipe_registry
        from app.services.gis_harness.workflow_schema import (
            recipe_content_fingerprint,
        )
        recipe = get_recipe_registry().get(base.recipe_id)
        if recipe is not None:
            recipe_fp = recipe_content_fingerprint(recipe)
    pkg = WorkflowPackage(
        package_id=base.recipe_id[:64],
        version=version,
        compiler_version=WORKFLOW_COMPILER_VERSION,
        methodology_family=compilation.methodology_family[:40],
        recipe_fingerprint=recipe_fp[:64],
        methodology_fingerprint=compilation.methodology_fingerprint[:64],
        compiled_form=compiled,
    )
    pkg.fingerprint = _canonical_fingerprint(pkg.compiled_form)
    return pkg


def check_compatibility(
    package: WorkflowPackage,
    *,
    current_compiler_version: str = WORKFLOW_COMPILER_VERSION,
) -> CompatibilityVerdict:
    """包 ↔ 编译器兼容性（确定性）：

    - compiler major 不同 → 不兼容（契约破坏，需 recompile）；
    - 包 schema_version minor > 当前 → 不兼容（新结构旧读者不识）；
    - 其余（patch 差异 / minor 更旧）→ 兼容。
    """
    cur = parse_semver(current_compiler_version)
    pkg = parse_semver(package.compiler_version)
    if cur is None or pkg is None:
        return CompatibilityVerdict(
            compatible=False, reason_code="MALFORMED_VERSION",
            detail=f"package={package.compiler_version!r} "
                   f"current={current_compiler_version!r}",
        )
    if pkg[0] != cur[0]:
        return CompatibilityVerdict(
            compatible=False, reason_code="COMPILER_MAJOR_MISMATCH",
            detail=f"package compiler {package.compiler_version} vs "
                   f"current {current_compiler_version}",
            migration_required=True,
            migration_hint="recompile_workflow：major 契约破坏，必须从源 "
                           "query/recipe 重新编译，不可原包重放。",
        )
    pkg_schema = parse_semver(package.schema_version)
    cur_schema = parse_semver(WORKFLOW_PACKAGE_SCHEMA_VERSION)
    if pkg_schema is not None and cur_schema is not None \
            and pkg_schema[:2] > cur_schema[:2]:
        return CompatibilityVerdict(
            compatible=False, reason_code="SCHEMA_NEWER",
            detail=f"package schema {package.schema_version} > "
                   f"current {WORKFLOW_PACKAGE_SCHEMA_VERSION}",
            migration_required=True,
            migration_hint="upgrade reader：包结构更新，旧编译器不能读。",
        )
    return CompatibilityVerdict(compatible=True, reason_code="OK")
