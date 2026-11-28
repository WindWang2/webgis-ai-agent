"""发布快照契约（publication_snapshot.v1）——WP1。

快照是发布后不可变的事实源：冻结 MapSpec 文档 + 公开字段白名单 + 许可/
署名/目的 + 可访问性清单 + QA 摘要。作者编辑原图后旧版本不变，新版本
显式发布（product_id 稳定、version 递增）。

红线（``FORBIDDEN_SNAPSHOT_KEYS`` 负向扫描强制）：快照不得包含会话完整
历史、私有 ref 载荷（ref 数据只允许进入物化产物文件，且经白名单投影）、
owner_token、provider key、用户笔记、内部绝对路径。
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: 契约版本。破坏性变更必须 bump 并提供迁移说明。
PUBLICATION_SNAPSHOT_SPEC_VERSION = "publication_snapshot.v1"

#: 快照指纹前缀（同 PublicationIR 的 pubir-sha256 惯例）。
SNAPSHOT_FINGERPRINT_PREFIX = "pub-snapshot-sha256:"


class AccessPolicy(str, Enum):
    """访问策略。``private`` 默认；``link`` = 可撤销只读链接。"""

    private = "private"
    link = "link"


class PublicationStatus(str, Enum):
    """生命周期：draft → published → revoked（终态可见）。"""

    draft = "draft"
    published = "published"
    revoked = "revoked"


class DataMode(str, Enum):
    """层数据的发布形态。``materialized`` = 物化快照；其余为 typed 阻断。"""

    materialized = "materialized"
    remote_projection = "remote_projection"


class PublicationBlockerReason(str, Enum):
    """typed 阻断词表（preview/publish/reader 共用，不造第二套词）。"""

    SOURCE_NOT_AUTHORIZABLE = "source_not_authorizable"
    SOURCE_DATA_UNAVAILABLE = "source_data_unavailable"
    FIELD_WHITELIST_EMPTY = "field_whitelist_empty"
    SNAPSHOT_BUDGET_EXCEEDED = "snapshot_budget_exceeded"
    LICENSE_UNKNOWN = "license_unknown"
    MAPSPEC_INVALID = "mapspec_invalid"
    PUBLICATION_NOT_FOUND = "publication_not_found"
    PUBLICATION_REVOKED = "publication_revoked"
    PUBLICATION_EXPIRED = "publication_expired"
    PUBLICATION_NOT_PUBLISHED = "publication_not_published"
    ACCESS_DENIED = "access_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_INVALID = "confirmation_invalid"
    LAYER_NOT_ALLOWED = "layer_not_allowed"
    FIELD_NOT_ALLOWED = "field_not_allowed"


class PublicationBlocker(BaseModel):
    """一条 typed 阻断/降级记录（真实理由，不静默吞）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: PublicationBlockerReason
    message: str = Field(min_length=1, max_length=500)
    layer_id: Optional[str] = Field(default=None, max_length=200)
    source_id: Optional[str] = Field(default=None, max_length=200)


class PublicationSourceInfo(BaseModel):
    """发布面 source 摘要（不含任何凭证/会话路径）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    attribution: str = Field(default="", max_length=300)
    license: str = Field(default="", max_length=120)
    data_mode: DataMode


class PublicationLayerInfo(BaseModel):
    """发布面 layer 摘要：读者可见的层清单与字段白名单。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    source_id: str = Field(default="", max_length=200)
    #: 读者可查询/弹窗可见的字段白名单（有序；空 = 该层无属性查询）。
    field_whitelist: Tuple[str, ...] = Field(default=())
    feature_count: int = Field(default=0, ge=0)
    data_mode: DataMode = DataMode.materialized
    #: 该层允许读者显隐（默认 True；作者可关）。
    reader_visibility_allowed: bool = True


class PublicationScope(BaseModel):
    """范围语义：时间/CRS/空间范围必须随结果（真实科学语义）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    crs: str = Field(default="EPSG:3857", max_length=80)
    bbox: Optional[Tuple[float, float, float, float]] = None
    time_start: Optional[str] = Field(default=None, max_length=64)
    time_end: Optional[str] = Field(default=None, max_length=64)
    #: 几何精度（发布投影钳制的小数位）。
    geometry_precision: int = Field(default=6, ge=0, le=12)


class PublicationQaSummary(BaseModel):
    """预览/发布 QA 摘要：checked=false 的项如实存在。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_validity: str = Field(default="not_evaluated", max_length=40)
    compile_ok: Optional[bool] = None
    checks: Tuple[Tuple[str, str], ...] = Field(default=())  # (name, status)


class PublicationMeta(BaseModel):
    """产品语义元数据：目的/署名/许可/缺失值说明。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(default="", max_length=600)
    attribution: str = Field(default="", max_length=300)
    license: str = Field(default="", max_length=120)
    missing_values_note: str = Field(default="", max_length=300)


class PublicationSnapshotDocument(BaseModel):
    """``publication_snapshot.v1`` —— 发布后不可变的读者事实源。

    ``frozen spec``（完整 MapSpec 文档）放在 ``spec`` 字段：读者 map 端点
    返回它的**授权投影**（source 重写为读者数据端点、剥编辑面字段），
    原样 spec 永不直接下发。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: str = PUBLICATION_SNAPSHOT_SPEC_VERSION
    product_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    meta: PublicationMeta
    scope: PublicationScope = Field(default_factory=PublicationScope)
    sources: Tuple[PublicationSourceInfo, ...] = Field(default=())
    layers: Tuple[PublicationLayerInfo, ...] = Field(default=())
    #: 未纳入发布的层/源及真实理由（读者端同样可见，诚实呈现）。
    blockers: Tuple[PublicationBlocker, ...] = Field(default=())
    accessibility: Dict[str, Any] = Field(default_factory=dict)
    qa: PublicationQaSummary = Field(default_factory=PublicationQaSummary)
    #: 冻结 MapSpec 文档（编辑面投影见 reader.projection；此处为发布时刻原样）。
    spec: Dict[str, Any] = Field(default_factory=dict)
    #: 快照自身指纹（canonical digest，含 spec）。
    snapshot_fingerprint: str = Field(default="", max_length=120)

    def reader_allowed_layers(self) -> List[PublicationLayerInfo]:
        return [lyr for lyr in self.layers if lyr.data_mode == DataMode.materialized]

    def layer_by_id(self, layer_id: str) -> Optional[PublicationLayerInfo]:
        for lyr in self.layers:
            if lyr.layer_id == layer_id:
                return lyr
        return None

    def field_allowed(self, layer_id: str, field: str) -> bool:
        lyr = self.layer_by_id(layer_id)
        if lyr is None:
            return False
        return field in lyr.field_whitelist


# ── 红线：快照禁带内容（负向扫描） ─────────────────────────────────────

#: 键名级禁带（大小写不敏感；出现在冻结 spec 或任何嵌套 dict 键即违例）。
FORBIDDEN_SNAPSHOT_KEYS: Tuple[str, ...] = (
    "owner_token",
    "ownertoken",
    "x-session-token",
    "sessiontoken",
    "api_key",
    "apikey",
    "provider_key",
    "secret",
    "password",
    "authorization",
    "chat_history",
    "messages_history",
    "user_notes",
)

#: 绝对路径形态（内部部署路径不得进入读者面；含 file:// 变体与 Windows 盘符）。
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\"'\s:\[\(])/(?:home|Users|root|var|tmp|opt|srv|data)/[A-Za-z0-9_./-]+"
    r"|file:///[A-Za-z0-9_./-]+"
    r"|[A-Za-z]:\\(?:[A-Za-z0-9_.\\-]+)"
)


def _normalize_key(key: Any) -> str:
    """键名归一：小写并剥 ``_``/``-``（``X-Session-Token`` ≡ ``session_token``）。"""
    return re.sub(r"[_\-\s]+", "", str(key)).lower()


#: 词表归一一次（模块加载时），扫描按同一归一比较。
_FORBIDDEN_NORMALIZED = frozenset(_normalize_key(k) for k in FORBIDDEN_SNAPSHOT_KEYS)


def scan_forbidden_content(node: Any, path: str = "") -> List[str]:
    """递归扫描快照树，返回违例路径清单（键名红线 + 绝对路径形态）。

    ``messages`` 键单列：冻结 spec 不含会话历史；若出现即违例。
    """
    found: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_l = _normalize_key(key)
            child_path = f"{path}.{key}" if path else str(key)
            if key_l in _FORBIDDEN_NORMALIZED:
                found.append(child_path)
            elif key_l == "messages":
                found.append(child_path)
            found.extend(scan_forbidden_content(value, child_path))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            found.extend(scan_forbidden_content(item, f"{path}[{idx}]"))
    elif isinstance(node, str):
        if _ABSOLUTE_PATH_RE.search(node):
            found.append(path or "<root>")
    return found


def compute_snapshot_fingerprint(doc: Dict[str, Any]) -> str:
    """canonical digest（与 ``plan_ir.digest_of`` 同算法）。"""
    from app.lib.cartography.plan_ir import digest_of

    return f"{SNAPSHOT_FINGERPRINT_PREFIX}{digest_of(doc)}"


def build_snapshot_document(
    *,
    product_id: str,
    version: int,
    meta: PublicationMeta,
    spec: Dict[str, Any],
    layers: List[PublicationLayerInfo],
    sources: List[PublicationSourceInfo],
    scope: PublicationScope,
    accessibility: Dict[str, Any],
    qa: PublicationQaSummary,
    blockers: List[PublicationBlocker],
) -> Tuple[PublicationSnapshotDocument, str]:
    """构造快照并计算指纹；违例红线直接抛 ``ValueError``（fail-loud）。"""
    document = PublicationSnapshotDocument(
        spec_version=PUBLICATION_SNAPSHOT_SPEC_VERSION,
        product_id=product_id,
        version=version,
        meta=meta,
        scope=scope,
        sources=tuple(sources),
        layers=tuple(layers),
        blockers=tuple(blockers),
        accessibility=accessibility,
        qa=qa,
        spec=spec,
    )
    payload = document.model_dump(mode="json")
    fingerprint = compute_snapshot_fingerprint(payload)
    final = PublicationSnapshotDocument(
        **{**payload, "snapshot_fingerprint": fingerprint}
    )
    violations = scan_forbidden_content(final.model_dump(mode="json"))
    if violations:
        raise ValueError(
            "publication_snapshot_forbidden_content: " + ", ".join(violations[:8])
        )
    return final, fingerprint


__all__ = [
    "PUBLICATION_SNAPSHOT_SPEC_VERSION",
    "SNAPSHOT_FINGERPRINT_PREFIX",
    "AccessPolicy",
    "PublicationStatus",
    "DataMode",
    "PublicationBlockerReason",
    "PublicationBlocker",
    "PublicationSourceInfo",
    "PublicationLayerInfo",
    "PublicationScope",
    "PublicationQaSummary",
    "PublicationMeta",
    "PublicationSnapshotDocument",
    "FORBIDDEN_SNAPSHOT_KEYS",
    "scan_forbidden_content",
    "compute_snapshot_fingerprint",
    "build_snapshot_document",
]
