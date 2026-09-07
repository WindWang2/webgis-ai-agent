"""GisExtensionManifest：扩展包的正式声明契约（ADR-0104 / Wave 1）。

设计原则：
- **fail closed**：pydantic ``extra="forbid"`` + 严格类型；任何未知字段、
  非法枚举、越界值都在解析期拒绝，不存在宽容降级路径。
- **声明即承诺**：tools / algorithms / data_providers / cartography_items /
  workflow_packs 各节是「激活后将注册哪些条目」的声明清单。激活期
  host 对照实际投影逐项核对——未声明的注册（UNDECLARED_REGISTRATION）
  与声明了却没注册（DECLARED_BUT_UNREGISTERED）都是 error 级诊断。
- **命名空间强制**：id 必须 ``<namespace>.<name>``；namespace 进入所有
  投影前缀，扩展条目在物理上不可能遮蔽核心 authoritative ID。
- **schema 版本化**：``schema_version`` 高于宿主认知即拒绝（拒绝静默
  忽略新字段）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .api_version import MANIFEST_SCHEMA_VERSION, is_version
from .trust import DECLARABLE_TRUST_LEVELS

# namespace / name 词表：小写字母开头的 snake 片段，防止与既有工具命名
# 空间、Python 标识符、文件系统路径产生歧义。
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# 保留命名空间：与核心子系统 / 运行时组件撞名的一律拒绝。
RESERVED_NAMESPACES = frozenset(
    {"core", "webgis", "app", "pi", "builtin", "internal", "gis", "lib", "tools", "vendor"}
)

EXTENSION_TYPES = frozenset(
    {"tools", "algorithms", "data_providers", "cartography", "workflow"}
)
# V1 明确不支持、但词表保留以产出精准诊断（EXTENSION_TYPE_UNSUPPORTED）
# 的类型；见 docs/extension-platform/limitations.md。
RESERVED_FUTURE_TYPES = frozenset({"model_provider"})

# 声明体量的硬上界（防御畸形 manifest 拖垮解析/投影）。
MAX_DECLARED_ITEMS = 128
MAX_SETTINGS_SCHEMA_BYTES = 32 * 1024
MAX_DESCRIPTION_CHARS = 2000


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DependencyDeclaration(_StrictModel):
    """对其它扩展的依赖。required 缺失 → 激活失败；optional 缺失 → degraded。"""

    id: str
    required: bool = True
    feature_flag: Optional[str] = None


class ToolDeclaration(_StrictModel):
    name: str = Field(..., description="工具名（不含命名空间前缀；投影为 <ns>_<name>）")
    description: str
    summary: str = ""
    tier: int = Field(default=2, ge=1, le=2, description="扩展工具禁入 tier 3（安全 chokepoint）")
    side_effect: str = "unclassified"

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"tool name {v!r} must match {_NAME_RE.pattern}")
        return v


class AlgorithmDeclaration(_StrictModel):
    id: str = Field(..., description="算法 id（不含命名空间前缀；投影为 <ns>.<id>）")
    description: str = ""
    scientific_status: str = "EXPERIMENTAL"

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"algorithm id {v!r} must match {_NAME_RE.pattern}")
        return v


class DataProviderDeclaration(_StrictModel):
    source_type: str = Field(..., description="source type（不含前缀；投影为 <ns>_<source_type>）")
    description: str = ""
    supports_query: bool = True

    @field_validator("source_type")
    @classmethod
    def _st_shape(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"source_type {v!r} must match {_NAME_RE.pattern}")
        return v


class CartographyItemDeclaration(_StrictModel):
    kind: str = Field(..., description="component | model | theme")
    id: str
    description: str = ""
    runtime_status: str = "planned"

    @field_validator("kind")
    @classmethod
    def _kind_vocab(cls, v: str) -> str:
        if v not in {"component", "model", "theme"}:
            raise ValueError(f"cartography kind must be component|model|theme, got {v!r}")
        return v

    @field_validator("runtime_status")
    @classmethod
    def _status_vocab(cls, v: str) -> str:
        if v not in {"native", "planned", "unavailable"}:
            raise ValueError(f"runtime_status must be native|planned|unavailable, got {v!r}")
        return v


class WorkflowPackDeclaration(_StrictModel):
    pack_id: str = Field(..., description="recipe pack id（不含前缀；投影为 <ns>_<pack_id>）")
    description: str = ""
    recipe_count: int = Field(default=0, ge=0, le=MAX_DECLARED_ITEMS)


class GisExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = MANIFEST_SCHEMA_VERSION
    id: str = Field(..., description="全局唯一扩展 id：<namespace>.<name>")
    name: str = Field(..., description="namespace 内的短名（与 id 尾段一致）")
    namespace: str
    version: str
    api_version: str = "1.0.0"
    minimum_core_version: str = "0.1.0"
    maximum_core_version: Optional[str] = None
    title: str = ""
    description: str = ""
    vendor: str = ""
    extension_types: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(
        default_factory=list,
        description="向 capability 目录自述的能力标签（信息性，非授权）",
    )
    permissions: list[str] = Field(default_factory=list)
    trust: str = Field(default="local_untrusted", description="信任自荐（不具授权效力）")
    dependencies: list[DependencyDeclaration] = Field(default_factory=list)
    optional_dependencies: list[DependencyDeclaration] = Field(default_factory=list)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    settings_schema: Optional[dict[str, Any]] = None
    tools: list[ToolDeclaration] = Field(default_factory=list)
    algorithms: list[AlgorithmDeclaration] = Field(default_factory=list)
    data_providers: list[DataProviderDeclaration] = Field(default_factory=list)
    cartography_items: list[CartographyItemDeclaration] = Field(default_factory=list)
    workflow_packs: list[WorkflowPackDeclaration] = Field(default_factory=list)
    entry_point: str = Field(..., description="扩展目录内入口模块名（不含 .py），须提供 activate()")
    diagnostics_entry: Optional[str] = Field(
        default=None, description="扩展目录内健康检查函数 'module:function'"
    )

    # ── 结构校验（全部 fail closed，错误消息可读） ─────────────────────
    @field_validator("namespace")
    @classmethod
    def _ns_valid(cls, v: str) -> str:
        if not _TOKEN_RE.match(v):
            raise ValueError(f"namespace {v!r} must match {_TOKEN_RE.pattern}")
        if v in RESERVED_NAMESPACES:
            raise ValueError(f"namespace {v!r} is reserved")
        return v

    @field_validator("trust")
    @classmethod
    def _trust_vocab(cls, v: str) -> str:
        if v not in DECLARABLE_TRUST_LEVELS:
            raise ValueError(f"trust must be one of {sorted(t.value for t in DECLARABLE_TRUST_LEVELS)}")
        return v

    @field_validator("version", "api_version", "minimum_core_version", "maximum_core_version")
    @classmethod
    def _versions_semver(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not is_version(v):
            raise ValueError(f"version {v!r} is not X.Y[.Z] semver")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> "GisExtensionManifest":
        # id 与 namespace/name 一致性：id == f"{namespace}.{name}"。
        expected_id = f"{self.namespace}.{self.name}"
        if self.id != expected_id:
            raise ValueError(f"manifest id {self.id!r} must equal {expected_id!r}")
        # schema 版本 fail closed。
        if self.schema_version > MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"manifest schema_version {self.schema_version} newer than supported "
                f"{MANIFEST_SCHEMA_VERSION}（升级宿主或降级扩展）"
            )
        if self.schema_version < 1:
            raise ValueError("manifest schema_version must be >= 1")
        # 上界窗口必须高于下界。
        from .api_version import parse_version

        if self.maximum_core_version is not None:
            lo, hi = parse_version(self.minimum_core_version), parse_version(self.maximum_core_version)
            assert lo is not None and hi is not None
            if lo >= hi:
                raise ValueError("maximum_core_version must exceed minimum_core_version")
        # 扩展类型词表。
        for ext_type in self.extension_types:
            if ext_type in RESERVED_FUTURE_TYPES:
                raise ValueError(f"extension type {ext_type!r} not supported in api_version 1.x")
            if ext_type not in EXTENSION_TYPES:
                raise ValueError(
                    f"unknown extension type {ext_type!r}; valid: {sorted(EXTENSION_TYPES)}"
                )
        # 声明条目上界。
        total = len(self.tools) + len(self.algorithms) + len(self.data_providers) + len(
            self.cartography_items
        ) + len(self.workflow_packs)
        if total > MAX_DECLARED_ITEMS:
            raise ValueError(f"manifest declares {total} items; limit is {MAX_DECLARED_ITEMS}")
        if len(self.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError("manifest description exceeds length limit")
        if self.settings_schema is not None:
            import json

            if len(json.dumps(self.settings_schema)) > MAX_SETTINGS_SCHEMA_BYTES:
                raise ValueError("settings_schema exceeds size limit")
            if not isinstance(self.settings_schema.get("type", "object"), str):
                raise ValueError("settings_schema.type must be a string")
        # 声明条目 id 在各自节内唯一。
        for section, items in (
            ("tools", [t.name for t in self.tools]),
            ("algorithms", [a.id for a in self.algorithms]),
            ("data_providers", [p.source_type for p in self.data_providers]),
            ("workflow_packs", [w.pack_id for w in self.workflow_packs]),
        ):
            dupes = {x for x in items if items.count(x) > 1}
            if dupes:
                raise ValueError(f"duplicate ids in {section}: {sorted(dupes)}")
        carto_keys = [(c.kind, c.id) for c in self.cartography_items]
        if len(carto_keys) != len(set(carto_keys)):
            raise ValueError("duplicate (kind, id) in cartography_items")
        return self

    def declared_type_set(self) -> frozenset[str]:
        """由声明节推导的扩展类型（extension_types 允许缺省时兜底）。"""
        inferred: set[str] = set(self.extension_types)
        if self.tools:
            inferred.add("tools")
        if self.algorithms:
            inferred.add("algorithms")
        if self.data_providers:
            inferred.add("data_providers")
        if self.cartography_items:
            inferred.add("cartography")
        if self.workflow_packs:
            inferred.add("workflow")
        return frozenset(inferred)

    def namespaced_tool_name(self, tool_name: str) -> str:
        return f"{self.namespace}_{tool_name}"

    def namespaced_algorithm_id(self, algorithm_id: str) -> str:
        return f"{self.namespace}.{algorithm_id}"

    def namespaced_source_type(self, source_type: str) -> str:
        return f"{self.namespace}_{source_type}"


def manifest_from_dict(data: Any) -> tuple[Optional[GisExtensionManifest], Optional[str]]:
    """解析 manifest dict → (manifest, None) 或 (None, 错误消息)。

    pydantic ValidationError 的原始输出对扩展作者不可读，这里压缩为
    单行定位信息（CLI / 诊断共同消费）。
    """
    if not isinstance(data, dict):
        return None, f"manifest must be a JSON object, got {type(data).__name__}"
    try:
        return GisExtensionManifest.model_validate(data), None
    except Exception as exc:  # noqa: BLE001 - 归一为解析失败诊断
        return None, str(exc)
