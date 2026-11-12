"""G17 staged 导入工作台 —— 领域状态与合同模型。

一个 ImportBatch 是用户一次拖入的一批文件；文件进入 staging 后不可变。
容器（GPKG/KML/ZIP/XLSX）被枚举为成员（member），用户选择成员并给出
解释（interpretation），解释 + 源身份确定一个有版本的计划（plan）；
preview 与 commit 使用同一解释器，提交按成员幂等并产出收据（receipt）。

设计合同（任务书 §产品合同 1-10 的直接编码）：
- 原件不可变：sources/ 下只写入一次；member_key 不依赖 UI 排序。
- 检查不等于提交：inspect/preview 不产生正式 ref（IngestPipeline 只在
  commit 调用）。
- 计划一致性：plan_hash 绑定 (source_sha256, member_key, interpretation,
  INTERPRETER_VERSION)；preview 后改配置，旧计划 hash 失配被拒绝。
- CRS 证据三态：source_metadata / declared / rfc7946_default / unknown，
  不存在"猜测即确认"。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# 解释器版本：解释语义变化（类型规则/几何构造/CRS 处理）时必须递增，
# 使旧 plan_hash 失配（任务书场景 12 的机制基础）。
INTERPRETER_VERSION = "g17-import-interpreter/1"

# 计划/合同 schema 版本（持久化状态向前兼容用）
BATCH_STATE_VERSION = 1

_BATCH_ID_RE = re.compile(r"^ib-[0-9a-f]{32}$")
_SAFE_STORED_RE = re.compile(r"[^A-Za-z0-9._\-]")


def new_batch_id() -> str:
    import uuid

    return f"ib-{uuid.uuid4().hex}"


def valid_batch_id(batch_id: str) -> bool:
    return bool(_BATCH_ID_RE.match(batch_id or ""))


def sanitize_stored_name(name: str) -> str:
    """原始文件名 → 可安全落盘的段（防穿越/控制字符；仅显示名保留原文）。"""
    cleaned = _SAFE_STORED_RE.sub("_", (name or "file").strip())[:120]
    return cleaned or "file"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BatchStatus(str, Enum):
    STAGED = "staged"                  # 文件已落盘，尚未枚举成员（预留；创建即枚举）
    INSPECTED = "inspected"            # 成员清单就绪
    CONFIGURED = "configured"          # 至少保存过一次解释计划
    PREVIEWED = "previewed"            # 至少一次有界预览完成
    COMMITTING = "committing"          # 提交进行中
    COMPLETED = "completed"            # 全部选定成员完成
    PARTIAL = "partial"                # 部分成员成功、部分失败
    FAILED = "failed"                  # 全部成员失败
    CANCELLED = "cancelled"            # 用户取消
    EXPIRED = "expired"                # staging 过期（数据可能已清理）


# 批次终态（不可再提交；failed/partial 成员可修正计划后重试 → 回到 configured）
BATCH_TERMINAL = {
    BatchStatus.COMPLETED,
    BatchStatus.PARTIAL,
    BatchStatus.FAILED,
    BatchStatus.CANCELLED,
    BatchStatus.EXPIRED,
}


class MemberStatus(str, Enum):
    DISCOVERED = "discovered"          # 已发现，未配置
    PLANNED = "planned"                # 已保存解释
    PREVIEWED = "previewed"            # 预览完成（绑定当前 plan_hash）
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 可重试成员状态：允许再次 commit
MEMBER_RETRYABLE = {MemberStatus.FAILED, MemberStatus.CANCELLED}


class ContainerKind(str, Enum):
    FILE = "file"        # 顶层单文件（geojson 等）
    GPKG = "gpkg"
    KML = "kml"
    ZIPSET = "zipset"    # zip 内一套 shapefile 组件（按 dir+basename 归组）
    SHEET = "sheet"      # xlsx 工作表


class MemberKind(str, Enum):
    VECTOR = "vector"    # 容器内矢量图层 / shapefile 集 / geojson 文件
    TABLE = "table"      # csv / xlsx sheet（可解释为点/WKT/纯表）


class OutputKind(str, Enum):
    FEATURE_COLLECTION = "feature_collection"
    TABLE = "table"


class CrsEvidence(BaseModel):
    """坐标声明的真实来源（合同 4：区分元数据/声明/RFC 默认，绝不猜测升级）。"""

    crs_source: str = "unknown"   # source_metadata | declared | rfc7946_default | unknown
    crs: Optional[str] = None     # 规范名（如 EPSG:32650）；unknown 时为 None
    declared_by_user: Optional[str] = None
    transform_target: Optional[str] = None  # 实际发生重投影时记录目标（EPSG:4326）


class SourceFile(BaseModel):
    source_id: str                       # src-<n>
    original_name: str                   # 展示用原名（不落盘路径）
    stored_name: str                     # sources/ 下安全文件名
    sha256: str
    size: int
    ext: str                             # .geojson/.gpkg/.kml/.zip/.csv/.xlsx
    upload_order: int                    # 用户提交顺序（仅审计，不参与 member_key 语义


class MemberRecord(BaseModel):
    member_key: str                      # 稳定标识（见模块 docstring；与 UI 无关）
    source_id: str
    container: ContainerKind
    kind: MemberKind
    display_name: str                    # 图层名 / basename / sheet 名
    # 可复现的源定位（绝不用 UI 排序/显示名解析）：
    # gpkg/kml → 图层名；sheet → 工作表名；zipset → 提取目录 token + basename
    container_ref: Optional[str] = None
    container_ref_detail: Optional[str] = None
    # 发现期元数据（metadata 读取，不整读数据）
    fields: List[str] = Field(default_factory=list)      # 有界（≤ budgets.MAX_COLUMNS_SHOWN）
    fields_total: Optional[int] = None                    # 真实字段总数（披露截断；评审 P2-1）
    geometry_type: Optional[str] = None
    feature_count: Optional[int] = None   # 未知时 None（诚实）
    crs: Optional[str] = None
    crs_source: str = "unknown"           # source_metadata / unknown（表格默认 unknown）
    size_bytes: Optional[int] = None      # 展开大小（zipset 组件合计）
    unsupported_reason: Optional[str] = None  # 有成员但当前不可导入的原因码
    hints: Dict[str, Any] = Field(default_factory=dict)  # 发现期建议（编码/分隔符/坐标列候选），非确认事实
    status: MemberStatus = MemberStatus.DISCOVERED
    selected: bool = False                # 用户勾选导入


class TableInterpretation(BaseModel):
    """csv / xlsx sheet 的解释参数（用户可配置项的合同面）。"""

    kind: MemberKind = MemberKind.TABLE
    mode: str = "auto"                    # auto | points | wkt | table
    encoding: Optional[str] = None        # None=探测回退链；显式值不再回退
    delimiter: Optional[str] = None       # None=嗅探；单字符
    header_row: int = 0                   # 0 基表头行；之前行跳过
    x_column: Optional[str] = None
    y_column: Optional[str] = None
    wkt_column: Optional[str] = None
    source_crs_declared: Optional[str] = None
    field_types: Dict[str, str] = Field(default_factory=dict)  # col -> text|number|boolean|date
    keep_fields: Optional[List[str]] = None                   # None=全部


class VectorInterpretation(BaseModel):
    """矢量成员（GPKG/KML 图层、zipset、geojson 文件）的解释参数。"""

    kind: MemberKind = MemberKind.VECTOR
    source_crs_declared: Optional[str] = None  # 仅当源缺 CRS 时生效
    keep_fields: Optional[List[str]] = None


INTERPRETATION_FIELD_TYPES = {"text", "number", "boolean", "date"}
INTERPRETATION_MODES = {"auto", "points", "wkt", "table"}


class CommitReceipt(BaseModel):
    """按成员的提交收据（合同 8：可验证的成功）。"""

    member_key: str
    plan_hash: str
    status: str                          # completed | failed | cancelled
    output_kind: Optional[str] = None
    output_ref: Optional[str] = None     # ref:upload-… / ref:table-…
    output_name: str = ""
    input_rows: Optional[int] = None
    output_rows: Optional[int] = None
    rejected_rows: Optional[int] = None
    geometry_type: Optional[str] = None
    crs_evidence: Optional[CrsEvidence] = None
    duplicate: bool = False              # 幂等复用既有产物
    quality_status: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    diagnostics_ref: Optional[str] = None  # 拒绝行明细产物路径（diag/ 下相对路径）
    source_identity: str = "none"        # none | source_id_field:<name>
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_ms: Optional[int] = None


class ImportBatchState(BaseModel):
    """batch.json —— staging 状态唯一事实源（原子写）。"""

    state_version: int = BATCH_STATE_VERSION
    batch_id: str
    session_id: str                      # 已验证归属的会话（权限根）
    user_fingerprint: str = ""           # 审计辅助（user_id/匿名指纹），非授权依据
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    expires_at: str = ""
    status: BatchStatus = BatchStatus.STAGED
    rev: int = 0                         # 乐观并发版本（每次保存递增）
    sources: List[SourceFile] = Field(default_factory=list)
    members: List[MemberRecord] = Field(default_factory=list)
    plan_version: int = 0
    interpretations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    plan_hashes: Dict[str, str] = Field(default_factory=dict)
    previewed_hashes: Dict[str, str] = Field(default_factory=dict)  # member -> 预览时的 hash
    receipts: Dict[str, CommitReceipt] = Field(default_factory=dict)
    diagnostics: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    license: str = ""                     # 与旧上传 form 对齐（可空=unknown）
    attribution: str = ""
    committing_since: Optional[str] = None
    last_commit_started_at: Optional[str] = None
    cancel_requested: bool = False            # 取消意图（评审 P2-9：锁窗口期也生效）
    #: G23：本次提交对应的 durable job（analysis_tasks.id）。活跃 job 是
    #: 「另一副本可能正在推进」的跨进程在飞证据；None = 旧格式批次（G23 之前）
    #: 或从未提交。恢复判据见 service._load_owned。
    active_job_id: Optional[int] = None


def canonical_interpretation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """解释参数 → canonical dict（排序键、去默认空值），供 hash 与持久化。

    只保留合同字段，未知键丢弃（版本拒绝策略在 schema 层做）。
    """
    kind = payload.get("kind", "table")
    base: Dict[str, Any] = {"kind": kind}
    if kind == "vector":
        keys = ("source_crs_declared", "keep_fields")
    else:
        keys = (
            "mode", "encoding", "delimiter", "header_row",
            "x_column", "y_column", "wkt_column",
            "source_crs_declared", "field_types", "keep_fields",
        )
    for k in keys:
        v = payload.get(k)
        if v is None or v == "" or v == {}:
            continue
        if k == "field_types":
            v = {str(c): str(t) for c, t in sorted(v.items())}
        elif k == "keep_fields":
            v = sorted({str(f) for f in v})
        elif k == "header_row":
            v = int(v)
        else:
            v = str(v) if k not in ("header_row",) else v
        base[k] = v
    return base


def compute_plan_hash(
    source_sha256: str, member_key: str, interpretation: Dict[str, Any]
) -> str:
    """(源身份, 成员, canonical 解释, 解释器版本) → sha256 前 16 位。

    同一源文件的不同解释（不同 X/Y、不同 CRS、不同类型）得到不同 hash，
    因此不同计划产生不同产物、不被 bytes 去重误合并（场景 13）；
    解释配置变更使旧 hash 失配（场景 12）。
    """
    blob = json.dumps(
        {
            "source_sha256": source_sha256,
            "member_key": member_key,
            "interpretation": canonical_interpretation(interpretation),
            "interpreter_version": INTERPRETER_VERSION,
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def member_status_value(m: MemberRecord) -> str:
    return m.status.value if isinstance(m.status, MemberStatus) else str(m.status)
