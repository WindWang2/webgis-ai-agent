"""ads-v1 fact store, cost budgets & ratchet (DS8, ADR-0178, gap A10).

The D4 ``AcquisitionFact`` is the single acquisition telemetry record: the
fallback chain (DS4), the version gate (DS5) and the matrix runner all emit
facts here. In-process store by default; the alembic table (0071) mirrors the
shape for multi-worker persistence (DAO wired in DS9 closeout).

Ratchet: facts aggregate by (source_id × metric × wave). An **injected
degradation** (latency spike / pushdown removed / forced degradation) must be
intercepted 100% — the gate compares aggregates against the previous wave's
baseline and the declared cost budgets, raising ``RatchetViolation`` on
regressions beyond tolerance.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.services.data_fabric.contracts import AcquisitionFact

#: Metrics tracked per (source × wave) and their ratchet direction:
#: higher-is-worse metrics regress when they GROW beyond tolerance.
METRICS = ("rows", "bytes", "latency_ms", "retries", "degraded")

#: Ratchet tolerances (fraction); provisional — recalibrated at closeout.
TOLERANCE = {"rows": 0.25, "bytes": 0.25, "latency_ms": 0.30, "retries": 0.5, "degraded": 0.0}


class CostBudget(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    request_type: str
    max_rows: Optional[int] = None
    max_bytes: Optional[int] = None
    max_ms: Optional[float] = None
    max_quota: Optional[float] = None
    wave: str = ""


class BudgetAlert(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    request_type: str
    metric: str
    value: float
    budget: float


class RatchetViolation(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    wave: str
    metric: str
    baseline: float
    current: float
    tolerance: float


class FactsStore:
    """In-process fact + budget store (mirror shape of migration 0071)."""

    def __init__(self) -> None:
        self._facts: List[AcquisitionFact] = []
        self._budgets: Dict[Any, CostBudget] = {}

    # -- facts ---------------------------------------------------------------
    def record(self, fact: AcquisitionFact) -> AcquisitionFact:
        if not fact.ts:
            fact = fact.model_copy(update={"ts": _now()})
        self._facts.append(fact)
        return fact

    def facts(self, *, source_id: Optional[str] = None, wave: str = "") -> List[AcquisitionFact]:
        out = self._facts
        if source_id is not None:
            out = [f for f in out if f.source_id == source_id]
        if wave:
            out = [f for f in out if f.wave == wave]
        return list(out)

    # -- budgets ---------------------------------------------------------------
    def set_budget(self, budget: CostBudget) -> None:
        self._budgets[(budget.source_id, budget.request_type, budget.wave)] = budget

    def check_budget(self, fact: AcquisitionFact, request_type: str) -> List[BudgetAlert]:
        alerts: List[BudgetAlert] = []
        budget = self._budgets.get((fact.source_id, request_type, fact.wave)) \
            or self._budgets.get((fact.source_id, request_type, ""))
        if budget is None:
            return alerts
        pairs = [
            ("rows", budget.max_rows, float(fact.rows)),
            ("bytes", budget.max_bytes, float(fact.bytes)),
            ("latency_ms", budget.max_ms, fact.latency_ms),
            ("quota", budget.max_quota, None),
        ]
        for metric, cap, value in pairs:
            if cap is None or value is None:
                continue
            if value > float(cap):
                alerts.append(BudgetAlert(
                    source_id=fact.source_id, request_type=request_type,
                    metric=metric, value=value, budget=float(cap),
                ))
        return alerts

    # -- ratchet ---------------------------------------------------------------
    def aggregate(self, wave: str) -> Dict[str, Dict[str, float]]:
        """(source_id × metric) → p50 aggregate for one wave."""
        grouped: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        for f in self.facts(wave=wave):
            if f.source_id is None:
                continue
            grouped[f.source_id]["rows"].append(float(f.rows))
            grouped[f.source_id]["bytes"].append(float(f.bytes))
            grouped[f.source_id]["latency_ms"].append(f.latency_ms)
            grouped[f.source_id]["retries"].append(float(f.retries))
            grouped[f.source_id]["degraded"].append(1.0 if f.degraded else 0.0)
        out: Dict[str, Dict[str, float]] = {}
        for sid, metrics in grouped.items():
            out[sid] = {m: _p50(vals) for m, vals in metrics.items()}
        return out

    def ratchet_check(self, baseline_wave: str, current_wave: str) -> List[RatchetViolation]:
        """Compare waves per (source × metric); degradation beyond tolerance
        is a violation — injected degradation must be intercepted 100%."""
        base = self.aggregate(baseline_wave)
        cur = self.aggregate(current_wave)
        violations: List[RatchetViolation] = []
        for sid, metrics in cur.items():
            if sid not in base:
                continue  # new source: nothing to regress against
            for metric, tolerance in TOLERANCE.items():
                b = base[sid].get(metric)
                c = metrics.get(metric)
                if b is None or c is None:
                    continue
                if b > 0:
                    violated = c > b * (1.0 + tolerance) + 1e-9
                else:
                    # zero baseline: ANY growth is a regression (e.g. 0 retries
                    # → 3 retries is exactly the storm the ratchet exists for)
                    violated = c > 0
                if violated:
                    violations.append(RatchetViolation(
                        source_id=sid, wave=current_wave, metric=metric,
                        baseline=b, current=c, tolerance=tolerance,
                    ))
        return violations

    def clear(self) -> None:
        self._facts.clear()
        self._budgets.clear()


def _p50(values: List[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[len(xs) // 2]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def new_fact_id() -> str:
    return uuid.uuid4().hex


_STORE: Optional[FactsStore] = None


def get_facts_store() -> FactsStore:
    global _STORE
    if _STORE is None:
        _STORE = FactsStore()
    return _STORE


__all__ = [
    "METRICS",
    "TOLERANCE",
    "CostBudget",
    "BudgetAlert",
    "RatchetViolation",
    "FactsStore",
    "get_facts_store",
    "new_fact_id",
]
