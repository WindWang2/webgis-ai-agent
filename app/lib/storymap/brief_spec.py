"""证据化地图简报契约（G11 / ADR-0288）——MapBriefSpec 领域模型。

MapBriefSpec **继承** StoryMapSpec（additive-only）：既有 v1.1 消费者以
``StoryMapSpec.model_validate`` 读简报 dict 时未知键 round-trip 保留
（``extra="allow"`` 纪律），本模块在其上追加三层结构：

- ``blocks``  —— 内容块：编辑器的最小单元（稳定 ID、origin、locked）。
  数值只经 ``facts`` 结构化进入简报；自由文本中的数字不可被标 verified。
- ``facts``   —— 结构化数值事实：每个自动数值的权威载体，绑定
  EvidenceBinding（artifact/dataset 版本引用 + 用户可读来源口径）。
- ``provenance`` —— 会话/产物/MapSpec 指纹溯源（重生成与 stale 判定的基准）。

证据绑定是**引用与只读投影**，不是第二事实库：真身在
ArtifactRegistry/CAS/dataset 版本；本契约只保存 ref + 口径 + 对账指纹，
重读失败封闭（status 落 unverified + 原因），绝不编造数值。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import Field, model_validator

from app.lib.storymap.spec import StoryMapSpec, _StoryModel

#: 简报契约自身版本（独立于 StoryMapSpec.schema_version 演进）。
BRIEF_SPEC_SCHEMA_VERSION = "brief-1"

#: 块 kind 词表（BRF-01 内容块面）：
#: text=说明文本 map_view=地图视图 metric=指标卡 chart/table=统计图表
#: method=方法说明 limitation=局限建议 source=来源引用
BlockKind = Literal[
    "text", "map_view", "metric", "chart", "table",
    "method", "limitation", "source",
]

#: 事实 kind（对齐 evidence_claim.ClaimType 的数值子集；narrative 不在此）。
FactKind = Literal[
    "count", "sum", "mean", "median", "density", "rate", "ratio",
    "percentage", "share", "rank", "maximum", "minimum", "change",
    "difference", "other",
]

#: 事实核验状态（BRF-O02：分母缺失/单位不一/口径漂移 → warning/unverified，
#: 绝不静默放行为 verified）。
FactStatus = Literal["verified", "warning", "unverified"]

#: 内容来源：auto=编译器生成（重生成可刷新）manual=用户自写（默认锁定保护）。
Origin = Literal["auto", "manual"]

# 块/事实 ID 词表：`blk-`/`fact-` 前缀 + 受限字符（防引用串注入）。
_ID_RE = re.compile(r"^(blk|fact)-[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# artifact ref 词表与 story_compiler._REF_RE 同源（ref:chart-… / ref:table-…）。
_ARTIFACT_REF_RE = re.compile(r"^ref:[a-z]+-[A-Za-z0-9_/]{1,96}$")

# 简报 spec 序列化上限（与 story asset 同一数量级；blocks+facts 有界）。
MAX_BRIEF_BLOCKS = 200
MAX_BRIEF_FACTS = 200
MAX_BRIEF_SOURCES = 64


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EvidenceBinding(_StoryModel):
    """证据绑定：ref + 用户可读来源口径 + 内部对账指纹。

    用户可读面（source_label/method/stat_semantics/时间窗）是简报"来源"
    展示的权威；内部面（artifact_sha256/dataset_version_ref）只作对账，
    不承担展示义务。缺 ref 的绑定只允许出现在 manual 块上（用户自述）。
    """

    # 引用面（真身在 ArtifactRegistry / dataset 版本 / ClaimStore 投影）。
    artifact_ref: str = ""
    dataset_version_ref: str = ""
    claim_id: str = ""
    # 用户可读来源（BRF-01：不能只有无意义 hash）。
    source_label: str = ""
    produced_at: str = ""
    method: str = ""
    stat_semantics: str = ""
    unit: str = ""
    # 口径与范围。
    scope_spatial: str = ""
    temporal_window: str = ""
    # 内部对账指纹（不进用户展示面）。
    artifact_sha256: str = ""
    bound_at: str = Field(default_factory=_utcnow_iso)

    @model_validator(mode="after")
    def _ref_shape(self) -> "EvidenceBinding":
        if self.artifact_ref and not _ARTIFACT_REF_RE.match(self.artifact_ref):
            raise ValueError(f"invalid artifact ref shape: {self.artifact_ref!r}")
        return self


class BriefMapView(_StoryModel):
    """地图视图块载荷：镜头 + 图层白名单 + MapSpec 版本 pin。

    BRF-O04：镜头/图层/图例绑定明确 MapSpec 产物版本 —— 当前会话换城市
    不改变旧简报内容；指纹缺失时渲染层必须显式告警，不得沿用会话现状
    伪造一致。
    """

    model_config = _StoryModel.model_config | {"allow_inf_nan": False}

    center: List[float] = Field(..., min_length=2, max_length=2)
    zoom: float = Field(4.0, ge=0.0, le=20.0)
    pitch: float = Field(0.0, ge=0.0, le=85.0)
    bearing: float = Field(0.0, ge=-180.0, le=180.0)
    visible_layer_ids: List[str] = Field(default_factory=list, max_length=32)
    mapspec_fingerprint: str = ""
    # 冻结后由服务端填充：静态图件 ref（SVG/PNG 经既有导出服务）。
    static_image_ref: str = ""


class BriefFact(_StoryModel):
    """结构化数值事实：简报内每个自动数值的唯一权威载体。

    ratio 必须指向 denominator_fact_id；change 必须指向 baseline_fact_id
    （BRF-O02：无分母/基期的比例与增长不可 verified —— 编译器不凑数，
    校验器不放过）。
    """

    model_config = _StoryModel.model_config | {"allow_inf_nan": False}

    fact_id: str
    label: str
    kind: FactKind = "other"
    value: float
    unit: str = ""
    basis: EvidenceBinding = Field(default_factory=EvidenceBinding)
    denominator_fact_id: str = ""
    baseline_fact_id: str = ""
    status: FactStatus = "unverified"
    status_reason: str = ""
    origin: Origin = "auto"

    @model_validator(mode="after")
    def _id_shape(self) -> "BriefFact":
        if not _ID_RE.match(self.fact_id):
            raise ValueError(f"invalid fact id: {self.fact_id!r}")
        needs_denominator = self.kind in ("ratio", "percentage", "share", "rate")
        if needs_denominator and not self.denominator_fact_id:
            raise ValueError(
                f"fact {self.fact_id} of kind {self.kind} requires "
                "denominator_fact_id (no fabricated proportions)")
        if self.kind == "change" and not self.baseline_fact_id:
            raise ValueError(
                f"fact {self.fact_id} of kind change requires "
                "baseline_fact_id (no fabricated growth)")
        if self.status == "verified" and not self.basis.artifact_ref:
            raise ValueError(
                f"fact {self.fact_id} cannot be verified without an "
                "artifact binding (evidence-first)")
        return self


class BriefBlock(_StoryModel):
    """内容块：编辑器最小单元。稳定 ID；locked 块重生成不覆盖。"""

    id: str
    chapter_id: str
    kind: BlockKind = "text"
    order: int = Field(0, ge=0)
    origin: Origin = "auto"
    locked: bool = False
    # 内容面（按 kind 取用；多余字段容忍 round-trip）。
    text: str = ""
    fact_id: str = ""
    widget_id: str = ""
    map_view: Optional[BriefMapView] = None
    binding: Optional[EvidenceBinding] = None
    updated_at: str = Field(default_factory=_utcnow_iso)

    @model_validator(mode="after")
    def _id_shape(self) -> "BriefBlock":
        if not _ID_RE.match(self.id):
            raise ValueError(f"invalid block id: {self.id!r}")
        if self.kind == "metric" and not self.fact_id:
            raise ValueError(
                f"metric block {self.id} requires fact_id (metrics are "
                "evidence-bound, never free-text)")
        if self.kind in ("chart", "table") and not self.widget_id:
            raise ValueError(
                f"{self.kind} block {self.id} requires widget_id")
        if self.kind == "map_view" and self.map_view is None:
            raise ValueError(f"map_view block {self.id} requires map_view")
        return self


class BriefProvenance(_StoryModel):
    """简报溯源：重生成与 stale 判定的基准指纹。"""

    session_id: str = ""
    turn_id: str = ""
    artifact_refs: List[str] = Field(default_factory=list, max_length=128)
    mapspec_fingerprint: str = ""
    generator: Literal["deterministic", "stub_llm", "llm"] = "deterministic"
    generated_at: str = Field(default_factory=_utcnow_iso)


class MapBriefSpec(StoryMapSpec):
    """证据化地图简报规范（StoryMapSpec 的 additive 扩展）。"""

    brief_schema_version: str = BRIEF_SPEC_SCHEMA_VERSION
    provenance: BriefProvenance = Field(default_factory=BriefProvenance)
    blocks: List[BriefBlock] = Field(default_factory=list,
                                     max_length=MAX_BRIEF_BLOCKS)
    facts: List[BriefFact] = Field(default_factory=list,
                                   max_length=MAX_BRIEF_FACTS)

    @model_validator(mode="after")
    def _cross_references_resolve(self) -> "MapBriefSpec":
        chapter_ids = {ch.id for ch in self.chapters}
        block_ids = set()
        fact_ids = set()
        for block in self.blocks:
            if block.id in block_ids:
                raise ValueError(f"duplicate block id {block.id!r}")
            block_ids.add(block.id)
            if block.chapter_id not in chapter_ids:
                raise ValueError(
                    f"block {block.id} references unknown chapter "
                    f"{block.chapter_id!r}")
        for fact in self.facts:
            if fact.fact_id in fact_ids:
                raise ValueError(f"duplicate fact id {fact.fact_id!r}")
            fact_ids.add(fact.fact_id)
        # 依赖引用（分母/基期/块绑定）必须在同一简报内可解析。
        for fact in self.facts:
            deps = (fact.denominator_fact_id, fact.baseline_fact_id)
            for dep in deps:
                if dep and dep not in fact_ids:
                    raise ValueError(
                        f"fact {fact.fact_id} references unknown "
                        f"dependency fact {dep!r}")
        for block in self.blocks:
            if block.fact_id and block.fact_id not in fact_ids:
                raise ValueError(
                    f"block {block.id} references unknown fact "
                    f"{block.fact_id!r}")
        # 环状依赖禁止（denominator/baseline 不得指向自身或成环）。
        graph = {
            f.fact_id: [d for d in (f.denominator_fact_id, f.baseline_fact_id)
                        if d]
            for f in self.facts
        }
        for start in graph:
            seen: set = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node == start and node in seen:
                    raise ValueError(
                        f"fact dependency cycle detected at {start!r}")
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(graph.get(node, []))
        return self


def is_brief_payload(payload: object) -> bool:
    """粗判 dict 是否为简报载荷（路由分流用，不做完整校验）。"""
    return (
        isinstance(payload, dict)
        and payload.get("brief_schema_version") == BRIEF_SPEC_SCHEMA_VERSION
    )


__all__ = [
    "BRIEF_SPEC_SCHEMA_VERSION",
    "BlockKind",
    "FactKind",
    "FactStatus",
    "Origin",
    "EvidenceBinding",
    "BriefMapView",
    "BriefFact",
    "BriefBlock",
    "BriefProvenance",
    "MapBriefSpec",
    "MAX_BRIEF_BLOCKS",
    "MAX_BRIEF_FACTS",
    "MAX_BRIEF_SOURCES",
    "is_brief_payload",
]
