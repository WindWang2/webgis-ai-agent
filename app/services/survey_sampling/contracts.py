"""Survey sampling 域契约 — 词表、预算、typed 错误与状态模型。

公共条款（与 G39 同纪律）：
- 词表封闭：调查记录状态、参考证据类型、替换原因全部显式声明，
  绝不从字符串猜语义；
- 参考标签与预测类别**分离**：预测类来自框（地图类），参考类只能
  由调查记录产生；盲标模式下不回显预测类（默认盲标）；
- 记录 append-only：更正产生新版本，旧版本保留（争议可追溯）；
- 设计锁定后不可变：变更 = 新 survey（历史保留）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ── 调查记录状态词表（封闭；goal WP3） ───────────────────────────────

REC_NOT_VISITED = "not_visited"       # 未访问
REC_PENDING = "pending"               # 待核查（列入工作清单）
REC_LABELLED = "labelled"             # 已标注（有可用参考类）
REC_DISPUTED = "disputed"             # 争议（多判读不一致/待裁定）
REC_UNDETERMINED = "undetermined"     # 不可判（时相不符/无法判读）
REC_REFUSED = "refused"               # 拒绝（判读员拒标）

RECORD_STATUSES = frozenset({
    REC_NOT_VISITED, REC_PENDING, REC_LABELLED, REC_DISPUTED,
    REC_UNDETERMINED, REC_REFUSED,
})

#: 有可用参考类标签的状态（进入误差矩阵的候选）
USABLE_STATUSES = frozenset({REC_LABELLED})

#: 非响应/不可用状态（触发预抽替补顶替 + 原因披露）
NON_USABLE_STATUSES = frozenset({REC_NOT_VISITED, REC_PENDING,
                                 REC_UNDETERMINED, REC_REFUSED})

# ── 参考证据类型（封闭词表；隐私字段最小化） ─────────────────────────

EVIDENCE_FIELD_VISIT = "field_visit"            # 野外实地核查
EVIDENCE_HIGH_RES_IMAGERY = "high_res_imagery"  # 高分辨率影像判读
EVIDENCE_EXISTING_SURVEY = "existing_survey"    # 既有调查表导入
EVIDENCE_OTHER = "other"
EVIDENCE_TYPES = frozenset({EVIDENCE_FIELD_VISIT, EVIDENCE_HIGH_RES_IMAGERY,
                            EVIDENCE_EXISTING_SURVEY, EVIDENCE_OTHER})

#: 质量等级（封闭词表）
QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_LEVELS = frozenset({QUALITY_HIGH, QUALITY_MEDIUM, QUALITY_LOW})

# ── 替换/排除原因（披露词表） ────────────────────────────────────────

SUB_REASON_NOT_VISITED = "not_visited"
SUB_REASON_PENDING = "pending"
SUB_REASON_UNDETERMINED = "undetermined"
SUB_REASON_REFUSED = "refused"
SUB_REASONS = frozenset({SUB_REASON_NOT_VISITED, SUB_REASON_PENDING,
                         SUB_REASON_UNDETERMINED, SUB_REASON_REFUSED})

# ── 估计状态词表（goal §2 严格区分） ─────────────────────────────────

EST_SUPPORTED = "supported"
EST_PARTIAL = "partial"
EST_NOT_EVALUATED = "not_evaluated"
EST_STALE = "stale"

# ── 预算（先于分配 typed 阻断） ──────────────────────────────────────

MAX_STRATA = 64                     #: 层数上限（类别字典上限）
MAX_SAMPLES_PER_SURVEY = 200_000    #: 单调查样本+替补上限
MAX_RECORD_BATCH = 5_000            #: 单批导入记录上限
MAX_SURVEYS_PER_SCOPE = 200         #: 单 owner scope 调查数上限
MAX_NOTE_CHARS = 280                #: 备注长度（隐私最小化，不留自由长文）
MAX_INTERPRETER_CHARS = 64
MAX_CLASS_NAME_CHARS = 64

SURVEY_ERROR_CODES = frozenset({
    "INVALID_FRAME_REQUEST", "FRAME_NOT_FOUND", "INVALID_DESIGN",
    "DESIGN_NOT_FOUND", "SURVEY_NOT_FOUND", "SURVEY_REVISION_CONFLICT",
    "SAMPLES_ALREADY_DRAWN", "SAMPLE_NOT_FOUND", "RECORD_CONFLICT",
    "RECORD_INVALID", "SOURCE_DRIFTED", "STALE_ESTIMATE",
    "ESTIMATION_NOT_EVALUABLE", "BUDGET_EXCEEDED", "SOURCE_UNAVAILABLE",
    "CLASS_DICTIONARY_DRIFT", "SURVEY_FAILED", "UNAUTHORIZED",
})


class SurveySamplingError(Exception):
    """带稳定 code / 字段 / 修复指引的调查域 typed 错误（HTTP 与工具共用）。"""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        field: str = "",
        status_code: int = 422,
        details: Optional[Dict[str, Any]] = None,
        correction_hint: str = "",
    ) -> None:
        super().__init__(detail)
        self.code = code if code in SURVEY_ERROR_CODES else "SURVEY_FAILED"
        self.detail = detail
        self.field = field
        self.status_code = status_code
        self.details = details or {}
        self.correction_hint = correction_hint

    def to_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "code": self.code,
            "detail": self.detail,
        }
        if self.field:
            out["field"] = self.field
        if self.details:
            out["details"] = self.details
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        return out


# ── 持久模型（pydantic；store 序列化事实源） ─────────────────────────

class ReferenceRecord(BaseModel):
    """一条调查记录（append-only 版本；更正 = 新记录条目）。

    隐私最小化：不收姓名/联系方式/精确私宅坐标；备注 ≤280 字符；
    evidence_source 只存**类型化来源**（词表+简短描述），不存原始凭据。
    """

    survey_id: str
    sample_id: str
    stratum_id: str                     # 冗余存储（审计一致性校验用）
    status: str                         # RECORD_STATUSES
    reference_class: Optional[str] = None   # status=labelled 时必填
    version: int = 1                    # 同 sample_id 递增（旧版本保留）
    evidence_type: str = EVIDENCE_OTHER
    evidence_note: str = ""
    quality: str = QUALITY_MEDIUM
    interpreter: str = ""               # 判读员标识（可空）
    observed_at: str = ""               # ISO 日期（参考时相；空=未声明）
    note: str = ""
    actor: str = ""                     # 记录操作者（user|agent:<tool>）
    superseded_by: Optional[int] = None # 被更正时指向新版本号

    def model_validate_strict(self) -> None:
        if self.status not in RECORD_STATUSES:
            raise ValueError(f"status {self.status!r} outside closed vocabulary")
        if self.status == REC_LABELLED and not self.reference_class:
            raise ValueError("status=labelled requires reference_class")
        if self.status != REC_LABELLED and self.reference_class:
            raise ValueError(
                "reference_class only allowed with status=labelled")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"evidence_type {self.evidence_type!r} outside "
                             "closed vocabulary")
        if self.quality not in QUALITY_LEVELS:
            raise ValueError(f"quality {self.quality!r} outside closed vocabulary")
        if len(self.note) > MAX_NOTE_CHARS:
            raise ValueError(f"note exceeds {MAX_NOTE_CHARS} chars (privacy "
                             "minimization)")


class SurveySummary(BaseModel):
    """调查清单条目（列表面）。"""

    survey_id: str
    design_digest: str
    objective: str
    target_time: str
    frame_digest: str
    strata_count: int
    total_area: float
    samples_planned: int
    records_labelled: int = 0
    estimate_status: str = ""
    created_at: float = 0.0
