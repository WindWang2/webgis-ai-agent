"""CapabilityDescriptors —— 结构化能力描述符联合检索（V7 ADR-0135 D4）。

V6 基线：hybrid 检索（词法 + 双语扩展 + capability graph + 方法论 +
embedding 可选）已超越字符串匹配（ADR-0119 D1-D3），但**结构面**缺失 ——
检索信号不看 preconditions（几何/CRS/字段前提）、不看 cost 档位、不看
历史可靠性、不展开 fallback 链；template/component/model/workflow 各自
为战（只在 tool 面汇合）。

V7 契约（**只读投影，不复制业务元数据** —— 描述符由既有 registry 派生）：

- **统一 CapabilityDescriptor**（V7 投影，区别于 capability_registry 的
  CapabilityDescriptor 事实源）：kind ∈ {capability, algorithm, template,
  component}；preconditions（几何/CRS 类/最小要素数/必需字段/科学前提）；
  postconditions（输出 artifact 类型 + 不确定性输出）；cost（cpu/memory/io
  档位数值化）；latency/resource profile（complexity + preferred_execution
  + resource_envelope 声明）；fallback chain（registry 声明序）。
- **结构化检索**：``select_capabilities(request)`` —— 词法种子命中 +
  precondition 硬过滤（不满足 → 剔除并披露原因）+ compat 加成
  （geometry/crs/data_kind 匹配）+ cost 偏好 + **可靠性反馈**
  （``reliability`` 注入：durable recovery ledger 聚合投影，成功/失败
  计数 → score）。同输入同序（确定性 tie-break by id）。
- **fallback 链展开**：top 命中的 fallback 链以 ``fallback_available``
  附加披露（不自动入候选 —— fallback 裁决仍归 resolver/finalizer）。

可靠性来源：``reliability_from_ledger(session_id)`` 读 durable ledger 的
(session, tool) 聚合（成功清零/失败 +1 的既有语义 → 当前未清零的失败
计数即负反馈）。无 session / 无记录 → 中性（0 反馈），不虚构历史。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def v7_capability_retrieval_enabled() -> bool:
    """kill switch（默认开；=0 回到 V6 逐位行为）。"""
    return os.getenv("GIS_CAPABILITY_RETRIEVAL_V7", "1") not in ("0", "false", "False")


#: kind 封闭词表。
KIND_CAPABILITY = "capability"
KIND_ALGORITHM = "algorithm"
KIND_TEMPLATE = "template"
KIND_COMPONENT = "component"

#: CostLevel 档位 → 数值（低 = 好；加成取负）。
_COST_RANK = {"low": 0, "medium": 1, "high": 2}

#: 有界预算。
MAX_INDEX = 512
MAX_FALLBACK_CHAIN = 4
MAX_RESULTS = 16


# ── V7 投影描述符 ────────────────────────────────────────────────────────


class CapabilityDescriptorV7(BaseModel):
    """统一能力描述符（registry 事实源的只读投影；零业务语义复制）。"""

    id: str
    kind: str                          # capability | algorithm | template | component
    label: str = ""
    corpus_text: str = ""              # 检索语料（label+tags+description 有界拼接）
    # preconditions（不满足 → 硬过滤剔除）
    geometry_requirements: List[str] = Field(default_factory=list)
    crs_requirements: str = ""
    crs_class: str = ""
    min_features: Optional[int] = None
    required_fields: List[str] = Field(default_factory=list)
    scientific_preconditions: List[str] = Field(default_factory=list)
    # postconditions / schema
    output_artifact_types: List[str] = Field(default_factory=list)
    input_artifact_types: List[str] = Field(default_factory=list)
    uncertainty_outputs: List[str] = Field(default_factory=list)
    parameter_contract_ref: str = ""
    # cost / latency / resource profile（声明式）
    cpu_cost: str = "medium"
    memory_cost: str = "medium"
    io_cost: str = "medium"
    complexity: str = ""
    preferred_execution: str = ""
    # fallback chain（registry 声明序；有界）
    fallback_chain: List[str] = Field(default_factory=list)
    # 关联（capability ↔ algorithm ↔ tool 面的反查投影）
    related_tools: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id[:64],
            "kind": self.kind,
            "label": self.label[:80],
            "preconditions": {
                "geometry": self.geometry_requirements[:4],
                "crs_requirements": self.crs_requirements[:48],
                "crs_class": self.crs_class[:24],
                "min_features": self.min_features,
                "required_fields": self.required_fields[:6],
                "scientific": self.scientific_preconditions[:4],
            },
            "postconditions": {
                "outputs": self.output_artifact_types[:4],
                "uncertainty": self.uncertainty_outputs[:4],
                "param_contract_ref": self.parameter_contract_ref[:48],
            },
            "cost": {"cpu": self.cpu_cost, "memory": self.memory_cost,
                     "io": self.io_cost, "complexity": self.complexity[:24]},
            "profile": {"preferred_execution": self.preferred_execution[:24]},
            "fallback_chain": [f[:64] for f in self.fallback_chain[:MAX_FALLBACK_CHAIN]],
            "related_tools": [t[:64] for t in self.related_tools[:6]],
        }


def _corpus_text(*parts: Iterable[Any]) -> str:
    words: List[str] = []
    for group in parts:
        for item in group:
            if item:
                words.append(str(item))
    return " ".join(words)[:400].lower()


def build_capability_index() -> Dict[str, CapabilityDescriptorV7]:
    """registry 事实源 → 统一描述符索引（确定性；进程级缓存见
    ``get_capability_index_cached``）。

    来源：
    - capability registry 全量（kind=capability；fallback_capabilities）；
    - algorithm registry（kind=algorithm；scientific_preconditions /
      uncertainty_outputs / fallback_algorithms / resource profile）；
    - template catalog（kind=template；轻投影）；
    - component 目录（kind=component；默认组件 id 表投影）。
    任一来源缺席 → 跳过该段（不虚构）。"""
    index, _complete = _build_capability_index_state()
    return index


def _build_capability_index_state() -> Tuple[Dict[str, CapabilityDescriptorV7], bool]:
    """构建索引 + 完整性标记（评审 F10：残缺索引不缓存）。"""
    index: Dict[str, CapabilityDescriptorV7] = {}
    complete = True

    # 1) capability registry
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        caps = get_capability_registry()
        for cid in _all_ids(caps)[:MAX_INDEX]:
            d = caps.get(cid)
            if d is None:
                continue
            index[f"capability:{d.id}"] = CapabilityDescriptorV7(
                id=str(d.id), kind=KIND_CAPABILITY,
                label=str(d.name or d.id),
                corpus_text=_corpus_text([d.id, d.name, d.description, d.domain,
                                          d.category, d.required_fields],
                                         [d.input_artifact_types],
                                         [d.output_artifact_types]),
                geometry_requirements=[str(g) for g in (
                    d.geometry_requirements or [])][:4],
                required_fields=[str(f) for f in (d.required_fields or [])][:6],
                output_artifact_types=[str(o) for o in (
                    d.output_artifact_types or [])][:4],
                input_artifact_types=[str(i) for i in (
                    d.input_artifact_types or [])][:4],
                preferred_execution=str(d.preferred_execution or ""),
                cpu_cost="low" if d.supports_large_data else "medium",
                fallback_chain=[str(f) for f in (
                    d.fallback_capabilities or [])][:MAX_FALLBACK_CHAIN],
            )
    except Exception:  # noqa: BLE001 — registry 缺席跳过该段
        complete = False
        logger.debug("[CapabilityIndex] capability registry unavailable",
                     exc_info=True)

    # 2) algorithm registry
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        algos = get_algorithm_registry()
        for aid in _all_ids(algos)[:MAX_INDEX]:
            d = algos.get(aid)
            if d is None:
                continue
            index[f"algorithm:{d.id}"] = CapabilityDescriptorV7(
                id=str(d.id), kind=KIND_ALGORITHM,
                label=str(d.name or d.id),
                corpus_text=_corpus_text([d.id, d.name, d.category,
                                          d.subcategory, d.tags,
                                          d.algorithm_family],
                                         [d.capabilities]),
                geometry_requirements=[str(g) for g in (
                    d.geometry_requirements or [])][:4],
                crs_requirements=str(d.crs_requirements or "")[:48],
                crs_class=str(getattr(d, "crs_class", "") or ""),
                min_features=d.min_features,
                required_fields=[str(f) for f in (d.required_fields or [])][:6],
                scientific_preconditions=[str(p) for p in (
                    getattr(d, "scientific_preconditions", None) or [])][:4],
                output_artifact_types=([str(d.output_artifact_type)]
                                       if d.output_artifact_type else []),
                input_artifact_types=[str(i) for i in (
                    d.input_artifact_types or [])][:4],
                uncertainty_outputs=[str(u) for u in (
                    getattr(d, "uncertainty_outputs", None) or [])][:4],
                parameter_contract_ref=str(d.parameter_contract_ref or ""),
                cpu_cost=str(getattr(d.cpu_cost, "value", d.cpu_cost) or "medium"),
                memory_cost=str(getattr(d.memory_cost, "value", d.memory_cost)
                                or "medium"),
                io_cost=str(getattr(d.io_cost, "value", d.io_cost) or "medium"),
                complexity=str(d.complexity or ""),
                preferred_execution=str(d.preferred_execution_policy or "")[:24],
                fallback_chain=[str(f) for f in (
                    d.fallback_algorithms or [])][:MAX_FALLBACK_CHAIN],
                related_tools=[str(t) for t in (d.tool_candidates or [])][:6],
            )
    except Exception:  # noqa: BLE001
        complete = False
        logger.debug("[CapabilityIndex] algorithm registry unavailable",
                     exc_info=True)

    # 3) product templates（SEED_PRODUCT_TEMPLATES → kind=template 轻投影）
    try:
        from app.services.gis_harness.product_templates import (
            SEED_PRODUCT_TEMPLATES,
        )

        for t in list(SEED_PRODUCT_TEMPLATES)[:64]:
            index[f"template:{t.id}"] = CapabilityDescriptorV7(
                id=str(t.id), kind=KIND_TEMPLATE,
                label=str(t.name or t.id),
                corpus_text=_corpus_text([t.id, t.name, t.description],
                                         [t.subject_categories],
                                         [t.task_affinity]),
            )
    except Exception:  # noqa: BLE001
        complete = False
        logger.debug("[CapabilityIndex] template catalog unavailable",
                     exc_info=True)

    # 4) component 目录（completion contracts 的默认组件 id 表 —— 静态
    #    单一来源；build_default_components 需要计划上下文，索引期不用）
    try:
        from app.services.gis_harness.completion.contracts import (
            _COMPONENT_DEFAULT_IDS,
        )

        for comp_type, cid in list(_COMPONENT_DEFAULT_IDS.items())[:32]:
            index[f"component:{cid}"] = CapabilityDescriptorV7(
                id=str(cid), kind=KIND_COMPONENT,
                label=str(comp_type),
                corpus_text=_corpus_text([comp_type, cid]),
            )
    except Exception:  # noqa: BLE001
        complete = False
        logger.debug("[CapabilityIndex] component catalog unavailable",
                     exc_info=True)

    # F10：capability 段补 related_tools（algorithm registry 的
    # capability→tools 反查）—— 让 durable ledger 的工具失败计数能命中
    # capability 类描述符（否则可靠性反馈对 capability 永不生效）。
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        cap_tool_map = get_algorithm_registry().capability_tool_map()
        for desc in index.values():
            if desc.kind == KIND_CAPABILITY and not desc.related_tools:
                bare = desc.id
                desc.related_tools = [
                    str(t)[:64] for t in (cap_tool_map.get(bare) or ())[:6]
                ]
    except Exception:  # noqa: BLE001 — 反查缺席不致命
        logger.debug("[CapabilityIndex] capability→tool map unavailable",
                     exc_info=True)
    return index, complete


# ── 可靠性反馈（durable ledger 聚合投影；中性缺省）──────────────────────


def reliability_from_ledger(session_id: str) -> Dict[str, Dict[str, int]]:
    """session durable ledger → {tool: {fail: n}}（只投未清零的失败面）。

    键格式 ``tool:class``，值 ``{"attempts": n}``（snapshot 既有语义；
    成功回写在 ledger 侧已清零 —— 这里零额外语义）。无 session / 读
    失败 → 空（中性）。"""
    out: Dict[str, Dict[str, int]] = {}
    if not session_id:
        return out
    try:
        from app.services.gis_harness.recovery_ledger import get_recovery_ledger

        snapshot = get_recovery_ledger().snapshot(session_id, max_entries=64)
        for key, info in list((snapshot or {}).items())[:64]:
            if not isinstance(info, dict):
                continue
            tool = str(key).split(":", 1)[0][:64]
            n = int(info.get("attempts") or 0)
            if n <= 0:
                continue
            out.setdefault(tool, {"fail": 0})
            out[tool]["fail"] += n
    except Exception:  # noqa: BLE001 — 反馈缺席中性
        logger.debug("[CapabilityIndex] reliability feedback unavailable",
                     exc_info=True)
    return out


# ── 结构化检索 ───────────────────────────────────────────────────────────


def check_preconditions(
    desc: CapabilityDescriptorV7,
    *,
    geometry: str = "",
    crs_class: str = "",
    feature_count: Optional[int] = None,
    available_fields: Iterable[str] = (),
) -> Tuple[bool, str]:
    """请求事实 → preconditions 硬检查（不满足 → (False, 原因)）。

    请求侧缺席（空串/None）→ 不约束（不猜测）；声明了前提而请求给了
    冲突事实 → 拒绝。"""
    g = (geometry or "").lower()
    if g and desc.geometry_requirements:
        if not any(g == str(r).lower() or str(r).lower() in g
                   for r in desc.geometry_requirements):
            return False, f"geometry:{g}!={desc.geometry_requirements[0]}"
    c = (crs_class or "").lower()
    if c and desc.crs_class:
        if c != desc.crs_class.lower():
            return False, f"crs_class:{c}!={desc.crs_class}"
    if (
        feature_count is not None
        and desc.min_features is not None
        and int(feature_count) < int(desc.min_features)
    ):
        return False, f"features:{feature_count}<{desc.min_features}"
    if available_fields and desc.required_fields:
        avail = {str(f).lower() for f in available_fields}
        missing = [f for f in desc.required_fields if f.lower() not in avail]
        if missing:
            return False, f"missing_fields:{','.join(missing[:3])}"
    return True, ""


def _tokenize(query: str) -> List[str]:
    """查询切词：空白切分 + CJK 段二元组（中文无空格 —— 二元组保证命中）。"""
    import re

    terms: List[str] = []
    for chunk in re.split(r"[\s，。；,;]+", (query or "").strip().lower()):
        if not chunk:
            continue
        if re.search(r"[\u4e00-\u9fff]", chunk) and len(chunk) > 2:
            terms.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        elif len(chunk) >= 2:
            terms.append(chunk)
    return terms


def select_capabilities(
    index: Dict[str, CapabilityDescriptorV7],
    query: str,
    *,
    geometry: str = "",
    crs_class: str = "",
    feature_count: Optional[int] = None,
    available_fields: Iterable[str] = (),
    reliability: Optional[Dict[str, Dict[str, int]]] = None,
    limit: int = MAX_RESULTS,
) -> List[Dict[str, Any]]:
    """结构化联合检索（确定性）：

    1. preconditions 硬过滤（冲突事实 → 剔除 + 原因披露）；
    2. 语料词法种子分（词边界命中；label 权重 > corpus）；
    3. 结构加成：cost 低档 / 确定性偏好（query 含「快速/cheap/fast」时）；
    4. 可靠性反馈：fail>0 线性罚分（有界）；
    5. tie-break by id（同输入同序）。
    """
    q = (query or "").strip().lower()
    terms = _tokenize(q)
    prefer_fast = any(w in q for w in ("fast", "quick", "快速", "轻量"))
    rel = reliability or {}
    scored: List[Tuple[float, str, List[str]]] = []
    for desc_id, desc in index.items():
        ok, reason = check_preconditions(
            desc, geometry=geometry, crs_class=crs_class,
            feature_count=feature_count, available_fields=available_fields)
        if not ok:
            continue
        if not terms:
            continue
        label = desc.label.lower()
        score = 0.0
        reasons: List[str] = []
        for term in terms:
            if term in label:
                score += 3.0
                reasons.append(f"label:{term}")
            elif term in desc.corpus_text:
                score += 1.0
                reasons.append(f"corpus:{term}")
        if score <= 0.0:
            continue
        if prefer_fast:
            cost_bonus = sum(
                -_COST_RANK.get(str(getattr(desc, field, "medium")), 1)
                for field in ("cpu_cost", "memory_cost")
            )
            score += 0.2 * cost_bonus
            reasons.append(f"cost_bias({cost_bonus})")
        # 可靠性罚分：desc.id（带/不带 kind 前缀）与关联工具名都可作键
        fails = 0
        bare_id = desc_id.split(":", 1)[-1]
        for key in (desc_id, bare_id):
            fails += int((rel.get(key) or {}).get("fail") or 0)
        rel_tools_fail = sum(
            int((rel.get(t) or {}).get("fail") or 0)
            for t in desc.related_tools[:6])
        penalty = 0.5 * min(fails + rel_tools_fail, 4)
        if penalty:
            score -= penalty
            reasons.append(f"reliability(-{penalty:.1f})")
        scored.append((round(score, 4), desc_id, reasons))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out: List[Dict[str, Any]] = []
    for score, desc_id, reasons in scored[:max(1, min(limit, MAX_RESULTS))]:
        desc = index[desc_id]
        out.append({
            "id": desc_id,
            "kind": desc.kind,
            "score": score,
            "reasons": reasons[:6],
            "fallback_available": list(desc.fallback_chain[:MAX_FALLBACK_CHAIN]),
            "postconditions": {
                "outputs": list(desc.output_artifact_types[:4]),
                "uncertainty": list(desc.uncertainty_outputs[:4]),
            },
        })
    return out


def _all_ids(registry: Any) -> List[str]:
    """registry.all_ids 兼容取值（property 或 method —— 两种历史形态）。"""
    ids = getattr(registry, "all_ids")
    return list(ids() if callable(ids) else ids)


_INDEX_CACHE: Optional[Dict[str, CapabilityDescriptorV7]] = None
_INDEX_CACHE_COMPLETE = False


def get_capability_index_cached() -> Dict[str, CapabilityDescriptorV7]:
    """进程级缓存（仅完整构建入缓存 —— 评审 F10：某段失败的残缺索引
    不缓存，下次触发重建以免终身残缺）。"""
    global _INDEX_CACHE, _INDEX_CACHE_COMPLETE
    if _INDEX_CACHE is None or not _INDEX_CACHE_COMPLETE:
        index, complete = _build_capability_index_state()
        if complete:
            _INDEX_CACHE = index
            _INDEX_CACHE_COMPLETE = True
        return index
    return _INDEX_CACHE


def reset_capability_index() -> None:
    """测试钩子（registry 重载 / reset_recipe_registry 同门）。"""
    global _INDEX_CACHE, _INDEX_CACHE_COMPLETE
    _INDEX_CACHE = None
    _INDEX_CACHE_COMPLETE = False


def descriptor_boosts(
    index: Dict[str, CapabilityDescriptorV7],
    query: str,
    *,
    limit: int = 8,
) -> Dict[str, float]:
    """描述符信号 → 工具加成映射（纯函数；评审 F11 抽出以便正路径测试）。

    首位命中候选的 related_tools 各 +0.25，逐位减半（下限 0.05）——
    「小幅」有界：远低于词法标签分（3.0/词），只影响并列区相对序。"""
    boosts: Dict[str, float] = {}
    weight = 0.25
    for pick in select_capabilities(index, query, limit=limit):
        desc = index.get(pick["id"])
        if desc is None:
            continue
        for tool in desc.related_tools[:2]:
            boosts[tool] = boosts.get(tool, 0.0) + weight
        weight = max(0.05, weight * 0.5)
    return {t: round(b, 4) for t, b in boosts.items() if b > 0}


__all__ = [
    "KIND_CAPABILITY",
    "KIND_ALGORITHM",
    "KIND_TEMPLATE",
    "KIND_COMPONENT",
    "CapabilityDescriptorV7",
    "build_capability_index",
    "check_preconditions",
    "select_capabilities",
    "reliability_from_ledger",
    "v7_capability_retrieval_enabled",
    "get_capability_index_cached",
    "reset_capability_index",
    "descriptor_boosts",
    "MAX_RESULTS",
]
