"""Project Exchange 合同 —— 格式门、硬帽、分类词汇表与 manifest 模型。

单点真相（与 data_delivery.contracts 同款纪律）：
- 包格式门 ``{"format": "webgis-project-package", "format_version": 1}``；
- 硬帽是**服务端强制边界**（导入侧拒绝、导出侧预算），不是建议值；
- 分类/复现分级是有界封闭词表，manifest、REST、Pi 工具、前端同源消费；
- manifest 模型全部有界（条目数/字符串长度/深度），导入侧 extra="forbid"。

禁止入包：绝对路径、凭据/密钥、签名 URL、owner_token、原 owner/org 授权
（manifest.origin 仅作溯源展示，导入端绝不沿用）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── 格式门 ────────────────────────────────────────────────────────────────

#: 包格式标识（manifest.format）。与 model_authoring 的 "webgis-model"/"1"
#: 同款 format/version 门范式。
PACKAGE_FORMAT = "webgis-project-package"

#: 当前唯一受支持的 manifest schema 版本。
PACKAGE_FORMAT_VERSION = 1

#: 包容器 MIME（本质是 ZIP；下载路由 Content-Type）。
PACKAGE_MIME = "application/zip"

#: 下载文件名后缀（.zip 是诚实容器语义；前缀是产品辨识）。
PACKAGE_FILENAME_SUFFIX = ".wgproject.zip"

# ── 分类与分级（封闭词表）───────────────────────────────────────────────

#: 依赖项分类：引用绝不冒充已打包文件（任务书 WP1 硬性）。
CLASS_EMBEDDED = "embedded"                    # 载荷在包内且 sha256 可校验
CLASS_EXTERNAL_REFERENCE = "external_reference"  # 不在包内；URI+义务披露
CLASS_REDACTED = "redacted"                    # 许可/授权不允许携带
CLASS_MISSING = "missing"                      # 源 detached/会话过期/不可解析
CLASS_UNSUPPORTED = "unsupported"              # 本系统能力外（云模型/外部服务任务）
CLASSIFICATIONS: tuple = (
    CLASS_EMBEDDED, CLASS_EXTERNAL_REFERENCE, CLASS_REDACTED,
    CLASS_MISSING, CLASS_UNSUPPORTED,
)

#: 整包复现分级（inspect/export 报告与导入计划共用）。
REPRO_FULL_OFFLINE = "full_offline_recompute"  # 数据全 embedded、工作流可运行
REPRO_VIEW_ONLY = "view_only"                  # 数据/地图可重开查看，重算不可能
REPRO_PARTIAL = "partial"                      # 有 embedded 也有外部/缺失
REPRO_LEVELS: tuple = (REPRO_FULL_OFFLINE, REPRO_VIEW_ONLY, REPRO_PARTIAL)

#: fidelity 校验结论（复现证据词汇表；not_evaluated 是诚实态不是失败态）。
FIDELITY_EXACT = "exact"
FIDELITY_SEMANTIC = "semantic_equivalent"
FIDELITY_PARTIAL = "partial"
FIDELITY_NOT_EVALUATED = "not_evaluated"

# ── 选择范围 ─────────────────────────────────────────────────────────────

#: 打包范围：整项目 / 指定地图（含其数据闭包）/ 指定分析（工作流闭包）。
SCOPE_PROJECT = "project"
SCOPE_MAP = "map"
SCOPE_ANALYSIS = "analysis"
SCOPES: tuple = (SCOPE_PROJECT, SCOPE_MAP, SCOPE_ANALYSIS)

# ── 硬帽（服务端强制；导出=预算，导入=拒绝）────────────────────────────

#: manifest.json 自身上限。
MAX_MANIFEST_BYTES = 1 * 1024 * 1024

#: 包内条目数上限（含目录条目；导入侧拒、导出侧守）。
MAX_PACKAGE_ENTRIES = 2000

#: 包总展开字节默认预算（可经 settings.PROJECT_EXCHANGE_MAX_PACKAGE_BYTES 覆盖）。
DEFAULT_MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024

#: 单条目展开字节上限。
MAX_ENTRY_BYTES = 512 * 1024 * 1024

#: 单条目压缩比上限（超过 = ZIP bomb 嫌疑；对已是压缩格式的条目同样适用，
#: 因为我们只放行白名单扩展且逐条 sha256 对账 manifest）。
MAX_COMPRESSION_RATIO = 200

#: manifest 内单字符串字段上限（防 manifest 本身成为炸弹载体）。
MAX_MANIFEST_STR = 4096

#: 单数据集 feature 数硬帽（与 data_delivery MAX_ROWS_PER_DELIVERY 对齐）。
MAX_FEATURES_PER_DATASET = 200_000

#: 逻辑 ID 列表硬帽。
MAX_LOGICAL_IDS = 500

#: 选择集里引用 ID 的条目上限。
MAX_SELECTION_IDS = 100

#: 包内扩展名白名单（导入侧唯一放行面；.zip/.tar 等嵌套压缩包显式拒绝）。
ALLOWED_ENTRY_EXTENSIONS: tuple = (
    ".json", ".geojson", ".gpkg", ".tif", ".tiff", ".csv",
    ".parquet", ".md", ".fgb", ".png",
)

#: manifest 之外包内固定路径词表（导入侧布局校验）。
MANIFEST_PATH = "manifest.json"
DISCLOSURE_PATH = "DISCLOSURE.md"
LAYERS_GPKG_PATH = "standard/layers.gpkg"
RASTERS_DIR = "standard/rasters/"
TABLES_DIR = "standard/tables/"
ARTIFACTS_DIR = "artifacts/"
MAPS_DIR = "maps/"
STYLES_DIR = "styles/"

# ── 错误码词汇表（typed；REST/Pi 工具/回执共用）─────────────────────────

ERR_PACKAGE_FORMAT = "PACKAGE_FORMAT_UNSUPPORTED"   # format/version 门拒绝
ERR_MANIFEST_INVALID = "MANIFEST_INVALID"           # schema 校验失败
ERR_MANIFEST_TOO_LARGE = "MANIFEST_TOO_LARGE"
ERR_ENTRY_LIMIT = "ENTRY_LIMIT_EXCEEDED"
ERR_SIZE_BUDGET = "SIZE_BUDGET_EXCEEDED"
ERR_COMPRESSION_RATIO = "COMPRESSION_RATIO_EXCEEDED"
ERR_PATH_UNSAFE = "PATH_UNSAFE"                     # 绝对路径/穿越/符号链接/重复
ERR_ENTRY_TYPE = "ENTRY_TYPE_REJECTED"              # 扩展名白名单外
ERR_HASH_MISMATCH = "HASH_MISMATCH"                 # sha256 对账不符
ERR_PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
ERR_UNAUTHORIZED = "UNAUTHORIZED"
ERR_EMPTY_SELECTION = "EMPTY_SELECTION"             # 闭包为空（无可打包内容）
ERR_PAYLOAD_UNAVAILABLE = "PAYLOAD_UNAVAILABLE"     # 会话过期/上传缺失
ERR_IMPORT_SOURCE_INVALID = "IMPORT_SOURCE_INVALID"
ERR_CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
ERR_LICENSE_BLOCKED = "LICENSE_BLOCKED"             # 许可不允许导入方分发/使用
ERR_WRITE_FAILED = "WRITE_FAILED"
ERR_VERIFY_FAILED = "VERIFY_FAILED"
ERR_CANCELLED = "CANCELLED"
ERR_JOB_NOT_FOUND = "JOB_NOT_FOUND"

#: fatal 安全拒绝码（不可部分导入；缺一个就整包拒）。
FATAL_IMPORT_ERRORS = frozenset({
    ERR_PACKAGE_FORMAT, ERR_MANIFEST_INVALID, ERR_MANIFEST_TOO_LARGE,
    ERR_ENTRY_LIMIT, ERR_SIZE_BUDGET, ERR_COMPRESSION_RATIO,
    ERR_PATH_UNSAFE, ERR_ENTRY_TYPE, ERR_HASH_MISMATCH,
})


class PackageError(Exception):
    """typed 包错误（error_code + reason；同 DeliveryError 范式）。"""

    def __init__(self, error_code: str, reason: str) -> None:
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.reason = reason


# ── 逻辑 ID 工具 ──────────────────────────────────────────────────────────

_SAFE_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_HEX_RE = re.compile(r"^[0-9a-f]{1,64}$")


def slugify(name: str, default: str = "item", max_len: int = 48) -> str:
    """稳定逻辑 ID 主干：小写 ASCII 白名单 + 长度上限（同 safe_filename 纪律）。"""
    stem = (name or "").strip().lower()
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = _SAFE_SLUG_RE.sub("-", stem).strip("-._")
    return (stem[:max_len] or default)


def valid_logical_id(value: str) -> bool:
    """导入侧逻辑 ID 白名单：``<kind>-<slug>``，kind 有界。"""
    if not isinstance(value, str) or len(value) > 64 or len(value) < 4:
        return False
    kind, sep, rest = value.partition("-")
    if not sep or kind not in ("ds", "wf", "art", "map"):
        return False
    if not rest or _SAFE_SLUG_RE.search(rest.replace("-", "x")) or ".." in rest:
        # rest 已限定 [a-z0-9._-]；再拒绝整体十六进制以外的伪装前缀路径
        return False
    if rest.startswith(".") or rest.endswith("."):
        return False
    return True


def is_hex_digest(value: str) -> bool:
    """sha256 十六进制摘要形态校验。"""
    return bool(isinstance(value, str) and _HEX_RE.fullmatch(value))


def new_package_id() -> str:
    import uuid

    return f"pkg-{uuid.uuid4().hex}"


# ── manifest 模型（有界；导入侧 strict）─────────────────────────────────

_ManifestStrict = ConfigDict(extra="forbid", validate_assignment=True)


class OriginInfo(BaseModel):
    """溯源信息（导入端禁止沿用任何授权字段）。"""

    model_config = _ManifestStrict

    system: str = Field(default="webgis-ai-agent", max_length=64)
    project_id: str = Field(default="", max_length=255)
    project_name: str = Field(default="", max_length=255)
    app_version_hint: str = Field(default="", max_length=64)


class LicenseInfo(BaseModel):
    """许可声明：distributable=None = 未知（诚实三态，不虚构）。"""

    model_config = _ManifestStrict

    spdx: Optional[str] = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=MAX_MANIFEST_STR)
    distributable: Optional[bool] = None


class DatasetPayloadRef(BaseModel):
    """包内载荷引用（gpkg 图层成员或独立文件）。"""

    model_config = _ManifestStrict

    path: str = Field(max_length=256)
    member: Optional[str] = Field(default=None, max_length=64)  # gpkg 图层名
    sha256: str = Field(max_length=64)
    bytes: int = Field(ge=0)


class ExternalRef(BaseModel):
    model_config = _ManifestStrict

    uri: str = Field(max_length=MAX_MANIFEST_STR)
    reason: str = Field(default="", max_length=MAX_MANIFEST_STR)


class ManifestDataset(BaseModel):
    model_config = _ManifestStrict

    logical_id: str = Field(max_length=64)
    name: str = Field(max_length=255)
    classification: Literal[
        "embedded", "external_reference", "redacted", "missing", "unsupported",
    ]
    source_type: str = Field(default="", max_length=64)
    crs: str = Field(default="", max_length=100)
    geometry_type: str = Field(default="", max_length=64)
    feature_count: Optional[int] = None
    bbox: Optional[List[float]] = None
    schema_profile: Dict[str, Any] = Field(default_factory=dict)
    version_fingerprint: str = Field(default="", max_length=64)
    license: LicenseInfo = Field(default_factory=LicenseInfo)
    payload: Optional[DatasetPayloadRef] = None
    external: Optional[ExternalRef] = None
    original_ref: str = Field(default="", max_length=512)  # 溯源；导入重映射依据
    origin_id: str = Field(default="", max_length=255)  # 源系统真实 ID（导入图内重映射）
    detached_at: Optional[str] = Field(default=None, max_length=64)

    @field_validator("bbox")
    @classmethod
    def _bbox4(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None and len(v) != 4:
            raise ValueError("bbox 必须是 [w, s, e, n] 四元组")
        return v


class WorkflowInputBinding(BaseModel):
    model_config = _ManifestStrict

    kind: Literal["dataset", "artifact", "value", "map"]
    logical_id: str = Field(default="", max_length=64)


class ManifestWorkflow(BaseModel):
    model_config = _ManifestStrict

    logical_id: str = Field(max_length=64)
    name: str = Field(max_length=255)
    version: int = Field(ge=1)
    graph_fingerprint: str = Field(default="", max_length=64)
    graph_spec: Dict[str, Any] = Field(default_factory=dict)
    graph_spec_path: Optional[str] = Field(default=None, max_length=256)
    inputs_schema: Dict[str, Any] = Field(default_factory=dict)
    input_bindings: Dict[str, WorkflowInputBinding] = Field(default_factory=dict)
    runnable: bool = False
    not_runnable_reason: str = Field(default="", max_length=MAX_MANIFEST_STR)


class LineageEdge(BaseModel):
    model_config = _ManifestStrict

    parents: List[str] = Field(default_factory=list, max_length=64)
    producing_tool: str = Field(default="", max_length=128)
    tool_version: str = Field(default="", max_length=64)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    workflow: Optional[str] = Field(default=None, max_length=64)


class ManifestArtifact(BaseModel):
    model_config = _ManifestStrict

    logical_id: str = Field(max_length=64)
    artifact_type: str = Field(max_length=64)
    format: str = Field(default="", max_length=64)
    crs: Optional[str] = Field(default=None, max_length=100)
    content_fingerprint: str = Field(default="", max_length=64)
    payload: Optional[DatasetPayloadRef] = None
    lineage: LineageEdge = Field(default_factory=LineageEdge)


class MapSourceBinding(BaseModel):
    model_config = _ManifestStrict

    source_key: str = Field(max_length=128)
    dataset: Optional[str] = Field(default=None, max_length=64)
    artifact: Optional[str] = Field(default=None, max_length=64)
    raster_ref: Optional[str] = Field(default=None, max_length=128)


class ManifestMap(BaseModel):
    model_config = _ManifestStrict

    logical_id: str = Field(max_length=64)
    name: str = Field(default="", max_length=255)
    mapspec_fingerprint: str = Field(default="", max_length=80)
    mapspec_path: str = Field(max_length=256)
    style_path: Optional[str] = Field(default=None, max_length=256)
    source_bindings: List[MapSourceBinding] = Field(
        default_factory=list, max_length=64)
    map_product_fingerprint: Optional[str] = Field(default=None, max_length=64)
    version_no: Optional[int] = None


class PackageFileEntry(BaseModel):
    """包内文件台账条目（manifest.json 自身不在内）。"""

    model_config = _ManifestStrict

    path: str = Field(max_length=256)
    sha256: str = Field(max_length=64)
    bytes: int = Field(ge=0)
    mime: str = Field(default="", max_length=128)


class DisclosureItem(BaseModel):
    model_config = _ManifestStrict

    kind: str = Field(max_length=64)
    name: str = Field(max_length=255)
    reason: str = Field(default="", max_length=MAX_MANIFEST_STR)


class Disclosure(BaseModel):
    model_config = _ManifestStrict

    not_included: List[DisclosureItem] = Field(default_factory=list, max_length=200)
    external_links: List[DisclosureItem] = Field(default_factory=list, max_length=200)
    unsupported: List[DisclosureItem] = Field(default_factory=list, max_length=200)


class FidelityDatasetEntry(BaseModel):
    """单数据集保真回执（写前读源、写后独立库重读、双侧比对）。"""

    model_config = _ManifestStrict

    logical_id: str = Field(max_length=64)
    fields_verified: Optional[bool] = None
    crs_match: Optional[bool] = None
    feature_count_match: Optional[bool] = None
    geometry_match: Optional[bool] = None
    bbox_match: Optional[bool] = None
    verdict: Literal[
        "exact", "semantic_equivalent", "partial", "not_evaluated",
    ] = FIDELITY_NOT_EVALUATED
    details: Dict[str, Any] = Field(default_factory=dict)


class FidelityReceipt(BaseModel):
    model_config = _ManifestStrict

    datasets: List[FidelityDatasetEntry] = Field(
        default_factory=list, max_length=MAX_LOGICAL_IDS)
    writer_versions: Dict[str, str] = Field(default_factory=dict)


class ReproductionSummary(BaseModel):
    model_config = _ManifestStrict

    level: Literal["full_offline_recompute", "view_only", "partial"]
    counts: Dict[str, int] = Field(default_factory=dict)


class PackageManifest(BaseModel):
    """webgis-project-package manifest（schema v1）。

    导入侧以 ``PackageManifest.model_validate`` 作 schema 门
    （extra="forbid"）；导出侧由 closure/writer 构造。
    """

    model_config = _ManifestStrict

    format: str = Field(default=PACKAGE_FORMAT)
    format_version: int = Field(default=PACKAGE_FORMAT_VERSION)
    package_id: str = Field(max_length=64)
    created_at: str = Field(max_length=64)
    origin: OriginInfo = Field(default_factory=OriginInfo)
    project: "ProjectInfo"
    selection: Dict[str, Any] = Field(default_factory=dict)
    reproduction: ReproductionSummary
    datasets: List[ManifestDataset] = Field(
        default_factory=list, max_length=MAX_LOGICAL_IDS)
    workflows: List[ManifestWorkflow] = Field(
        default_factory=list, max_length=MAX_LOGICAL_IDS)
    artifacts: List[ManifestArtifact] = Field(
        default_factory=list, max_length=MAX_LOGICAL_IDS)
    maps: List[ManifestMap] = Field(default_factory=list, max_length=MAX_LOGICAL_IDS)
    files: List[PackageFileEntry] = Field(
        default_factory=list, max_length=MAX_PACKAGE_ENTRIES)
    disclosure: Disclosure = Field(default_factory=Disclosure)
    fidelity_receipt: FidelityReceipt = Field(default_factory=FidelityReceipt)

    @field_validator("format")
    @classmethod
    def _format_gate(cls, v: str) -> str:
        if v != PACKAGE_FORMAT:
            raise ValueError(f"不支持的包格式: {v}")
        return v

    @field_validator("format_version")
    @classmethod
    def _version_gate(cls, v: int) -> int:
        if v != PACKAGE_FORMAT_VERSION:
            raise ValueError(f"不支持的包 schema 版本: {v}")
        return v


class ProjectInfo(BaseModel):
    model_config = _ManifestStrict

    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=MAX_MANIFEST_STR)
    metadata_json: Dict[str, Any] = Field(default_factory=dict)


PackageManifest.model_rebuild()


def format_gate(raw: Any) -> PackageManifest:
    """导入 schema 门：解析 + format/version + 有界校验（fail-closed）。"""
    if not isinstance(raw, dict):
        raise PackageError(ERR_MANIFEST_INVALID, "manifest 不是 JSON 对象")
    try:
        blob = json_bytes(raw)
    except (TypeError, ValueError) as e:
        raise PackageError(ERR_MANIFEST_INVALID, f"manifest 不可序列化: {e}") from e
    if len(blob) > MAX_MANIFEST_BYTES:
        raise PackageError(ERR_MANIFEST_TOO_LARGE,
                           f"manifest {len(blob)}B 超过 {MAX_MANIFEST_BYTES}B 上限")
    try:
        return PackageManifest.model_validate(raw)
    except Exception as e:
        raise PackageError(ERR_MANIFEST_INVALID, f"schema 校验失败: {e}") from e


def json_bytes(raw: Any) -> bytes:
    import json

    return json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")


def sha256_file(path) -> str:
    """流式文件摘要（打包/校验共用；O(文件) 不整读进内存）。"""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
