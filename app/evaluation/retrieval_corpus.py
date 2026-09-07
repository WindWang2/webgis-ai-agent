"""Tool Retrieval V4 离线语料（ADR-0104 决策 5 / audit gap #10）。

**确定性 Python 生成器**（与 golden/case_matrix/conformance 同家族 —— 表驱动
× 有界扩展，非 JSON 大 blob、无 LLM、无时间戳、无随机）：

    get_retrieval_corpus() -> List[ToolRetrievalCase]   # 全量（≥2000 契约）
    get_retrieval_sample(n) -> List[ToolRetrievalCase]  # 确定性 stride 子样
    expected_tools_for(case, registry) -> Tuple[str, ...]  # 单一真相反查

三层来源（全部既有真相，不引入第二标注）：

- layer A ``intent-306``：golden_cases（G1–G33）+ case_matrix（273）逐条
  直取 —— query + expected_capabilities 原样保留；
- layer B ``intent-paraphrase``：对 layer A 中带 capability 标注的案例做
  确定性中文口语包装（3 模板，取自 conformance UTTERANCE_VARIANTS 已
  核实不携带任务语义的同款句式）；
- layer C ``conformance-direct``：conformance 家族表 × 12 scope × 直陈句式
  （utterance=direct）—— 复用 build_conformance_corpus() 生成的 query 与
  capability 标注，只取 id 以 ``-direct`` 结尾的确定性子集。

期望工具集不在本模块内标注：经 ``expected_tools_for`` → AlgorithmRegistry
``capability_tool_map`` 反查 + registry 可见性/tier 过滤（与
runtime_metrics.relevant_tools_for_case 同一真相，绝不复制 GIS 语义）。

**度量语义（review R3 MAJOR 诚实定界）**：期望工具与选择器共享
capability→tool 投影，因此本语料度量的是「capability 标注 → 工具面
投影完备性」（recall@30 ≈ 投影完整度、tier-3 泄漏 = 0、schema 字节
预算），**不是**开环 query→工具检索质量 —— 后者需要独立于选择器
映射的人工标注（future work）。recall@5 下限是实测保守钉值，防投影
退化；勿将其解读为语义检索精度。

规模契约：全量 ≥ MIN_CORPUS_SIZE（2000）；同构建必同输出；id 全局唯一。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

MIN_CORPUS_SIZE = 2000

#: layer B 的确定性口语包装模板（与 conformance UTTERANCE_VARIANTS 同款、
#: 已核实不携带任务语义；{q} = 原查询）。
_PARAPHRASE_TEMPLATES: Tuple[Tuple[str, str], ...] = (
    ("帮我看看{q}", "colloquial-show"),
    ("请分析{q}", "analyze"),
    ("{q}的情况", "situation"),
)


@dataclass(frozen=True)
class ToolRetrievalCase:
    """一条 query → 期望工具集检索案例（期望工具经 capability 反查解析）。"""

    case_id: str
    query: str
    expected_capabilities: Tuple[str, ...]
    source: str          # intent-306 | intent-paraphrase | conformance-direct
    variant: str = "direct"

    def as_metrics_case(self) -> Any:
        """retrieval_metrics / surface_retrieval_report 兼容视图（attrs）。"""
        return self


def expected_tools_for(case: ToolRetrievalCase, registry: Any) -> Tuple[str, ...]:
    """case → 相关工具集（AlgorithmRegistry 反查 + registry 可见/tier 过滤）。

    单一真相：与 runtime_metrics.relevant_tools_for_case 同门。
    """
    from app.evaluation.runtime_metrics import relevant_tools_for_case

    return tuple(relevant_tools_for_case(registry, case.expected_capabilities))


# ---------------------------------------------------------------------------
# 确定性构建
# ---------------------------------------------------------------------------

def _intent_cases() -> List[ToolRetrievalCase]:
    """layer A：306 意图语料直取（golden + matrix）。"""
    from app.evaluation.case_matrix import build_matrix_cases
    from app.evaluation.golden_cases import GOLDEN_CASES

    out: List[ToolRetrievalCase] = []
    for c in [*GOLDEN_CASES, *build_matrix_cases()]:
        caps = tuple(c.expected_capabilities or ())
        if not caps:
            continue  # 无 capability 标注 → 期望工具集不可反查，诚实跳过
        out.append(ToolRetrievalCase(
            case_id=f"TR-INTENT-{c.id}",
            query=c.query,
            expected_capabilities=caps,
            source="intent-306",
        ))
    return out


def _paraphrase_cases(base: List[ToolRetrievalCase]) -> List[ToolRetrievalCase]:
    """layer B：layer A 案例的确定性口语包装（task-neutral 模板）。"""
    out: List[ToolRetrievalCase] = []
    for c in base:
        for tpl, tag in _PARAPHRASE_TEMPLATES:
            out.append(ToolRetrievalCase(
                case_id=f"TR-PARA-{tag}-{c.case_id}",
                query=tpl.replace("{q}", c.query),
                expected_capabilities=c.expected_capabilities,
                source="intent-paraphrase",
                variant=tag,
            ))
    return out


def _conformance_direct_cases() -> List[ToolRetrievalCase]:
    """layer C：conformance 家族 × scope × 直陈句式（id 以 -direct 结尾）。"""
    from app.evaluation.conformance import build_conformance_corpus

    out: List[ToolRetrievalCase] = []
    for c in build_conformance_corpus():
        if not c.id.endswith("-direct"):
            continue  # 只取直陈句式（确定性子集；其余句式留全量语料族）
        caps = tuple(c.expected_capabilities or ())
        if not caps:
            continue
        out.append(ToolRetrievalCase(
            case_id=f"TR-CF-{c.id}",
            query=c.query,
            expected_capabilities=caps,
            source="conformance-direct",
        ))
    return out


def _build_sorted(cases: List[ToolRetrievalCase]) -> List[ToolRetrievalCase]:
    cases.sort(key=lambda c: c.case_id)
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        seen = set()
        dup = [i for i in ids if i in seen or seen.add(i)]  # type: ignore[func-configuration-value]
        raise AssertionError(f"retrieval corpus has duplicate ids: {dup[:5]}")
    return cases


def get_retrieval_corpus() -> List[ToolRetrievalCase]:
    """全量确定性检索语料（≥ MIN_CORPUS_SIZE；同输入必同输出）。"""
    intent = _intent_cases()
    corpus = [*intent, *_paraphrase_cases(intent), *_conformance_direct_cases()]
    return _build_sorted(corpus)


def get_retrieval_sample(sample_n: int = 800) -> List[ToolRetrievalCase]:
    """确定性 stride 子样（门测试跑 select 指标用；覆盖全部三层来源）。

    stride = ceil(total / n)，从 0 起等步长取点 —— 无随机、跨进程一致。
    """
    corpus = get_retrieval_corpus()
    total = len(corpus)
    if total <= sample_n:
        return list(corpus)
    stride = -(-total // max(1, sample_n))  # ceil division
    return corpus[::stride][:sample_n]


def corpus_source_counts(corpus: List[ToolRetrievalCase]) -> dict:
    """按来源统计（语料构成披露，测试断言面）。"""
    counts: dict = {}
    for c in corpus:
        counts[c.source] = counts.get(c.source, 0) + 1
    return counts
