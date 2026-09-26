"""Scale Matrix — 规模化验证矩阵（V11 W8，ADR-0168）。

任务书 W8.1：「验证矩阵：17 图型 × 12 数据态 × 2 语言 × 4 输出形态 = **1632
组**；按优先级分批（先核心 **408 组**），每组产出
``{quality_metrics, cost_tokens, cost_ms, artifacts}``」。

轴（全部复用既有单点词表，不新造）：

- 图型：``closed_loop_corpus.MAP_TYPE_IDS``（17）；
- 数据态：``quality_corpus.DATA_STATE_PROFILES``（12）；
- 语言：zh / en（请求语言面）；
- 输出形态：map / svg / pdf / print（C4 的产物维度）。

组合编号（确定性）：``core`` = 17 图型 × 12 数据态 × 2 语言（×1 形态 =
map）→ **408**；full 再 ×4 形态 → **1632**。

每组执行（确定性、无网络、无浏览器）：数据态事实 → ``SymbologyProfile``
合成 → ``resolve_symbology`` 裁决 + ``LabelPlan`` 策略 + 版面五维评分 →
quality_metrics（k / palette / confidence / 颜色可分辨 / 版面总分）+ cost_ms
（本机计时）+ cost_tokens（**0 并显式标注 `llmUsed: false`** —— 无 LLM
通道时诚实记 0 而非伪造）+ artifacts（决策工件摘要有界）。

成本治理（W8.5）：``COST_BUDGET_MS`` 分图型阈值；超预算行进入
``budget_alerts``（告警不拦截 —— 首轮 provisional）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

OUTPUT_FORMS = ("map", "svg", "pdf", "print")
LANGUAGES = ("zh", "en")

#: 分图型耗时预算（ms；首轮 provisional —— W8 实测分布校准前只告警）。
COST_BUDGET_MS: Dict[str, int] = {}
DEFAULT_COST_BUDGET_MS = 800


@dataclass(frozen=True)
class MatrixCombo:
    combo_id: str
    map_type: str
    data_state: str
    language: str
    output_form: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "comboId": self.combo_id, "mapType": self.map_type,
            "dataState": self.data_state, "language": self.language,
            "outputForm": self.output_form,
        }


def _map_types() -> Tuple[str, ...]:
    from app.evaluation.closed_loop_corpus import MAP_TYPE_IDS

    return tuple(MAP_TYPE_IDS)


def _state_ids() -> Tuple[str, ...]:
    from app.evaluation.quality_corpus import DATA_STATE_PROFILES

    return tuple(s.state_id for s in DATA_STATE_PROFILES)


def build_matrix_combos(
    *, tier: str = "core",
    map_types: Optional[Sequence[str]] = None,
    data_states: Optional[Sequence[str]] = None,
    languages: Sequence[str] = LANGUAGES,
    output_forms: Sequence[str] = ("map",),
) -> List[MatrixCombo]:
    """组合构建（确定性序：图型 → 数据态 → 语言 → 形态）。

    ``tier="core"``：默认 17 × 12 × 2 × 1 = 408；
    ``tier="full"``：默认 17 × 12 × 2 × 4 = 1632（output_forms 全量）。
    """
    mts = tuple(map_types) if map_types is not None else _map_types()
    states = tuple(data_states) if data_states is not None else _state_ids()
    forms = OUTPUT_FORMS if tier == "full" else tuple(output_forms)
    combos: List[MatrixCombo] = []
    for mt in mts:
        for st in states:
            for lang in languages:
                for form in forms:
                    combos.append(MatrixCombo(
                        combo_id=f"{mt}|{st}|{lang}|{form}",
                        map_type=mt, data_state=st, language=lang,
                        output_form=form,
                    ))
    return combos


def _state_facts(state_id: str) -> Dict[str, Any]:
    from app.evaluation.quality_corpus import DATA_STATE_PROFILES

    for s in DATA_STATE_PROFILES:
        if s.state_id == state_id:
            return dict(s.facts)
    return {}


def _synthetic_values(state_id: str) -> List[float]:
    """数据态 → 合成数值场（确定性；零方差/样本不足等态如实合成）。"""
    if state_id == "zero_variance":
        return [7.0] * 30
    if state_id == "too_few_samples":
        return [1.0, 2.0, 3.0]
    if state_id == "empty_dataset":
        return []
    if state_id == "high_null_ratio":
        return [float(i) for i in range(1, 21)]
    return [float((i * 37) % 101) for i in range(1, 61)]  # 确定性伪分布


def run_combo(combo: MatrixCombo) -> Dict[str, Any]:
    """单组执行（确定性；无网络）。返回
    ``{metrics, cost_tokens, cost_ms, artifacts, alerts}``。"""
    started = time.perf_counter()
    facts = _state_facts(combo.data_state)
    values = _synthetic_values(combo.data_state)

    # resolve_symbology 是 profile 入口；组合场景走便捷封装（同裁决面）
    from app.lib.cartography.symbology import symbology_decision_from_values

    # F10（M1 调用点迁移）：语义统一推导以 map_type 为字段名（合成值全正、
    # 词素面中性 → 判定零漂移；uncertainty 型语义如实入档 artifacts）。
    _sem = None
    try:
        from app.lib.cartography.semantic_inputs import derive_semantic_inputs
        _sem = derive_semantic_inputs(combo.map_type, value_samples=values)
    except Exception:  # noqa: BLE001 - 语义推导不阻断矩阵
        _sem = None
    decision = symbology_decision_from_values(
        values=values,
        context="screen",
        data_kind=(
            _sem.data_kind if _sem is not None and _sem.data_kind
            else "sequential"
        ),
        measurement_kind=(
            (_sem.contract_measurement_kind or None) if _sem is not None else None
        ),
        requested_k=None,
    )
    from app.lib.cartography.layout_score import score_layout

    layout = score_layout([
        {"type": "legend", "placement": {"anchor": "top-right"}},
        {"type": "scale_bar", "position": "bottom-left"},
        {"type": "title", "position": "top-left"},
    ])
    cost_ms = int((time.perf_counter() - started) * 1000)
    metrics = {
        "carto.symbology.k": decision.k,
        "carto.symbology.confidence": decision.confidence,
        "carto.symbology.low_confidence": 1.0 if decision.low_confidence else 0.0,
        "carto.layout.overall": layout["overall"],
        "carto.matrix.values_n": len(values),
    }
    budget = COST_BUDGET_MS.get(combo.map_type, DEFAULT_COST_BUDGET_MS)
    alerts = []
    if cost_ms > budget:
        alerts.append({
            "comboId": combo.combo_id, "costMs": cost_ms, "budgetMs": budget,
        })
    return {
        "combo": combo.to_dict(),
        "factsGeometry": (facts.get("geometryTypes") or ["unknown"])[0],
        "qualityMetrics": metrics,
        "costTokens": 0,          # 无 LLM 通道：诚实 0（llmUsed=false）
        "llmUsed": False,
        "costMs": cost_ms,
        "artifacts": {
            "symbology": decision.model_dump(),
            "layoutScore": layout,
            # F10：语义推导工件入档（测量语义可对账；失败如实 None）。
            "semanticInputs": (
                _sem.to_bounded_dict() if _sem is not None else None
            ),
        },
        "budgetAlerts": alerts,
    }


def run_matrix(
    combos: Iterable[MatrixCombo], *, limit: Optional[int] = None,
) -> Dict[str, Any]:
    """批量执行（串行；确定性序）。返回汇总 + 逐组结果（有界）。"""
    results: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    started = time.perf_counter()
    for i, combo in enumerate(combos):
        if limit is not None and i >= limit:
            break
        row = run_combo(combo)
        alerts.extend(row.pop("budgetAlerts"))
        results.append(row)
    total_ms = int((time.perf_counter() - started) * 1000)
    token_sum = sum(r["costTokens"] for r in results)
    return {
        "version": 1,
        "executed": len(results),
        "totalCostMs": total_ms,
        "totalCostTokens": token_sum,
        "llmUsed": any(r["llmUsed"] for r in results),
        "budgetAlerts": alerts[:50],
        "results": results,
    }




def matrix_results_to_observations(
    matrix_output: Dict[str, Any], *, wave: str = "W8",
) -> List[Dict[str, Any]]:
    """矩阵结果 → ratchet 观测行（图型 × 检查项 × 波次）。

    scene_id = 图型 id；check_id = 度量键；value = 数值；wave = 波次标签
    （C4 扩展维度，``aggregate_observations_by_wave`` 消费）。
    """
    rows: List[Dict[str, Any]] = []
    for result in matrix_output.get("results") or []:
        combo = result.get("combo") or {}
        map_type = str(combo.get("mapType") or "")
        for check_id, value in (result.get("qualityMetrics") or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            rows.append({
                "scene_id": map_type,
                "check_id": check_id,
                "value": float(value),
                "wave": wave,
                "combo_id": combo.get("comboId", ""),
            })
    return rows


__all__ = [
    "OUTPUT_FORMS", "LANGUAGES", "COST_BUDGET_MS", "DEFAULT_COST_BUDGET_MS",
    "MatrixCombo", "build_matrix_combos", "run_combo", "run_matrix",
    "matrix_results_to_observations",
]
