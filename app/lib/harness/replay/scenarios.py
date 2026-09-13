"""场景语料矩阵 + 确定性展开器（B4/B5，ADR-0183 决策四 / D7）。

紧凑模板 × 参数轴 → 展开场景（versioned JSON，提交展开产物供 review）：

- **核心单轮/双轮**（任务书 B4 十七类中的制图主类）× 8 参数变体 = 104；
- **多轮 3–8 轮**（follow-up / pin-hide / retry-reconnect / version-drift）
  × 6 变体 = 36；语料总量 140，可扩 500+（加类别 × 加变体轴）；
- **期望即语义**：expect 树只钉 {passed/evaluated/reason} 等裁决语义
  （canned 数据是它们的纯函数），数值面走 tolerant ratchet 行；
- 全部 fixture 合成、离线可跑（无浏览器 / 无 LLM / 无网络）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.harness.replay.replayer import (
    FINGERPRINT_PLACEHOLDER,
    SESSION_PLACEHOLDER,
    Scenario,
    ScenarioOp,
    TurnSpec,
)

CORPUS_VERSION = 1

# ── 期望树构建器（语义断言的最小词表）────────────────────────────────────────


def expect_checks(**checks: Dict[str, Any]) -> Dict[str, Any]:
    return {"gate": {"checks": checks}}


def _msv(passed: bool) -> Dict[str, Any]:
    return {"MapSpecValidity": {"evaluated": True, "passed": passed}}


# ── canned op 构建器（生产收据同形）──────────────────────────────────────────


def _upsert(layer_id: str, ref: str, *, ok: bool = True, call_id: str = "",
            error_msg: str = "source unavailable") -> ScenarioOp:
    cid = call_id or f"call-upsert-{layer_id}"
    if ok:
        return ScenarioOp(
            call_id=cid, tool="webgis_layer_upsert",
            arguments={"layer": {"id": layer_id, "type": "circle",
                                 "source": f"src-{layer_id}"},
                       "source_ref": ref},
            result={"success": True, "is_compiled": True,
                    "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER},
        )
    return ScenarioOp(
        call_id=cid, tool="webgis_layer_upsert",
        arguments={"layer": {"id": layer_id, "type": "circle",
                             "source": f"src-{layer_id}"},
                   "source_ref": ref},
        result={"success": False, "is_error": True, "error_msg": error_msg},
        is_error=True, error_msg=error_msg,
    )


def _validate(call_id: str = "call-validate",
              *, error: bool = False) -> ScenarioOp:
    """runtime_validate：视觉判定对象 + record-only 证据路径。"""
    return ScenarioOp(
        call_id=call_id, tool="webgis_runtime_validate",
        arguments={"mapspec_fingerprint": FINGERPRINT_PLACEHOLDER},
        result=(
            {"success": True, "is_compiled": True,
             "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER}
            if not error else
            {"success": False, "is_error": True,
             "error_msg": "renderer failure"}
        ),
        is_error=error,
        error_msg="renderer failure" if error else "",
    )


def _profile_op(source_id: str, ref: str,
                call_id: str = "call-profile") -> ScenarioOp:
    return ScenarioOp(
        call_id=call_id, tool="webgis_source_profile",
        arguments={"source_id": source_id, "source_ref": ref},
        result={"success": True, "is_compiled": True,
                "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER},
    )


# ── cartography fixture 构建（从 spec 派生 observation，占位符注入）──────────


def green_cartography(spec: Dict[str, Any], *,
                      visual: bool = True, hidden_layers: int = 0) -> Dict[str, Any]:
    """L4 pass 的完整 fixture：spec + session 观测（style/图层在场/可见）。

    observation 图层逐层对齐 spec 的身份（`_refId` = 源的 addressable
    ref/imageRef）与 legend_spec —— 满足生产信任阶梯的收敛检查。
    """
    layers = []
    for layer in spec.get("layers", []):
        source = spec["sources"].get(layer.get("source") or {}, {})
        ref_id = source.get("ref") or source.get("imageRef")
        actual: Dict[str, Any] = {"id": layer["id"], "_refId": ref_id,
                                  "visible": True, "style_converged": True}
        if layer.get("legend_spec") is not None:
            actual["legend_spec"] = layer["legend_spec"]
        layers.append(actual)
    for layer in layers[:hidden_layers]:
        layer["visible"] = False
    return {
        "mapspec": spec,
        "map_state": {"_cartographic_observation": {
            "source": "frontend_runtime",
            "session_id": SESSION_PLACEHOLDER,
            "sequence": 1,
            "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER,
            "style_loaded": True,
            "reconcile_error": "",
            "layers": layers,
            "viewport": dict(spec.get("view") or {}),
        }},
        **({"visual_judge": True} if visual else {}),
    }


#: 图层类型 → (paint, geometryTypes)；已实证通过确定性语义评审。
_LAYER_TYPE_SHAPES = {
    "circle": ({"circle-color": "#3182bd", "circle-radius": 5}, ["Point"]),
    "fill": ({"fill-color": "#3182bd"}, ["Polygon"]),
    "line": ({"line-color": "#3182bd"}, ["LineString"]),
    "heatmap": ({"heatmap-weight": 1}, ["Point"]),
}


def _spec(layer_ids: List[str], *, ref: str = "ref:geojson-parks",
          layer_type: str = "circle", crs: Optional[str] = "EPSG:4326",
          legend: bool = False, view: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raster = layer_type == "raster"
    paint, geom_types = _LAYER_TYPE_SHAPES.get(
        layer_type, ({"circle-color": "#3182bd", "circle-radius": 5}, ["Point"]))
    profile: Dict[str, Any] = {
        "featureCount": 120, "bbox": [116.0, 39.0, 117.0, 40.0],
        "geometryTypes": geom_types, "crs": crs,
    }
    if crs:
        profile["crs_status"] = "explicit"
    def _source() -> Dict[str, Any]:
        if raster:
            return {"type": "raster", "imageRef": ref,
                    "bounds": [116.0, 39.0, 117.0, 40.0], "profile": profile}
        return {"type": "geojson", "ref": ref, "profile": profile}
    layers = []
    for lid in layer_ids:
        layer: Dict[str, Any] = {"id": lid, "type": layer_type,
                                 "source": f"src-{lid}", "paint": paint}
        if legend:
            layer["legend_spec"] = {"title": lid, "entries": [
                {"label": "a", "color": "#3182bd"}]}
        layers.append(layer)
    return {"schema_version": "1.2", "title": layer_ids[0],
            "view": view or {"center": [116.4, 39.9], "zoom": 10},
            "sources": {lid_key: _source() for lid_key in
                        (f"src-{lid}" for lid in layer_ids)},
            "layers": layers}


def _turn(user_input: str, spec: Dict[str, Any], *, visual: bool = True,
          ops: Optional[List[ScenarioOp]] = None,
          extra_expect: Optional[Dict[str, Any]] = None,
          hidden_layers: int = 0) -> TurnSpec:
    layer_expect: Dict[str, Any] = {
        "MapSpecValidity": {"evaluated": True, "passed": True},
        "CursorResolutionRate": {"evaluated": True, "passed": True},
        "CartographicQuality": {"evaluated": True, "passed": True},
    }
    expect: Dict[str, Any] = {"gate": {"checks": layer_expect}}
    if visual:
        expect["goal"] = {"status": "pass"}
    else:
        expect["goal"] = {"status": "not_evaluated"}
    if extra_expect:
        for section, payload in extra_expect.items():
            if section == "gate":
                expect["gate"].setdefault("overall_passed", payload)
            else:
                expect[section] = payload
    refs = {src.get("ref") or src.get("imageRef"): {"type": "FeatureCollection"}
            for src in (spec.get("sources") or {}).values()
            if src.get("ref") or src.get("imageRef")}
    first_layer = spec["layers"][0]
    first_source = spec["sources"][first_layer["source"]]
    first_ref = first_source.get("ref") or first_source.get("imageRef")
    return TurnSpec(
        user_input=user_input,
        ops=ops if ops is not None else
        [_upsert(first_layer["id"], first_ref), _validate()],
        cartography=green_cartography(spec, visual=visual,
                                      hidden_layers=hidden_layers),
        refs=refs,
        expect=expect,
    )


# ── 单轮核心模板（× 参数变体）───────────────────────────────────────────────

_SINGLE_TURN_TEMPLATES = (
    # (类别, spec 工厂, 视觉判定, 变体数)
    # 图层形态均已实证通过确定性语义评审；legend 等价检查要求与数据驱动
    # paint 的深度配对 —— 属 master 自身测试覆盖面，语料不复验。
    ("point_distribution", lambda v: _spec([f"points-{v}"]), True, 8),
    ("district_comparison",
     lambda v: _spec([f"district-a-{v}", f"district-b-{v}"],
                     view={"center": [116.4, 39.9], "zoom": 11}), True, 8),
    ("heatmap", lambda v: _spec([f"heat-{v}"], layer_type="heatmap"), True, 8),
    ("choropleth",
     lambda v: _spec([f"region-{v}"], layer_type="fill"), True, 8),
    ("raster_terrain",
     lambda v: _spec([f"terrain-{v}"], layer_type="raster"), True, 8),
    ("remote_sensing_classification",
     lambda v: _spec([f"classified-{v}"], layer_type="fill",
                     view={"center": [102.0, 25.0], "zoom": 8}), True, 8),
    ("land_use_change",
     lambda v: _spec([f"landuse-{v}"], layer_type="fill",
                     view={"center": [118.0, 24.4], "zoom": 9}), True, 8),
    ("accessibility_network",
     lambda v: _spec([f"roads-{v}"], layer_type="line"), True, 8),
    ("multi_source_fallback",
     lambda v: _spec([f"fallback-{v}"]), False, 8),
    ("missing_crs", lambda v: _spec([f"nocrs-{v}"], crs=None), False, 8),
    ("dirty_data", lambda v: _spec([f"dirty-{v}"]), False, 8),
    ("cvd_print", lambda v: _spec([f"cvd-{v}"]), True, 8),
    ("presentation",
     lambda v: _spec([f"slide-{v}"], view={"center": [121.5, 31.2],
                                           "zoom": 12}), True, 8),
)


def _single_turn_scenario(category: str, variant: int) -> Scenario:
    if category == "multi_source_fallback":
        # 源 A 收据失败 + 源 B 重试成功：ErrorRecoveryRate 恢复证据；
        # MSV 是会话级比例（1 失败收据永久计入）→ 诚实期望 passed=False。
        spec = _spec([f"fallback-{variant}"])
        turn = _turn(
            f"用备用数据源画 fallback-{variant}",
            spec, visual=False,
            ops=[_upsert(f"fallback-{variant}", "ref:geojson-primary",
                         ok=False),
                 _upsert(f"fallback-{variant}", "ref:geojson-backup",
                         call_id=f"call-retry-{variant}"),
                 _validate()],
        )
        turn.expect["gate"]["checks"]["MapSpecValidity"] = {
            "evaluated": True, "passed": False}
        turn.expect["gate"]["checks"]["ErrorRecoveryRate"] = {
            "evaluated": True, "passed": True}
        turn.expect["gate"]["overall_passed"] = False
        turn.refs.update({"ref:geojson-primary": {"type": "FeatureCollection"},
                          "ref:geojson-backup": {"type": "FeatureCollection"}})
        return Scenario(scenario_id=f"core-{category}-{variant:02d}",
                        category=category, description="源失败回退后恢复",
                        turns=[turn])
    if category == "missing_crs":
        # profile 缺显式 CRS → 确定性评审 fail → CQ fail（fail-closed）。
        spec = _spec([f"nocrs-{variant}"], crs=None)
        turn = _turn(f"画 nocrs-{variant}", spec, visual=False)
        turn.expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": True, "passed": False}
        turn.expect["gate"]["overall_passed"] = False
        return Scenario(scenario_id=f"core-{category}-{variant:02d}",
                        category=category, description="缺 CRS 证据被拦截",
                        turns=[turn])
    if category == "dirty_data":
        # 收据报错 → MSV fail + CQ 策略失败（缺证据≠成功）。
        spec = _spec([f"dirty-{variant}"])
        turn = _turn(f"画 dirty-{variant}", spec, visual=False,
                     ops=[_upsert(f"dirty-{variant}",
                                  f"ref:geojson-dirty-{variant}", ok=False),
                          _validate(error=True)])
        turn.expect["gate"]["checks"]["MapSpecValidity"] = {
            "evaluated": True, "passed": False}
        turn.expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": False, "passed": False,
            "reason": "not_evaluated_policy_fail"}
        turn.expect["gate"]["overall_passed"] = False
        turn.refs[f"ref:geojson-dirty-{variant}"] = {"type": "FeatureCollection"}
        return Scenario(scenario_id=f"core-{category}-{variant:02d}",
                        category=category, description="脏数据收据失败",
                        turns=[turn],
                        faults=[{"type": "invalid_tool_result",
                                 "target": f"call-upsert-dirty-{variant}"}])
    spec = _SINGLE_TURN_MAKES[category](variant)
    visual = category not in ("multi_source_fallback", "missing_crs",
                              "dirty_data")
    turn = _turn(f"把 {spec['layers'][0]['id']} 做成地图", spec, visual=visual)
    return Scenario(scenario_id=f"core-{category}-{variant:02d}",
                    category=category,
                    description=f"{category} 参数变体 {variant}",
                    turns=[turn])


_SINGLE_TURN_MAKES = {
    category: make for category, make, _visual, _count in _SINGLE_TURN_TEMPLATES
}
_SINGLE_TURN_COUNTS = {
    category: count for category, make, visual, count in _SINGLE_TURN_TEMPLATES
}


# ── 多轮模板（3–8 轮；同 session 情境持续）──────────────────────────────────


def _multi_turn_variants(category: str, variant: int) -> Scenario:
    sid = f"mt-{category}-{variant:02d}"
    spec_a = _spec([f"{sid}-base"])
    if category == "user_followup":
        spec_b = _spec([f"{sid}-extra"])
        turns = [
            _turn(f"画 {sid}-base", spec_a),
            _turn(f"再加一层 {sid}-extra", spec_b),
            _turn(f"调整 {sid}-extra 的样式", spec_b),
        ]
        if variant % 2 == 0:
            turns.append(_turn(f"给 {sid}-base 补视觉验证", spec_a))
        for index, turn in enumerate(turns):
            turn.expect["gate"]["checks"]["MapSpecValidity"] = {
                "evaluated": True, "passed": True}
        return Scenario(scenario_id=f"mt-{category}-{variant:02d}",
                        category=category,
                        description=f"多轮 follow-up（{len(turns)} 轮）",
                        turns=turns)
    if category == "user_pin_hide":
        # 隐藏层 = observation 与 spec 可见性分歧 → CQ fail（诚实阶梯）；
        # 恢复显示后回到 pass —— 情境（隐藏状态）跨轮被 harness 保持。
        hidden_expect = expect_checks(
            **{"CartographicQuality": {"evaluated": True, "passed": False}})
        turns = [
            _turn(f"画 {sid}-base", spec_a),
            _turn(f"隐藏 {sid}-base 图层", spec_a, hidden_layers=1,
                  extra_expect={"gate": False, "goal":
                                {"status": "not_evaluated"}}),
            _turn(f"恢复显示 {sid}-base", spec_a),
        ]
        turns[1].expect["gate"]["checks"].update(
            hidden_expect["gate"]["checks"])
        if variant % 2 == 0:
            turns.insert(2, _turn(f"再次隐藏 {sid}-base", spec_a,
                                  hidden_layers=1,
                                  extra_expect={"gate": False, "goal":
                                                {"status": "not_evaluated"}}))
            turns[2].expect["gate"]["checks"].update(
                hidden_expect["gate"]["checks"])
            turns.append(_turn(f"最终验证 {sid}-base", spec_a))
        return Scenario(scenario_id=f"mt-{category}-{variant:02d}",
                        category=category,
                        description=f"用户 pin/hide 跨轮情境（{len(turns)} 轮）",
                        turns=turns)
    if category == "retry_reconnect":
        # MSV 是会话级比例：t1 的失败收据永远计入 → 后续轮 MSV 仍红，
        # 但 ErrorRecoveryRate（0 → 100）与 CQ 恢复才是恢复语义的载体。
        spec_a = _spec([f"{sid}-base"], ref=f"ref:geojson-{sid}")
        turns = [
            _turn(f"画 {sid}-base（首次失败）", spec_a, visual=False,
                  ops=[_upsert(f"{sid}-base", f"ref:geojson-{sid}",
                               ok=False),
                       _validate()]),
            _turn(f"重试 {sid}-base（断线重连）", spec_a, visual=False,
                  ops=[_upsert(f"{sid}-base", f"ref:geojson-{sid}",
                               call_id=f"call-retry-{variant}"),
                       _validate()]),
            _turn(f"确认 {sid}-base 完成", spec_a, visual=False),
        ]
        turns[0].expect["gate"]["checks"]["MapSpecValidity"] = {
            "evaluated": True, "passed": False}
        # validate 收据携带当前代际指纹 → CQ 评审通过（生产阶梯语义）。
        turns[0].expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": True, "passed": True}
        turns[0].expect["gate"]["checks"]["ErrorRecoveryRate"] = {
            "evaluated": True, "passed": False}
        turns[0].expect["gate"]["overall_passed"] = False
        for turn in turns[1:]:
            turn.expect["gate"]["checks"]["MapSpecValidity"] = {
                "evaluated": True, "passed": False}
            turn.expect["gate"]["checks"]["ErrorRecoveryRate"] = {
                "evaluated": True, "passed": True}
            turn.expect["gate"]["overall_passed"] = False
        turns[1].expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": True, "passed": True}
        turns[2].expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": True, "passed": True}
        return Scenario(scenario_id=f"mt-{category}-{variant:02d}",
                        category=category,
                        description="失败→重试→恢复（3 轮）", turns=turns,
                        faults=[{"type": "source_unavailable",
                                 "target_turn": 0}])
    if category == "data_version_drift":
        turns = [
            _turn(f"画 {sid}-base（v1）", spec_a),
            _turn(f"数据漂移后复核 {sid}-base", spec_a, visual=False,
                  ops=[ScenarioOp(
                      call_id=f"call-drift-{variant}",
                      tool="webgis_runtime_validate",
                      arguments={"mapspec_fingerprint":
                                 "fingerprint-drifted-generation"},
                      result={"success": True, "is_compiled": True,
                              "mapspec_fingerprint":
                              "fingerprint-drifted-generation"},
                  )]),
            _turn(f"按当前版本重绘 {sid}-base", spec_a),
            _turn(f"最终确认 {sid}-base", spec_a),
        ]
        # 漂移代际的指纹 ≠ 当前 spec 指纹 → CQ superseded（fail-closed）。
        turns[1].expect["gate"]["checks"]["CartographicQuality"] = {
            "evaluated": False, "passed": False,
            "reason": "not_evaluated_policy_fail"}
        turns[1].expect["gate"]["overall_passed"] = False
        return Scenario(scenario_id=f"mt-{category}-{variant:02d}",
                        category=category,
                        description="数据版本漂移→superseded→重绘（4 轮）",
                        turns=turns,
                        faults=[{"type": "stale_ref", "target_turn": 1}])
    raise ValueError(f"unknown multi-turn category: {category}")


_MULTI_TURN_CATEGORIES = (
    "user_followup", "user_pin_hide", "retry_reconnect", "data_version_drift",
)
_MULTI_TURN_VARIANTS = 9  # ×4 类别 = 36


# ── 语料组装与展开 ───────────────────────────────────────────────────────────


def build_corpus() -> List[Scenario]:
    """确定性语料（顺序稳定；同输入同输出）。"""
    corpus: List[Scenario] = []
    for category, _make, _visual, count in _SINGLE_TURN_TEMPLATES:
        corpus.extend(
            _single_turn_scenario(category, v) for v in range(1, count + 1)
        )
    for category in _MULTI_TURN_CATEGORIES:
        corpus.extend(
            _multi_turn_variants(category, v)
            for v in range(1, _MULTI_TURN_VARIANTS + 1)
        )
    return corpus


def corpus_stats(corpus: List[Scenario]) -> Dict[str, Any]:
    multi = [s for s in corpus if len(s.turns) >= 3]
    return {
        "corpus_version": CORPUS_VERSION,
        "total": len(corpus),
        "core_single_turn": sum(1 for s in corpus if len(s.turns) < 3),
        "multi_turn": len(multi),
        "multi_turn_turn_range": (
            [min(len(s.turns) for s in multi), max(len(s.turns) for s in multi)]
            if multi else []
        ),
        "categories": sorted({s.category for s in corpus}),
        "faulted": sum(1 for s in corpus if s.faults),
    }


def dump_corpus(out_dir, *, indent: Optional[int] = None) -> List:
    """语料展开产物（JSON per scenario + index.csv）—— 提交供 review/diff。"""
    import csv
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus()
    index_rows = []
    written = []
    for scenario in corpus:
        path = out / f"{scenario.scenario_id}.json"
        path.write_text(
            _scenario_to_json(scenario, indent=indent), encoding="utf-8")
        written.append(path)
        index_rows.append({
            "scenario_id": scenario.scenario_id,
            "category": scenario.category,
            "turns": len(scenario.turns),
            "faults": ";".join(f.get("type", "") for f in scenario.faults),
            "dispatch_backed": int(scenario.dispatch_backed),
        })
    with (out / "index.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["scenario_id", "category", "turns", "faults",
                            "dispatch_backed"])
        writer.writeheader()
        writer.writerows(index_rows)
    return written


def _scenario_to_json(scenario: Scenario, *, indent: Optional[int] = None) -> str:
    import json

    def _op_dict(op: ScenarioOp) -> Dict[str, Any]:
        return {
            "call_id": op.call_id, "tool": op.tool,
            "arguments": op.arguments, "result": op.result,
            "is_error": op.is_error, "error_msg": op.error_msg,
            "duration_ms": op.duration_ms,
        }

    def _turn_dict(turn: TurnSpec) -> Dict[str, Any]:
        return {
            "user_input": turn.user_input,
            "ops": [_op_dict(op) for op in turn.ops],
            "mutations": turn.mutations,
            "visual_report": turn.visual_report,
            "cartography": turn.cartography,
            "refs": turn.refs,
            "expect": turn.expect,
        }

    return json.dumps({
        "scenario_id": scenario.scenario_id,
        "category": scenario.category,
        "description": scenario.description,
        "schema_version": scenario.schema_version,
        "dispatch_backed": scenario.dispatch_backed,
        "faults": scenario.faults,
        "tags": scenario.tags,
        "turns": [_turn_dict(t) for t in scenario.turns],
    }, ensure_ascii=False, indent=indent, sort_keys=True, default=str)


if __name__ == "__main__":  # pragma: no cover — 语料展开 CLI
    import argparse

    parser = argparse.ArgumentParser(
        description="展开 replay 场景语料（JSON + index.csv）")
    parser.add_argument("--out", default="tests/fixtures/replay/scenarios")
    parser.add_argument("--indent", type=int, default=None)
    args = parser.parse_args()
    files = dump_corpus(args.out, indent=args.indent)
    stats = corpus_stats(build_corpus())
    print(f"wrote {len(files)} scenarios to {args.out}")
    print(stats)
