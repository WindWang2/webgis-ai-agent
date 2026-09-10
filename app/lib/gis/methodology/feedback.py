"""Methodology Feedback —— 方法选择反馈契约（Epic 11 §5.L）。

记录方法选择的运行时结果（selected/rejected、失败、渲染诊断、用户
纠偏、workflow 成败、回退选择），只进**离线/审阅式 improvement
corpus**——运行时不存在任何未经审查改写 authoritative 知识表的路径
（测试锁定：writer 只写 JSONL；无 reader 回灌知识表的 API）。

边界（架构 §3.8 冻结）：

- 默认禁用：``FEEDBACK_SINK_PATH`` 为空时 writer 是 no-op；
- 单文件硬上限 ``_MAX_RECORDS``（10000 条），达到后停止写入并产出
  truncation 披露事件（不静默丢、也不无限增长）；
- 隔离：每条记录携带 project_id/session_id（tenant 隔离字段）；
  读取（审阅）按 project 过滤是离线脚本职责，本模块只追加；
- 记录不含用户个人数据——只有方法决策与机器诊断。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

#: feedback schema 版本。
FEEDBACK_SCHEMA_VERSION = 1

#: 单文件硬上限（达到后停止写入 + truncation 披露；架构 R1-F12）。
MAX_RECORDS = 10000

#: 环境变量名（默认空 = 禁用）。
FEEDBACK_SINK_ENV = "METHODOLOGY_FEEDBACK_SINK_PATH"

#: 反馈记录类型词表（封闭）。
FEEDBACK_KINDS = (
    "method_selected",
    "method_rejected",
    "method_abstained",
    "runtime_failure",
    "render_diagnostic",
    "user_correction",
    "workflow_outcome",
    "fallback_used",
)


class MethodologyFeedbackRecord(BaseModel):
    """一条方法选择反馈（离线审阅语料；运行时只追加不回灌）。"""

    kind: str                          # ⊆ FEEDBACK_KINDS
    project_id: str = ""               # tenant 隔离
    session_id: str = ""
    query: str = ""                    # 有界截断
    category_id: str = ""
    family_id: str = ""
    method_id: str = ""
    alternative_method_id: str = ""    # user_correction：改为
    outcome: str = ""                  # success/failure/partial/…
    reason_codes: List[str] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    #: 知识链指纹（记录裁决时点的知识版本，审计可回放）
    knowledge_fingerprint: str = ""

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in FEEDBACK_KINDS:
            raise ValueError(f"unknown feedback kind: {v}")
        return v

    @field_validator("query")
    @classmethod
    def _bounded_query(cls, v: str) -> str:
        return v[:200]

    @field_validator("method_id", "alternative_method_id", "family_id",
                     "category_id")
    @classmethod
    def _bounded_ids(cls, v: str) -> str:
        return v[:64]

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "project_id": self.project_id[:48],
            "method_id": self.method_id[:64],
            "outcome": self.outcome[:24],
            "reason_codes": [c[:64] for c in self.reason_codes[:4]],
        }


class FeedbackCorpusWriter:
    """JSONL 追加 writer（默认禁用；硬上限；无回灌路径）。"""

    def __init__(self, sink_path: str = "") -> None:
        self._sink_path = sink_path or os.environ.get(FEEDBACK_SINK_ENV, "")
        self._truncated = False

    @property
    def enabled(self) -> bool:
        return bool(self._sink_path) and not self._truncated

    @property
    def truncated(self) -> bool:
        """达到硬上限后 True（披露事件；继续调用为 no-op）。"""
        return self._truncated

    def record(self, feedback: MethodologyFeedbackRecord) -> bool:
        """追加一条记录（禁用/超限 = no-op；写失败不抛——反馈不阻断主流程）。"""
        if not self.enabled:
            return False
        try:
            count = self._count_lines()
            if count >= MAX_RECORDS:
                self._truncated = True
                return False
            payload = {
                "schema_version": FEEDBACK_SCHEMA_VERSION,
                **feedback.model_dump(mode="json"),
            }
            with open(self._sink_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
            return True
        except OSError:
            return False

    def _count_lines(self) -> int:
        if not os.path.exists(self._sink_path):
            return 0
        with open(self._sink_path, "rb") as fh:
            return sum(1 for _ in fh)


__all__ = [
    "FEEDBACK_SCHEMA_VERSION",
    "FEEDBACK_KINDS",
    "MAX_RECORDS",
    "FEEDBACK_SINK_ENV",
    "MethodologyFeedbackRecord",
    "FeedbackCorpusWriter",
]
