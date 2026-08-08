"""
Metric Evaluator V2.
Evaluates quantitative baseline & simulated metrics, propagates uncertainty ranges (MetricRange),
integrates rule interval bounds, and calculates overall decision confidence.
"""
from typing import Any, Dict, List, Optional, Tuple
from app.services.spatial_decision.models import (
    MetricDeltaV2,
    MetricRange,
    DomainRule,
    EvidenceItem,
)


class MetricEvaluator:
    """Evaluates spatial decision metrics, uncertainty propagation, and decision confidence."""

    def extract_rule_interval(
        self,
        rule: Optional[DomainRule] = None,
        custom_pct: Optional[Tuple[float, float, float]] = None,
        custom_abs: Optional[Tuple[float, float, float]] = None,
    ) -> Tuple[Optional[str], Tuple[float, float, float]]:
        """
        Extracts interval range (min, expected, max) from rule or custom parameters.
        
        Returns:
            Tuple[str (type: 'pct'|'abs'), Tuple[float, float, float]]
        """
        if custom_pct is not None:
            return "pct", custom_pct
        if custom_abs is not None:
            return "abs", custom_abs

        if not rule or not rule.parameters:
            return "pct", (0.0, 0.0, 0.0)

        params = rule.parameters

        # Check percentage parameter keys
        if "pct_change_min" in params or "pct_change_expected" in params or "pct_change_max" in params:
            p_min = params.get("pct_change_min", params.get("pct_change_expected", 0.0))
            p_exp = params.get("pct_change_expected", (p_min + params.get("pct_change_max", p_min)) / 2.0)
            p_max = params.get("pct_change_max", p_exp)
            return "pct", (float(p_min), float(p_exp), float(p_max))

        if "pct_range" in params:
            rng = params["pct_range"]
            if len(rng) == 2:
                return "pct", (float(rng[0]), float(rng[0] + rng[1]) / 2.0, float(rng[1]))
            elif len(rng) >= 3:
                return "pct", (float(rng[0]), float(rng[1]), float(rng[2]))

        # Check absolute delta parameter keys
        if "delta_min" in params or "delta_expected" in params or "delta_max" in params:
            d_min = params.get("delta_min", params.get("delta_expected", 0.0))
            d_exp = params.get("delta_expected", (d_min + params.get("delta_max", d_min)) / 2.0)
            d_max = params.get("delta_max", d_exp)
            return "abs", (float(d_min), float(d_exp), float(d_max))

        if "delta_range" in params:
            rng = params["delta_range"]
            if len(rng) == 2:
                return "abs", (float(rng[0]), float(rng[0] + rng[1]) / 2.0, float(rng[1]))
            elif len(rng) >= 3:
                return "abs", (float(rng[0]), float(rng[1]), float(rng[2]))

        return "pct", (0.0, 0.0, 0.0)

    def evaluate_metric(
        self,
        metric_key: str,
        metric_name: str,
        baseline: float,
        rule: Optional[DomainRule] = None,
        evidence: Optional[List[EvidenceItem]] = None,
        unit: str = "",
        custom_pct: Optional[Tuple[float, float, float]] = None,
        custom_delta_pct: Optional[Tuple[float, float, float]] = None,
        custom_delta_abs: Optional[Tuple[float, float, float]] = None,
        missing_baseline: bool = False,
        evidence_gap_note: str = "",
    ) -> MetricDeltaV2:
        """
        Calculates baseline, simulated expected, absolute delta, percentage delta, and MetricRange.
        """
        if custom_pct is None and custom_delta_pct is not None:
            custom_pct = custom_delta_pct

        interval_type, (val_min, val_exp, val_max) = self.extract_rule_interval(
            rule=rule, custom_pct=custom_pct, custom_abs=custom_delta_abs
        )

        if interval_type == "pct":
            # val_min, val_exp, val_max are relative multipliers or percentages e.g. 0.15, 0.20, 0.25
            min_val = baseline * (1.0 + val_min)
            expected_val = baseline * (1.0 + val_exp)
            max_val = baseline * (1.0 + val_max)
            delta_abs = expected_val - baseline
            delta_pct = val_exp * 100.0 if baseline != 0.0 else val_exp * 100.0
        else:
            # interval_type == "abs": val_min, val_exp, val_max are direct deltas
            min_val = baseline + val_min
            expected_val = baseline + val_exp
            max_val = baseline + val_max
            delta_abs = val_exp
            delta_pct = (delta_abs / baseline * 100.0) if baseline != 0.0 else 0.0

        metric_range = MetricRange(
            min_val=round(min_val, 4),
            expected_val=round(expected_val, 4),
            max_val=round(max_val, 4),
        )

        gap_note = None
        if missing_baseline:
            # Prefer the caller-supplied evidence_gap_note (e.g. the rule-pack's
            # specific "未找到实测基线数据…" or "Missing baseline for <key>" detail)
            # over the generic fallback. The parameter was previously accepted
            # but never read, so callers' notes were silently discarded.
            gap_note = evidence_gap_note or (
                f"Baseline for '{metric_name}' was missing or estimated from default models."
            )

        return MetricDeltaV2(
            metric_key=metric_key,
            metric_name=metric_name,
            baseline=round(baseline, 4),
            simulated=round(expected_val, 4),
            delta_abs=round(delta_abs, 4),
            delta_pct=round(delta_pct, 4),
            range=metric_range,
            unit=unit,
            missing_baseline=missing_baseline,
            evidence_gap_note=gap_note,
        )

    def calculate_decision_confidence(
        self,
        evidence_chain: Optional[List[EvidenceItem]] = None,
        baseline_available: bool = True,
        rules_applied: Optional[List[DomainRule]] = None,
        target_resolution_confidence: float = 1.0,
        baseline_deltas: Optional[Dict[str, MetricDeltaV2]] = None,
        **kwargs,
    ) -> float:
        """
        Calculates overall decision confidence score in [0.0, 1.0] based on
        evidence completeness, baseline resolution, and rule reliability.
        """
        if evidence_chain is None:
            evidence_chain = []
        if rules_applied is None:
            rules_applied = []
        if baseline_deltas is not None:
            baseline_available = not any(m.missing_baseline for m in baseline_deltas.values())

        # 1. Baseline resolution score
        baseline_score = target_resolution_confidence * (1.0 if baseline_available else 0.5)

        # 2. Evidence completeness score
        if evidence_chain:
            avg_ev_conf = sum(e.confidence for e in evidence_chain) / len(evidence_chain)
            evidence_score = min(1.0, avg_ev_conf * (0.8 + 0.05 * min(len(evidence_chain), 4)))
        else:
            evidence_score = 0.6

        # 3. Rule reliability score
        if rules_applied:
            rule_score = sum(r.confidence for r in rules_applied) / len(rules_applied)
        else:
            rule_score = 0.75

        # Weighted combination
        w_baseline = 0.35
        w_evidence = 0.35
        w_rules = 0.30

        total_conf = (w_baseline * baseline_score) + (w_evidence * evidence_score) + (w_rules * rule_score)
        return float(max(0.0, min(1.0, round(total_conf, 4))))

    def generate_uncertainty_description(
        self,
        metric_deltas: Dict[str, MetricDeltaV2],
        overall_confidence: float,
    ) -> str:
        """Generates detailed human-readable uncertainty analysis description."""
        lines = [f"### Overall Decision Confidence: {overall_confidence * 100:.1f}%"]

        missing_baselines = [m for m in metric_deltas.values() if m.missing_baseline]
        if missing_baselines:
            lines.append("\n**Baseline Gaps Identified:**")
            for m in missing_baselines:
                lines.append(f"- {m.metric_name}: {m.evidence_gap_note or 'Baseline missing.'}")
        else:
            lines.append("\n- All baseline metrics resolved with high empirical fidelity.")

        lines.append("\n**Evaluated Metric Ranges (Uncertainty Propagation):**")
        for m in metric_deltas.values():
            if m.range:
                lines.append(
                    f"- {m.metric_name} ({m.unit}): Baseline = {m.baseline}, Expected = {m.simulated} "
                    f"[Min: {m.range.min_val}, Max: {m.range.max_val}] (Δ: {m.delta_pct:+.1f}%)"
                )
            else:
                lines.append(f"- {m.metric_name} ({m.unit}): Baseline = {m.baseline}, Simulated = {m.simulated}")

        return "\n".join(lines)

    def evaluate(
        self,
        scenario_type: str,
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
        parameters: Dict[str, Any],
        target_area: Any,
        evidence_chain: List[EvidenceItem],
    ) -> Tuple[Dict[str, MetricDeltaV2], List[str], float, str]:
        """
        Unified evaluation facade.
        Evaluates metrics, propagates uncertainty ranges, determines confidence, and outputs uncertainty notes.
        """
        # Determine scenario metrics schema
        scenario_metric_keys = {
            "subway": ["housing_price", "rent", "commute_time", "commercial_vitality"],
            "school": ["housing_price", "education_access", "rent"],
            "hospital": ["housing_price", "medical_access"],
            "park": ["housing_price", "living_quality"],
            "population_growth": ["housing_demand", "traffic_load", "school_demand", "hospital_demand"],
            "traffic_restriction": ["road_saturation", "public_transit_usage", "commute_time", "air_quality"],
        }.get(scenario_type, ["housing_price", "living_quality"])

        # Default realistic baseline values if not present in baseline_metrics
        default_baselines = {
            "housing_price": (45000.0, "RMB/m2", "Housing Price"),
            "rent": (120.0, "RMB/m2/month", "Rent Price"),
            "commute_time": (45.0, "min", "Commute Time"),
            "commercial_vitality": (75.0, "pts", "Commercial Vitality Index"),
            "education_access": (60.0, "pts", "Education Access Index"),
            "medical_access": (55.0, "pts", "Medical Access Index"),
            "living_quality": (70.0, "pts", "Living Quality Index"),
            "housing_demand": (100.0, "idx", "Housing Demand Index"),
            "traffic_load": (80.0, "idx", "Traffic Load Index"),
            "school_demand": (100.0, "idx", "School Demand Index"),
            "hospital_demand": (100.0, "idx", "Hospital Demand Index"),
            "road_saturation": (0.85, "ratio", "Road Saturation Ratio"),
            "public_transit_usage": (35.0, "%", "Public Transit Share"),
            "air_quality": (75.0, "AQI", "Air Quality Index"),
        }

        # Default deltas per scenario type
        default_pct_ranges = {
            "subway": {
                "housing_price": (0.15, 0.20, 0.25),
                "rent": (0.10, 0.14, 0.18),
                "commute_time": (-0.15, -0.10, -0.05),
                "commercial_vitality": (0.20, 0.30, 0.40),
            },
            "school": {
                "housing_price": (0.08, 0.115, 0.15),
                "education_access": (0.30, 0.40, 0.50),
                "rent": (0.05, 0.085, 0.12),
            },
            "hospital": {
                "housing_price": (0.05, 0.075, 0.10),
                "medical_access": (0.40, 0.50, 0.60),
            },
            "park": {
                "housing_price": (0.05, 0.075, 0.10),
                "living_quality": (0.15, 0.20, 0.25),
            },
            "population_growth": {
                "housing_demand": (0.08, 0.10, 0.12),
                "traffic_load": (0.10, 0.125, 0.15),
                "school_demand": (0.10, 0.125, 0.15),
                "hospital_demand": (0.05, 0.075, 0.10),
            },
            "traffic_restriction": {
                "road_saturation": (-0.20, -0.15, -0.10),
                "public_transit_usage": (0.15, 0.225, 0.30),
                "commute_time": (0.05, 0.10, 0.15),
                "air_quality": (0.05, 0.10, 0.15),
            },
        }.get(scenario_type, {})

        evaluated_metrics: Dict[str, MetricDeltaV2] = {}
        assumptions: List[str] = []

        for m_key in scenario_metric_keys:
            # Check if baseline provided
            base_val = 0.0
            base_unit = ""
            base_name = ""
            is_missing = False
            gap_note = None

            if m_key in baseline_metrics and not baseline_metrics[m_key].missing_baseline:
                bm = baseline_metrics[m_key]
                base_val = bm.baseline
                base_unit = bm.unit
                base_name = bm.metric_name
            elif m_key in default_baselines:
                def_val, base_unit, base_name = default_baselines[m_key]
                base_val = def_val
                is_missing = True
                gap_note = f"未找到实测基线数据，基于地段估算基准值 ({base_val} {base_unit}) 进行推算。"
                assumptions.append(f"基线指标 [{base_name}] 缺少精确实时数据，按地段基准值 {base_val} {base_unit} 进行估算。")
            else:
                base_name = m_key.replace("_", " ").title()
                is_missing = True
                gap_note = f"Missing baseline for {m_key}"
                assumptions.append(f"基线指标 [{base_name}] 缺失。")

            # Find matching rule for metric
            rule_for_m = next((r for r in rules if r.parameters and (m_key in r.parameters or f"pct_{m_key}" in r.parameters)), None)
            pct_range = default_pct_ranges.get(m_key)

            metric_result = self.evaluate_metric(
                metric_key=m_key,
                metric_name=base_name,
                baseline=base_val,
                unit=base_unit,
                rule=rule_for_m,
                custom_pct=pct_range,
                missing_baseline=is_missing,
                evidence_gap_note=gap_note,
            )
            evaluated_metrics[m_key] = metric_result

        # Calculate confidence
        confidence = self.calculate_decision_confidence(
            baseline_deltas=evaluated_metrics,
            evidence_chain=evidence_chain,
            rules_applied=rules,
        )

        uncertainty_desc = self.generate_uncertainty_description(
            metric_deltas=evaluated_metrics,
            overall_confidence=confidence,
        )

        return evaluated_metrics, assumptions, confidence, uncertainty_desc
