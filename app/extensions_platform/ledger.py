"""投影台账（ADR-0104 / Wave 2）。

扩展激活期间对核心权威 registry 的每一次写入都记为一条可撤销记录。
激活失败 / 停用时按注册逆序回放 undo——这是「unload 不留僵尸条目」与
「激活原子性」的唯一机制。核心 registry 自身不感知扩展；平台侧通过
additive 的 ``unregister_*`` 方法完成回滚。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .diagnostics import DiagnosticCode, ExtensionDiagnostic

logger = logging.getLogger(__name__)

# 投影类别（诊断 / CLI 呈现用；词表封闭）。
PROJECTION_KINDS = frozenset({"tool", "algorithm", "data_provider", "cartography", "workflow_recipe"})


@dataclass(frozen=True)
class ProjectionRecord:
    kind: str
    projected_id: str
    undo: Callable[[], bool]
    extension_id: str


@dataclass
class ProjectionLedger:
    extension_id: str
    records: list[ProjectionRecord] = field(default_factory=list)

    def record(self, kind: str, projected_id: str, undo: Callable[[], bool]) -> None:
        if kind not in PROJECTION_KINDS:
            raise ValueError(f"unknown projection kind {kind!r}")
        self.records.append(
            ProjectionRecord(kind=kind, projected_id=projected_id, undo=undo, extension_id=self.extension_id)
        )

    def rollback(self) -> list[ExtensionDiagnostic]:
        """逆序回放全部 undo。单个 undo 失败不阻断其余回滚（尽力清场）。"""
        diagnostics: list[ExtensionDiagnostic] = []
        for rec in reversed(self.records):
            try:
                ok = rec.undo()
            except Exception as exc:  # noqa: BLE001 - 回滚必须继续
                ok = False
                logger.warning("extension %s undo of %s(%s) raised: %s",
                               self.extension_id, rec.kind, rec.projected_id, exc)
            if not ok:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_ROLLBACK_INCOMPLETE,
                        f"failed to unregister {rec.kind} {rec.projected_id!r}",
                        extension_id=self.extension_id,
                        kind=rec.kind,
                        projected_id=rec.projected_id,
                    )
                )
        self.records.clear()
        return diagnostics
