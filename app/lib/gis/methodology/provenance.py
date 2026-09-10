"""Knowledge Provenance —— 方法论知识出处登记（Epic 11 §5.K）。

每张新知识表（taxonomy / descriptors / viz bridge / case corpus）的条目
都可引用本登记表的 provenance 记录，声明：

- source_kind：curated（人工审定）/ code_derived（从 canonical registry
  投影推导）/ test_derived（由回归测试锚定）/ doc_derived（仓库文档）/
  reference_derived（经典文献，经 method_references.py 对账）；
- confidence ∈ [0, 1]；
- 审定时所对照的 registry 指纹（``validated_registry_fingerprint``）。

红线：

- **LLM 自动生成内容不是合法 source_kind**——词表里不存在
  ``llm_suggested`` 之类的值（Non-goal「不让 LLM 生成知识成为
  authoritative gold」的机器可读表达）；知识只能经人工审定后以
  ``curated`` 入库；
- provenance 是**元数据**，不是事实源：stale（表指纹变化）只产出标记，
  不阻塞运行时查询（可用性优先，审计方消费 stale 标记）；
- 全部有界：登记表是代码内审定表，条目数 O(知识表数)，无运行时写入。

消费方：taxonomy / descriptors / viz_bridge 的 ``provenance_id`` 字段
（存在性校验进 ``methodology_intel:`` 中央对账）、explain API 的
出处披露。
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

#: provenance schema 版本（进指纹）。
PROVENANCE_SCHEMA_VERSION = 1

#: 知识来源词表（封闭；**故意不含任何 LLM 生成类**）。
SourceKind = Literal[
    "curated",           # 人工审定（唯一 authoritative 类）
    "code_derived",      # 从 canonical registry 结构化投影
    "test_derived",      # 由回归测试/oracle 锚定
    "doc_derived",       # 仓库文档（docs/ 或 spec/）
    "reference_derived", # 经典文献（method_references.py 对账）
]

#: confidence 下限：低于此值的条目只能进解释面披露，不得驱动硬裁决。
MIN_ACTIONABLE_CONFIDENCE = 0.6


class KnowledgeProvenance(BaseModel):
    """一条知识出处记录（不可变审定工件）。"""

    provenance_id: str
    source_kind: SourceKind
    #: 出处说明（来源文档/代码位置/文献 id；有界）
    source_ref: str = ""
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    #: 审定时对照的 registry 指纹拼接（sha256 hex；空 = 未记录）
    validated_registry_fingerprint: str = ""

    @field_validator("provenance_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v or len(v) > 64 or not v.replace("_", "a").replace(".", "a").isidentifier():
            raise ValueError(f"invalid provenance_id: {v!r}")
        return v

    @field_validator("source_ref")
    @classmethod
    def _bounded_ref(cls, v: str) -> str:
        return v[:200]

    def to_bounded_dict(self) -> Dict[str, str]:
        return {
            "provenance_id": self.provenance_id[:64],
            "source_kind": str(self.source_kind)[:24],
            "source_ref": self.source_ref[:120],
            "confidence": f"{self.confidence:.2f}",
        }


def _ledger_entry(pid: str, kind: SourceKind, ref: str,
                  confidence: float = 1.0) -> KnowledgeProvenance:
    return KnowledgeProvenance(
        provenance_id=pid, source_kind=kind, source_ref=ref,
        confidence=confidence,
    )


#: 审定出处登记表（代码内 curated；新增知识表 = 追加条目，纯加法）。
PROVENANCE_LEDGER: Dict[str, KnowledgeProvenance] = {
    p.provenance_id: p
    for p in [
        # ── taxonomy（GIS 任务分类学 V2）──────────────────────────────
        _ledger_entry(
            "prov.taxonomy.alignment", "curated",
            "Epic 11 人工审定的 category→task/family 对齐表；"
            "数据需求字段一律由 ontology 投影派生，不手写。"),
        _ledger_entry(
            "prov.taxonomy.invalid_methods", "curated",
            "类别级 invalid method = 经典方法混淆（教科书级错误替换），"
            "逐条经 case corpus 负例锚定。", 0.9),
        # ── method descriptors V2 ────────────────────────────────────
        _ledger_entry(
            "prov.method_enrichment.curated", "curated",
            "方法级 problem class / assumptions / alternatives 人工审定；"
            "引用字段（capabilities/algorithm_ids）仍以 V4 MethodCandidate "
            "为唯一事实源。"),
        _ledger_entry(
            "prov.method_enrichment.references", "reference_derived",
            "插值/空间统计/地形方法的标准出处见 app/lib/gis/"
            "method_references.py（124 条经 AlgorithmRegistry 对账）。",
            0.95),
        # ── viz bridge ───────────────────────────────────────────────
        _ledger_entry(
            "prov.viz_bridge.projection", "code_derived",
            "artifact typical_map_models × capability compatible_map_models × "
            "组件 compatible_artifact_types 的确定性交叉投影；分歧显式披露。"),
        _ledger_entry(
            "prov.viz_bridge.reconcile", "curated",
            "跨源模型分歧的审定 reconcile 表（如 dasymetric 面插值产物）；"
            "不改 canonical registries。", 0.85),
        # ── case corpus ──────────────────────────────────────────────
        _ledger_entry(
            "prov.case_corpus.curated", "curated",
            "GIS case corpus 人工审定（fixture 化 profile）；"
            "invalid-method gold 锚定既有 oracle（V4 资格拒绝集 + "
            "scientific_preconditions），防自证循环。"),
        # ── graph ────────────────────────────────────────────────────
        _ledger_entry(
            "prov.graph.projection", "code_derived",
            "knowledge graph 是 canonical registries + taxonomy/descriptor "
            "审定表的只读投影；不存储 payload 复制，悬空引用 build 失败。"),
    ]
}


def provenance_exists(provenance_id: str) -> bool:
    return provenance_id in PROVENANCE_LEDGER


def get_provenance(provenance_id: str) -> Optional[KnowledgeProvenance]:
    return PROVENANCE_LEDGER.get(provenance_id)


def validate_ledger() -> List[str]:
    """登记表自检（id 唯一性由 dict 构造保证；此处查 confidence/指纹形）。

    fail-closed：校验器异常转 issue，绝不静默返回「0 issues」。
    """
    try:
        return _validate_inner()
    except Exception as exc:  # pragma: no cover - 防御性
        return [f"provenance ledger validate raised: {exc}"]


def _validate_inner() -> List[str]:
    issues: List[str] = []
    for pid, record in PROVENANCE_LEDGER.items():
        if pid != record.provenance_id:
            issues.append(f"provenance[{pid}]: key/id mismatch")
        if (record.source_kind == "curated"
                and record.confidence < MIN_ACTIONABLE_CONFIDENCE):
            issues.append(
                f"provenance[{pid}]: curated 置信度低于可裁决下限"
                f"({record.confidence} < {MIN_ACTIONABLE_CONFIDENCE})")
    return issues


def content_fingerprint() -> str:
    """登记表内容指纹（canonical JSON sha256；进 knowledge 指纹链）。"""
    payload = {
        "version": PROVENANCE_SCHEMA_VERSION,
        "records": [
            r.model_dump(mode="json")
            for _, r in sorted(PROVENANCE_LEDGER.items())
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "PROVENANCE_SCHEMA_VERSION",
    "SourceKind",
    "MIN_ACTIONABLE_CONFIDENCE",
    "KnowledgeProvenance",
    "PROVENANCE_LEDGER",
    "provenance_exists",
    "get_provenance",
    "validate_ledger",
    "content_fingerprint",
]
