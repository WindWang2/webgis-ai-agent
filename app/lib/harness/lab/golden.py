"""Validation for the G07 golden-task manifest.

The manifest references existing acceptance scenarios and focused test files;
it does not execute them or duplicate their expectations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GOLDEN_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reliability" / "golden_tasks.json"
)
SCHEMA_VERSION = "gis_harness_reliability_golden.v1"
EXECUTION_MODES = frozenset({
    "offline_replay", "live_dispatch_stub_llm", "browser_renderer",
    "live_services_opt_in",
})
REQUIRED_CATEGORIES = (
    "point_distribution", "heatmap_eligibility", "zonal_statistics",
    "network_accessibility", "spatial_join", "cross_city_scope",
    "selection_bounds", "temporal_map", "style_increment",
    "publication_export", "cancel_recovery", "offline_degradation",
)
_ORACLE_KEYS = ("invariant", "reference_space", "units", "nodata", "ownership")


class GoldenSpecError(ValueError):
    pass


@dataclass(frozen=True)
class GoldenTask:
    task_id: str
    category: str
    title: str
    execution_mode: str
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    expectations: Dict[str, tuple[str, ...]]
    focused_tests: tuple[str, ...]
    oracle: Dict[str, str]
    coverage: Dict[str, str]
    disclosures: tuple[str, ...] = field(default_factory=tuple)


def _strings(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise GoldenSpecError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def load_golden_tasks(path: Path = DEFAULT_GOLDEN_PATH) -> List[GoldenTask]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise GoldenSpecError(f"expected {SCHEMA_VERSION}")
    if not isinstance(data.get("seed"), int) or isinstance(data.get("seed"), bool):
        raise GoldenSpecError("seed must be an integer")
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or len(raw_tasks) < 12:
        raise GoldenSpecError("at least 12 golden tasks are required")

    tasks: List[GoldenTask] = []
    ids: set[str] = set()
    categories: set[str] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise GoldenSpecError("task entries must be objects")
        task_id = str(raw.get("id") or "")
        category = str(raw.get("category") or "")
        if not task_id or task_id in ids:
            raise GoldenSpecError(f"duplicate or empty task id: {task_id!r}")
        if category not in REQUIRED_CATEGORIES or category in categories:
            raise GoldenSpecError(f"duplicate or unknown category: {category!r}")
        ids.add(task_id)
        categories.add(category)
        mode = str(raw.get("execution_mode") or "")
        if mode not in EXECUTION_MODES:
            raise GoldenSpecError(f"{task_id}: unknown execution_mode {mode!r}")
        positive = _strings(raw.get("positive") or [], field_name=f"{task_id}.positive")
        negative = _strings(raw.get("negative") or [], field_name=f"{task_id}.negative")
        focused = _strings(raw.get("focused_tests") or [], field_name=f"{task_id}.focused_tests")
        if not positive and not negative and not focused:
            raise GoldenSpecError(f"{task_id}: no executable oracle")
        if not positive and not focused:
            raise GoldenSpecError(f"{task_id}: missing positive oracle")
        if not negative and not focused:
            raise GoldenSpecError(f"{task_id}: missing negative oracle")
        expectations_raw = raw.get("expectations") or {}
        if not isinstance(expectations_raw, dict):
            raise GoldenSpecError(f"{task_id}: expectations must be an object")
        expectations = {
            str(scenario): _strings(exp, field_name=f"{task_id}.{scenario}.expectations")
            for scenario, exp in expectations_raw.items()
        }
        for scenario in (*positive, *negative):
            if scenario not in expectations or not expectations[scenario]:
                raise GoldenSpecError(f"{task_id}: missing expectations for {scenario}")
        oracle_raw = raw.get("oracle") or {}
        if not isinstance(oracle_raw, dict) or any(
            not isinstance(oracle_raw.get(key), str) or not oracle_raw.get(key)
            for key in _ORACLE_KEYS
        ):
            raise GoldenSpecError(f"{task_id}: incomplete scientific oracle")
        coverage = raw.get("coverage") or {}
        if not isinstance(coverage, dict) or any(
            not isinstance(coverage.get(key), str) or not coverage.get(key)
            for key in ("browser_renderer", "live_services_opt_in")
        ):
            raise GoldenSpecError(f"{task_id}: browser/live coverage must be explicit")
        tasks.append(GoldenTask(
            task_id=task_id,
            category=category,
            title=str(raw.get("title") or task_id),
            execution_mode=mode,
            positive=positive,
            negative=negative,
            expectations=expectations,
            focused_tests=focused,
            oracle=dict(oracle_raw),
            coverage=dict(coverage),
            disclosures=_strings(raw.get("disclosures") or [], field_name=f"{task_id}.disclosures"),
        ))
    if categories != set(REQUIRED_CATEGORIES):
        raise GoldenSpecError(f"category coverage mismatch: {sorted(categories)}")
    return tasks


__all__ = [
    "DEFAULT_GOLDEN_PATH", "EXECUTION_MODES", "REQUIRED_CATEGORIES",
    "SCHEMA_VERSION", "GoldenSpecError", "GoldenTask", "load_golden_tasks",
]
