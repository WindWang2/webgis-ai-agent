"""SpatialMemory 评估框架（R9）：多轮 GIS 会话语料回放 + 复用质量指标。

设计纪律（任务书）：
- **wrong/stale reuse 必须比低 recall 更严重**——记分
  ``score = 3×useful − 4×wrong − 4×stale``；wrong/stale 任一 > 0 即不可上线；
- 语料离线确定性：回放只驱动真实 store/retrieval/supersession/GC，
  不依赖 LLM/网络/真实数据体；
- 指标口径：useful（期望复用且命中同一事实）/ stale（复用了已失效事实）/
  wrong（不应复用时复用）/ retrieval_precision / context_bytes_saved /
  tool_calls_saved。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple

from sqlalchemy.orm import sessionmaker

from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_DATASET_SEMANTICS,
    KIND_RESOLVED_PLACE,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SOURCE_DATASET_PIN,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_USER_CORRECTION,
    MemoryEvidence,
    MemoryWriteRequest,
)
from app.services.gis_memory.retrieval import (
    latest_resolved_place,
    lookup_dataset_memory,
)

ORG_A = "org-eval"
ORG_B = "org-eval-b"
PROJ_A = "proj-eval-a"
PROJ_B = "proj-eval-b"
SESS = "sess-eval"

#: 记分权重（D11）：wrong/stale 严惩。
W_USEFUL, W_WRONG, W_STALE = 3.0, -4.0, -4.0


@dataclass
class TurnOp:
    """一个 turn 的动作（模拟真实生产缝的行为）。"""

    op: str                     # resolve_place | consult_place | pin_dataset |
    # consult_dataset | correct_place | set_pref | consult_pref |
    # version_drift | expire_all
    subject: str = ""
    value: Optional[dict] = None
    # 期望：consult 类动作的判定基准
    expect_reuse: Optional[bool] = None   # None = 非 consult 动作
    expect_subject: str = ""
    stale: bool = False                   # True = 若复用即 stale reuse
    note: str = ""
    org: str = ORG_A                      # consult 侧租户（跨租户剧本用）
    project: Optional[str] = None         # consult/pin 侧项目（项目切换剧本用）
    ttl_s: Optional[int] = None           # resolve_place 显式 TTL


@dataclass
class ScenarioResult:
    name: str
    category: str
    useful_reuse: int = 0
    wrong_reuse: int = 0
    stale_reuse: int = 0
    consults: int = 0
    relevant_returns: int = 0
    context_bytes_saved: int = 0
    tool_calls_saved: int = 0
    failures: List[str] = field(default_factory=list)


# ── 剧本（7 类任务书轨迹，各含占位符 {city}/{ds} 供变体替换）────────────


def _sc_reuse_city_new_subject() -> Tuple[str, str, List[TurnOp]]:
    """同城市/新主体：「成都的图书馆」→「再看看医院」。"""
    return "same_city_new_subject", "reuse", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}),
        TurnOp(op="consult_place", expect_reuse=True, expect_subject="{city}"),
        TurnOp(op="consult_place", expect_reuse=True, expect_subject="{city}",
               note="再看看医院"),
    ]


def _sc_same_subject_new_year() -> Tuple[str, str, List[TurnOp]]:
    """同主体/新年份：版本推进后旧语义必须失效、不得复用。"""
    return "same_subject_new_year", "version_drift", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}),
        TurnOp(op="pin_dataset", subject="{ds}",
               value={"dataset_key": "{ds}", "version_token": "v2023",
                      "time_field": "year"}),
        TurnOp(op="consult_dataset", subject="{ds}",
               value={"version_token": "v2023"},
               expect_reuse=True, expect_subject="{ds}"),
        TurnOp(op="version_drift", subject="{ds}",
               value={"version_token": "v2024"}),
        TurnOp(op="consult_dataset", subject="{ds}",
               value={"version_token": "v2024"},
               expect_reuse=False, note="version drift"),
    ]


def _sc_wrong_guess_corrected() -> Tuple[str, str, List[TurnOp]]:
    """错误更正：先记 city 级，用户显式纠正 → 纠正后事实胜出。"""
    return "wrong_guess_corrected", "contradiction", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}),
        TurnOp(op="correct_place", subject="{city}高新区",
               value={"name": "{city}高新区", "level": "district"}),
        TurnOp(op="consult_place", expect_reuse=True,
               expect_subject="{city}高新区", note="纠正后的范围胜出"),
        TurnOp(op="consult_place", expect_reuse=True,
               expect_subject="{city}高新区", note="稳定复用"),
    ]


def _sc_project_switch() -> Tuple[str, str, List[TurnOp]]:
    """项目切换：session 记忆可用，project 记忆不得跨项目跟随。"""
    return "project_switch", "isolation", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}),
        TurnOp(op="pin_dataset", subject="{ds}",
               value={"dataset_key": "{ds}", "version_token": "v1"},
               project=PROJ_A),
        TurnOp(op="consult_dataset", subject="{ds}",
               value={"version_token": "v1"}, project=PROJ_A,
               expect_reuse=True, expect_subject="{ds}"),
        TurnOp(op="consult_dataset", subject="{ds}",
               value={"version_token": "v1"}, project=PROJ_B,
               expect_reuse=False, note="project switch"),
    ]


def _sc_conflicting_preference() -> Tuple[str, str, List[TurnOp]]:
    """偏好冲突：先深色后浅色 → 新偏好生效（旧指纹被取代）。"""
    return "conflicting_preference", "contradiction", [
        TurnOp(op="set_pref", subject="basemap",
               value={"value": "dark"}, project=PROJ_A),
        TurnOp(op="consult_pref", subject="basemap", project=PROJ_A,
               expect_reuse=True, expect_subject="basemap"),
        TurnOp(op="set_pref", subject="basemap",
               value={"value": "light"}, project=PROJ_A),
        TurnOp(op="consult_pref", subject="basemap", project=PROJ_A,
               expect_reuse=True, expect_subject="basemap",
               note="新偏好 active（旧指纹 conflicted 不再注入）"),
    ]


def _sc_provider_failure_recovered() -> Tuple[str, str, List[TurnOp]]:
    """失败记忆带 TTL：过期后不得再抑制（stale 防线）。"""
    return "failure_memory_expiry", "expiry", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}, ttl_s=3600),
        TurnOp(op="consult_place", expect_reuse=True, expect_subject="{city}"),
        TurnOp(op="expire_all"),
        TurnOp(op="consult_place", expect_reuse=False, note="after expiry"),
    ]


def _sc_cross_tenant() -> Tuple[str, str, List[TurnOp]]:
    """跨租户：org-B 的会话读不到 org-A 的范围。"""
    return "cross_tenant", "security", [
        TurnOp(op="resolve_place", subject="{city}",
               value={"name": "{city}", "level": "city"}),
        TurnOp(op="consult_place", org=ORG_B,
               expect_reuse=False, note="cross tenant"),
    ]


SCENARIO_BUILDERS = [
    _sc_reuse_city_new_subject,
    _sc_same_subject_new_year,
    _sc_wrong_guess_corrected,
    _sc_project_switch,
    _sc_conflicting_preference,
    _sc_provider_failure_recovered,
    _sc_cross_tenant,
]

_VARIANT_CITIES = ["成都市", "重庆市", "西安市", "武汉市", "杭州市", "南京市",
                   "郑州市", "长沙市"]


def _expand_corpus() -> List[Tuple[str, str, List[TurnOp]]]:
    """7 剧本 × 变体（城市/数据集替换）= 32 个互异场景（4+6+4+6+4+4+4）。"""
    per_builder = {
        "same_city_new_subject": 4,
        "same_subject_new_year": 6,
        "wrong_guess_corrected": 4,
        "project_switch": 6,
        "conflicting_preference": 4,
        "failure_memory_expiry": 4,
        "cross_tenant": 4,
    }
    scenarios: List[Tuple[str, str, List[TurnOp]]] = []
    for builder in SCENARIO_BUILDERS:
        name, category, ops = builder()
        count = per_builder[name]
        for v in range(count):
            city = _VARIANT_CITIES[v % len(_VARIANT_CITIES)]
            ds = f"ds:gdp{v}"

            def _sub(x):
                if isinstance(x, str):
                    return x.replace("{city}", city).replace("{ds}", ds)
                if isinstance(x, dict):
                    return {k: _sub(val) for k, val in x.items()}
                return x

            vops = [
                TurnOp(
                    op=o.op, subject=_sub(o.subject), value=_sub(o.value),
                    expect_reuse=o.expect_reuse, expect_subject=_sub(o.expect_subject),
                    stale=o.stale, note=o.note,
                    org=o.org, project=o.project, ttl_s=o.ttl_s,
                )
                for o in copy.deepcopy(ops)
            ]
            scenarios.append((f"{name}#{v}", category, vops))
    assert len(scenarios) == 32, f"corpus 应为 32 场景，实际 {len(scenarios)}"
    return scenarios


def run_corpus(engine_factory: Callable[[], Any]) -> dict:
    """回放 32 场景（每场景独立库），返回聚合报告 + 逐场景明细。"""
    report = {
        "scenarios": 0, "useful_reuse": 0, "wrong_reuse": 0, "stale_reuse": 0,
        "consults": 0, "relevant_returns": 0, "context_bytes_saved": 0,
        "tool_calls_saved": 0, "score": 0.0, "per_scenario": [],
    }
    for name, category, ops in _expand_corpus():
        engine = engine_factory()
        result = _run_scenario(name, category, ops, engine)
        report["per_scenario"].append({
            "name": name, "category": category,
            "useful": result.useful_reuse, "wrong": result.wrong_reuse,
            "stale": result.stale_reuse,
            "recall_misses": sum(
                1 for f in result.failures if "expected reuse, got none" in f
            ),
            "failures": result.failures,
        })
        report["scenarios"] += 1
        report["useful_reuse"] += result.useful_reuse
        report["wrong_reuse"] += result.wrong_reuse
        report["stale_reuse"] += result.stale_reuse
        report["consults"] += result.consults
        report["relevant_returns"] += result.relevant_returns
        report["context_bytes_saved"] += result.context_bytes_saved
        report["tool_calls_saved"] += result.tool_calls_saved
    report["score"] = (
        W_USEFUL * report["useful_reuse"]
        + W_WRONG * report["wrong_reuse"]
        + W_STALE * report["stale_reuse"]
    )
    return report


def _run_scenario(name, category, ops, engine) -> ScenarioResult:
    result = ScenarioResult(name=name, category=category)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        project = PROJ_A
        for step in ops:
            if step.op == "resolve_place":
                s.record_memory(db, _place_write(
                    step.subject, step.value, ttl_s=step.ttl_s
                ))
                db.commit()
            elif step.op == "correct_place":
                s.record_memory(db, MemoryWriteRequest(
                    kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id=SESS,
                    subject=step.subject, value=step.value,
                    evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION,
                                            method="eval_correction"),
                    confidence=0.9, org_id=ORG_A,
                ))
                db.commit()
            elif step.op == "pin_dataset":
                s.record_memory(db, MemoryWriteRequest(
                    kind=KIND_DATASET_SEMANTICS, scope=SCOPE_PROJECT,
                    scope_id=step.project or project, subject=step.subject,
                    value=step.value,
                    evidence=MemoryEvidence(source=SOURCE_DATASET_PIN,
                                            method="eval_pin"),
                    confidence=0.9, org_id=ORG_A,
                    invalidation_rule="dataset_version",
                ))
                db.commit()
            elif step.op == "set_pref":
                # 项目偏好 → ADR-0069 账本（D4 路由）；consult_pref 直查账本
                from app.services.cartography.project_memory import record_fact

                record_fact(db, project, "preference", step.subject,
                            step.value, fingerprint=step.value.get("value"),
                            confidence=0.9)
                db.commit()
            elif step.op == "version_drift":
                s.invalidate_for_dataset(
                    db, org_id=ORG_A, dataset_key=step.subject,
                    version_token=(step.value or {}).get("version_token"),
                )
                db.commit()
            elif step.op == "expire_all":
                future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365)
                s.sweep_expired(db, now=future)
                db.commit()
            elif step.op == "consult_place":
                result.consults += 1
                hit = latest_resolved_place(
                    db, org_id=step.org, session_id=SESS, project_id=None
                )
                _judge(hit.record if hit is not None else None, step, result,
                       payload=json.dumps(hit.record.value, ensure_ascii=False)
                       if hit else "")
            elif step.op == "consult_dataset":
                result.consults += 1
                token = (step.value or {}).get("version_token")
                lookup = lookup_dataset_memory(
                    db, org_id=step.org, project_id=step.project or project,
                    dataset_key=step.subject, version_token=token,
                )
                hit = None
                payload = ""
                if lookup.get("semantics") is not None:
                    hit = lookup["semantics"]
                    payload = json.dumps(hit.value, ensure_ascii=False)
                _judge(hit, step, result, payload=payload)
            elif step.op == "consult_pref":
                result.consults += 1
                from app.services.cartography.project_memory import get_active_facts

                facts = get_active_facts(
                    db, step.project or project, kinds=("preference",)
                )
                hit = None
                payload = ""
                if facts:
                    hit = facts[0]
                    payload = json.dumps(hit.payload, ensure_ascii=False)
                _judge(hit, step, result, payload=payload)
    return result


def _place_write(subject, value, conf=0.86, ttl_s=None) -> MemoryWriteRequest:
    return MemoryWriteRequest(
        kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id=SESS,
        subject=subject, value=value,
        evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION, method="eval"),
        confidence=conf, org_id=ORG_A, ttl_s=ttl_s,
    )


def _judge(hit, step: TurnOp, result: ScenarioResult, *, payload: str) -> None:
    """命中判定（hit 为 record 形对象：subject 属性必须存在）。"""
    if step.expect_reuse is True:
        if hit is not None and hit.subject == step.expect_subject:
            result.useful_reuse += 1
            result.relevant_returns += 1
            result.context_bytes_saved += len(payload.encode("utf-8"))
            result.tool_calls_saved += 1
        elif hit is not None:
            result.wrong_reuse += 1
            result.failures.append(
                f"{step.note}: expected {step.expect_subject}, got {hit.subject}"
            )
        else:
            result.failures.append(f"{step.note}: expected reuse, got none (recall)")
    elif step.expect_reuse is False:
        if hit is not None:
            if step.stale:
                result.stale_reuse += 1
            else:
                result.wrong_reuse += 1
            result.failures.append(
                f"{step.note}: reuse not expected, got {hit.subject}"
            )
        # 未召回 = 正确（不计分）


__all__ = ["run_corpus", "W_USEFUL", "W_WRONG", "W_STALE"]
