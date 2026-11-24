"""试验执行器（method-experiment WP3）—— 真实 dispatch 的试验循环。

执行真相（AWF executor 同款纪律）：每个试验经注入的 ``dispatch``
（生产 = 共享 ToolDispatchService）调用候选声明的评价工具——Pydantic
校验/别名归一/ref 解引用/指标/缓存/防御闸全继承，本层零旁路。

诚实语义：

- 工具 ``status!="ok"`` / 异常 → 试验 failed（typed reason_code），
  绝不冒充完成；其余试验继续（单候选失败不误报整体成功）；
- **复用**：试验内容 hash = 输入绑定 + 算法/参数 + seed + 协议指纹 +
  评价工具——hash 相同的已完成试验直接标 ``reused``（不重复 dispatch，
  不重复扣预算）；
- **取消**：``cancel_token`` 在试验间与 dispatch 前检查 → cancelled
  终态 + 已完成部分保留（resume 从 pending 继续）；
- **预算**：``max_trials_per_call`` / 墙钟护栏限制单次调用窗口——
  超窗返回且保留 pending（可续跑）；进度汇总为实际完成数与真实
  wall_ms，绝不虚构 ETA；
- 工具结果无协议所需指标 → ``not_evaluated``（不是 0 分，不是失败）。

family 适配器（纯函数）：从真实工具结果**提取**指标（缺键 = 缺事实 =
not_evaluated；绝不推算、绝不默认成功）。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pydantic import JsonValue

from app.services.gis_harness.analysis_workflows.contracts import canonical_json
from app.services.gis_harness.method_experiment.plan import (
    MethodExperimentPlan,
)
from app.services.gis_harness.method_experiment.state import (
    ExperimentRunState,
    TrialRecord,
)

logger = logging.getLogger(__name__)

#: 单次 run 调用窗口的默认试验数上限（有界纪律；工具层可调低）。
DEFAULT_MAX_TRIALS_PER_CALL = 6

#: 单次 run 调用墙钟护栏（秒）——超窗停发新试验（已完成部分保留）。
DEFAULT_WALL_GUARD_SECONDS = 240.0

#: diagnostics 键数上限（有界）。
MAX_DIAGNOSTIC_KEYS = 8

#: 错误消息截断（有界状态纪律）。
_MAX_MSG = 240

#: 单次工具输入行数预算默认（runner 构造；构造器可覆盖）。
_MAX_FEATURES_DEFAULT = 100_000


def trial_content_fingerprint(
    plan: MethodExperimentPlan, params: Dict[str, Any], seed: int,
    eval_tool: str,
) -> str:
    """试验内容 hash（输入版本+算法/参数/seed+协议+工具）。

    同名输出覆盖的避免靠内容寻址：输入 ref 换 revision、参数变、协议变
    → hash 变 → 不复用。
    """
    payload = {
        "input": plan.input.model_dump(),
        "protocol": plan.protocol.to_dict(),
        "family": plan.family_id,
        "params": params,
        "seed": seed,
        "tool": eval_tool,
    }
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def _as_float(value: Any) -> Optional[float]:
    """严格 float 提取（bool/str/NaN/inf → None——不猜数值）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    return None


def _payload_of(raw: Any) -> Dict[str, Any]:
    """工具原始结果 → dict（非 dict = 空投影——由 not_evaluated 兜底）。"""
    return raw if isinstance(raw, dict) else {}


# ── family 适配器：真实工具结果 → (metrics, basis, diagnostics) ─────────

def _extract_interpolation(raw: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """``interpolation_block_cv`` 结果 → CV 指标（缺键 = 未评估）。"""
    metrics: Dict[str, float] = {}
    basis: Dict[str, Dict[str, Any]] = {}
    for metric_id, key in (("cv_rmse", "rmse"), ("cv_mae", "mae")):
        v = _as_float(raw.get(key))
        if v is not None:
            metrics[metric_id] = round(v, 6)
    bias = _as_float(raw.get("bias"))
    if bias is not None:
        metrics["cv_abs_bias"] = round(abs(bias), 6)
    shared = {
        "sample_count": raw.get("n_samples"),
        "folds": raw.get("folds_used"),
        "scheme": raw.get("scheme"),
    }
    basis = {m: dict(shared) for m in metrics}
    diagnostics = {
        "folds_used": raw.get("folds_used"),
        "fold_failures": raw.get("fold_failures"),
        "skipped_reason": raw.get("skipped_reason"),
        "leakage_check": raw.get("leakage_check"),
    }
    return metrics, basis, {k: v for k, v in diagnostics.items() if v is not None}


def _extract_density(raw: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """``density_edge_diagnostic`` 结果 → 守恒/边界指标。"""
    metrics: Dict[str, float] = {}
    mass_err = _as_float(raw.get("mass_error_ratio"))
    if mass_err is not None:
        metrics["density_mass_error"] = round(mass_err, 6)
    edge = _as_float(raw.get("edge_mass_share"))
    if edge is not None:
        metrics["density_edge_share"] = round(edge, 6)
    basis = {m: {
        "n_points": raw.get("n_points"),
        "grid": raw.get("grid_facts"),
    } for m in metrics}
    diagnostics = {
        "integral_mass": raw.get("integral_mass"),
        "n_points": raw.get("n_points"),
        "bandwidth_effective": raw.get("bandwidth_effective"),
        "edge_note": raw.get("edge_note"),
    }
    return metrics, basis, {k: v for k, v in diagnostics.items() if v is not None}


def _extract_clustering(raw: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """``cluster_stability`` 结果 → 稳定性/轮廓/噪声指标。"""
    metrics: Dict[str, float] = {}
    sil = _as_float(raw.get("silhouette_mean"))
    if sil is not None:
        metrics["cluster_silhouette"] = round(sil, 6)
    ari = _as_float(raw.get("ari_mean"))
    if ari is not None:
        metrics["cluster_stability_ari"] = round(ari, 6)
    noise = _as_float(raw.get("noise_share"))
    if noise is not None:
        metrics["cluster_noise_share"] = round(noise, 6)
    basis = {m: {
        "n_samples": raw.get("n_samples"),
        "runs": raw.get("runs_used"),
    } for m in metrics}
    diagnostics = {
        "k_found": raw.get("k_found"),
        "ari_std": raw.get("ari_std"),
        "runs_used": raw.get("runs_used"),
        "note": raw.get("note"),
    }
    return metrics, basis, {k: v for k, v in diagnostics.items() if v is not None}


def _extract_network(raw: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """``isochrone_network`` 结果 → 覆盖指标。

    对齐工具真实输出（review C-P0 修正：此前读取不存在的 stats 键）：
    - 逐设施 feature 携带 ``properties.reachable``（bool）；
    - 工具层 stats：``facility_polygons``（保留数）、
      ``excluded_unreachable``（exclude_unreachable=True 时剔除数）、
      ``facilities_total``（输入设施总数——工具层 additive 补写）。
    覆盖率 = 可达保留设施 / 输入设施总数（脱离路网与剔除占位面都算
    不可达——同一口径下跨情景可比）。
    """
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    metrics: Dict[str, float] = {}
    basis: Dict[str, Dict[str, Any]] = {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    features = data.get("features") if isinstance(
        data.get("features"), list) else []
    reachable_kept = sum(
        1 for f in features
        if isinstance(f, dict)
        and isinstance(f.get("properties"), dict)
        and f["properties"].get("reachable") is True)
    total = _as_float(stats.get("facilities_total"))
    excluded = _as_float(stats.get("excluded_unreachable")) or 0.0
    if total is not None and total > 0:
        metrics["network_coverage"] = round(reachable_kept / total, 6)
        basis["network_coverage"] = {
            "reachable_kept": reachable_kept,
            "excluded_unreachable": excluded,
            "total": total,
            "note": "脱离路网(unsnappable)+剔除占位面都计入不可达",
        }
    diagnostics = {
        "facility_polygons": stats.get("facility_polygons"),
        "excluded_unreachable": excluded,
        "facilities_total": total,
        "mode": stats.get("mode"),
    }
    return metrics, basis, {k: v for k, v in diagnostics.items() if v is not None}


def _extract_autocorr(raw: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """``moran_i`` 结果 → 统计量/显著性（固定种子契约披露）。

    对齐工具真实输出（GeoAnalysisResult.to_llm_response）：指标在
    ``data`` 子 dict（moran_i/p_value/n_features/permutations/expected_i）。
    """
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    metrics: Dict[str, float] = {}
    stat = _as_float(data.get("moran_i"))
    if stat is not None:
        metrics["autocorr_statistic"] = round(stat, 6)
    p = _as_float(data.get("p_value"))
    if p is not None:
        metrics["autocorr_p_value"] = round(p, 6)
    basis = {m: {
        "n_features": data.get("n_features"),
        "permutations": data.get("permutations"),
        "seed": 42,   # 工具契约固定种子（可复现的诚实披露）
    } for m in metrics}
    diagnostics = {
        "pattern": data.get("pattern"),
        "expected_i": data.get("expected_i"),
    }
    return metrics, basis, {k: v for k, v in diagnostics.items() if v is not None}


#: 族 → 指标提取器（注册面；未知族 not_evaluated 兜底）。
METRIC_EXTRACTORS = {
    "interpolation": _extract_interpolation,
    "density": _extract_density,
    "clustering": _extract_clustering,
    "network_accessibility": _extract_network,
    "spatial_autocorrelation": _extract_autocorr,
}


def build_trial_args(
    plan: MethodExperimentPlan, params: Dict[str, Any], seed: int = 0,
    max_features: int = _MAX_FEATURES_DEFAULT,
) -> Dict[str, JsonValue]:
    """试验参数 → 评价工具入参。

    族分派按**评价工具的真实签名**构造（逐键与工具 schema 对齐——
    不给不接受这些参数的工具注入 max_features/geojson；review C-P0）。
    ref 直通——dispatch 层解引用。
    """
    family = plan.family_id
    protocol = plan.protocol
    args: Dict[str, JsonValue]
    if family == "interpolation":
        # interpolation_block_cv(geojson, value_field, method, folds,
        #   scheme, power, model, kernel, max_samples, max_features)
        args = {
            "geojson": plan.input.ref,
            "value_field": plan.input.value_field,
            "method": str(params.get("method", "idw")),
            "folds": protocol.folds,
            "scheme": protocol.scheme or "spatial_block",
            "max_samples": protocol.max_samples,
            "max_features": int(max_features),
        }
        for k in ("power", "model", "kernel"):
            if k in params:
                args[k] = params[k]
    elif family == "density":
        # density_edge_diagnostic(geojson, method, bandwidth_m, max_features)
        args = {
            "geojson": plan.input.ref,
            "method": str(params.get("method", "kde_fixed")),
            "max_features": int(max_features),
        }
        if "bandwidth_m" in params:
            args["bandwidth_m"] = params["bandwidth_m"]
    elif family == "clustering":
        # cluster_stability(geojson, method, k, eps_m, min_samples, runs,
        #   seed, value_field, max_features)
        args = {
            "geojson": plan.input.ref,
            "method": str(params.get("method", "kmeans")),
            "runs": protocol.stability_runs,
            "seed": int(seed) or 42,
            "max_features": int(max_features),
        }
        if "k" in params:
            args["k"] = params["k"]
        if "eps_m" in params:
            args["eps_m"] = params["eps_m"]
        if "min_samples" in params:
            args["min_samples"] = params["min_samples"]
        if plan.input.value_field:
            args["value_field"] = plan.input.value_field
    elif family == "network_accessibility":
        # isochrone_network(network_layer, facilities, travel_time, mode,
        #   exclude_unreachable)——无 geojson/max_features 形参（review C-P0）。
        args = {
            "network_layer": plan.input.extra_inputs.get("network", ""),
            "facilities": plan.input.extra_inputs.get("facilities", ""),
            "travel_time": float(params.get("travel_time", 15.0)),
            "mode": str(params.get("mode", "walking")),
            "exclude_unreachable": True,
        }
    elif family == "spatial_autocorrelation":
        # moran_i(geojson, value_field, weights_scheme, k, distance_band,
        #   permutations)——无 max_features 形参（review C-P0）。
        args = {
            "geojson": plan.input.ref,
            "value_field": plan.input.value_field,
            "weights_scheme": str(params.get("weights_scheme", "knn")),
        }
        if "k" in params:
            args["k"] = params["k"]
        if "distance_band" in params:
            args["distance_band"] = params["distance_band"]
    else:  # pragma: no cover — EXPERIMENT_FAMILY_IDS 词表守护
        args = {"geojson": plan.input.ref}
    return args


class ExperimentRunner:
    """试验循环（注入 dispatch/persist——与 AWF executor 同接缝形状）。"""

    def __init__(
        self,
        *,
        dispatch: Callable[[str, Dict[str, JsonValue], str], Awaitable[Any]],
        persist: Callable[[ExperimentRunState], Awaitable[None]],
        max_features: int = 100_000,
    ) -> None:
        self._dispatch = dispatch
        self._persist = persist
        self._max_features = max(1, min(int(max_features), 100_000))

    async def run(
        self,
        session_id: str,
        plan: MethodExperimentPlan,
        state: ExperimentRunState,
        *,
        only_candidates: Optional[List[str]] = None,
        max_trials_per_call: int = DEFAULT_MAX_TRIALS_PER_CALL,
        wall_guard_seconds: float = DEFAULT_WALL_GUARD_SECONDS,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> ExperimentRunState:
        """执行 pending 试验（预算/取消/复用/诚实结局），返回更新后的态。"""
        extract = METRIC_EXTRACTORS.get(plan.family_id)
        call_budget = max(1, min(int(max_trials_per_call), DEFAULT_MAX_TRIALS_PER_CALL))
        started = time.monotonic()
        dispatched = 0

        state.status = "running"
        state.touch()
        state.log("run_window_open", budget=call_budget)
        await self._persist(state)

        for trial in state.trials.values():
            if trial.state not in ("pending", "running"):
                continue
            if only_candidates and trial.candidate_id not in only_candidates:
                continue
            if dispatched >= call_budget:
                state.log("run_window_budget_pause", dispatched=dispatched)
                break
            if (time.monotonic() - started) > wall_guard_seconds:
                state.log("run_window_wall_pause",
                          wall_ms=int((time.monotonic() - started) * 1000))
                break
            if cancel_check is not None and cancel_check():
                trial.state = "cancelled"
                trial.reason_code = "MEXP_CANCELLED"
                trial.touch()
                state.log("cancelled", trial_id=trial.trial_id)
                state.status = "cancelled"
                await self._persist(state)
                return state

            plan_trial = self._plan_trial(plan, trial.trial_id)
            if plan_trial is None:
                trial.state = "skipped"
                trial.reason_code = "MEXP_TRIAL_NOT_IN_PLAN"
                trial.touch()
                continue

            # ── 内容寻址复用 ────────────────────────────────────────────
            fp = trial_content_fingerprint(
                plan, plan_trial.params, plan_trial.seed, plan_trial.eval_tool)
            trial.content_fingerprint = fp
            reused = self._find_reusable(state, fp, trial.trial_id)
            if reused is not None:
                trial.state = "reused"
                trial.metrics = dict(reused.metrics)
                trial.metric_basis = dict(reused.metric_basis)
                trial.diagnostics = dict(reused.diagnostics)
                trial.reason_code = ""
                trial.message = f"复用 {reused.trial_id}（内容指纹一致）"
                trial.touch()
                state.log("trial_reused", trial_id=trial.trial_id,
                          source=reused.trial_id)
                await self._persist(state)
                continue

            # ── 真实 dispatch ───────────────────────────────────────────
            args = build_trial_args(
                plan, plan_trial.params, seed=plan_trial.seed,
                max_features=self._max_features)
            trial.state = "running"
            trial.attempts += 1
            trial.touch()
            state.log("trial_dispatch", trial_id=trial.trial_id,
                      tool=plan_trial.eval_tool)
            await self._persist(state)

            dispatched += 1               # 窗口预算按 dispatch 次数计（失败同扣——
            # 快速失败不得绕过 max_trials_per_call；review C-P1）
            t0 = time.monotonic()
            try:
                result = await self._dispatch(
                    plan_trial.eval_tool, args, session_id)
            except Exception as exc:  # noqa: BLE001 — 工具异常 = 该试验失败
                trial.state = "failed"
                trial.reason_code = "MEXP_DISPATCH_EXCEPTION"
                trial.message = f"{type(exc).__name__}: {exc}"[:_MAX_MSG]
                trial.wall_ms = int((time.monotonic() - t0) * 1000)
                trial.touch()
                state.log("trial_exception", trial_id=trial.trial_id)
                await self._persist(state)
                continue
            trial.wall_ms = int((time.monotonic() - t0) * 1000)

            status = getattr(result, "status", None)
            if status != "ok":
                trial.state = "failed"
                trial.reason_code = f"MEXP_TOOL_{str(status or 'error').upper()}"
                trial.message = str(getattr(result, "error_msg", "") or "")[:_MAX_MSG]
                trial.touch()
                state.log("trial_tool_error", trial_id=trial.trial_id)
                await self._persist(state)
                continue

            raw = _payload_of(getattr(result, "raw_result", None))
            if raw.get("success") is False:
                trial.state = "failed"
                trial.reason_code = "MEXP_TOOL_REPORTED_FAILURE"
                trial.message = str(raw.get("summary") or raw.get("message") or "")[:_MAX_MSG]
                trial.touch()
                state.log("trial_reported_failure", trial_id=trial.trial_id)
                await self._persist(state)
                continue

            if extract is None:
                trial.state = "not_evaluated"
                trial.reason_code = "MEXP_NO_EXTRACTOR"
                trial.message = f"族 {plan.family_id} 无指标提取适配器"
                trial.touch()
                await self._persist(state)
                continue

            metrics, basis, diagnostics = extract(raw)
            if not metrics:
                trial.state = "not_evaluated"
                trial.reason_code = "MEXP_METRICS_ABSENT"
                trial.message = "工具结果不含协议所需指标（未评估≠零分）"
                trial.diagnostics = {
                    k: v for k, v in list(diagnostics.items())[:MAX_DIAGNOSTIC_KEYS]}
                trial.touch()
                await self._persist(state)
                continue

            # 协议裁剪：只保留协议声明的指标（工具多给的事实进 diagnostics）
            allowed = set(plan.protocol.metrics)
            trial.metrics = {m: v for m, v in metrics.items() if m in allowed}
            trial.metric_basis = {
                m: _bounded_basis(b) for m, b in basis.items() if m in allowed}
            extra_metrics = {
                m: v for m, v in metrics.items() if m not in allowed}
            trial.diagnostics = {
                k: v for k, v in list(diagnostics.items())[:MAX_DIAGNOSTIC_KEYS]}
            if extra_metrics:
                trial.diagnostics["extra_metrics_discarded"] = sorted(extra_metrics)[:4]
            trial.state = "completed"
            trial.reason_code = ""
            trial.message = ""
            trial.touch()
            state.log("trial_completed", trial_id=trial.trial_id,
                      metrics=len(trial.metrics))
            await self._persist(state)

        # ── 窗口收口：诚实整体状态 ──────────────────────────────────────
        # cancelled 不是终局僵局：取消后剩余续跑完成 → 实验整体 completed
        # （取消计数诚实保留在 counts 里；review C-P1）。
        counts = state.counts()
        if counts.get("pending", 0) or counts.get("running", 0):
            state.status = "partial" if (
                counts.get("completed", 0) or counts.get("reused", 0)) else "running"
            if state.status == "running" and dispatched >= call_budget:
                state.status = "partial"   # 预算暂停也是部分（可续跑）
        elif counts.get("completed", 0) or counts.get("reused", 0):
            state.status = "completed"
        elif counts.get("failed", 0):
            state.status = "failed"
        elif counts.get("cancelled", 0):
            state.status = "cancelled"
        else:
            state.status = "completed"
        state.touch()
        state.log("run_window_close", status=state.status, counts=state.counts())
        await self._persist(state)
        return state

    def _plan_trial(self, plan: MethodExperimentPlan, trial_id: str):
        for t in plan.trials:
            if t.trial_id == trial_id:
                return t
        return None

    def _find_reusable(
        self, state: ExperimentRunState, fingerprint: str, exclude_trial: str,
    ) -> Optional[TrialRecord]:
        """同内容指纹的已完成试验（completed 优先；跳过自身）。"""
        for other in state.trials.values():
            if other.trial_id == exclude_trial:
                continue
            if other.state == "completed" and other.content_fingerprint == fingerprint:
                return other
        return None


def _bounded_basis(basis: Dict[str, Any]) -> Dict[str, Any]:
    """计算依据有界化（≤6 键，值截断——大数组不进状态）。"""
    out: Dict[str, Any] = {}
    for k, v in list(basis.items())[:6]:
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        elif isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in list(v.items())[:4]}
        else:
            out[k] = str(v)[:80]
    return out
