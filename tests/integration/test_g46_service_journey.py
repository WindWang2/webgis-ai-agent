"""G46 服务旅程集成测试：两图一地图联合探索闭环（服务层真实链路）。

真实链：session_data_manager.store（强版本 ref）→ freeze_source（G41 冻结）
→ contract 组合编译 → evaluate_where 求值 → buckets 分桶 → selection
handle → exploration_views 持久化（CAS/锚校验）→ MapSpec apply → 导出三
格式（compile_mapspec_to_svg_detailed + rsvg/WeasyPrint）回读。

oracle：期望计数/桶归属全部独立手算（fixture 属性表枚举），不调用生产
evaluator 生成 expected。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.linked_exploration import contract, service
from app.services.linked_exploration.errors import LinkedExplorationError

pytestmark = pytest.mark.asyncio

SESSION = "g46-journey-session"
OTHER_SESSION = "g46-journey-other"
ACTOR = {"user_id": "g46-user", "org_id": None, "owner_token": None,
         "trusted_session_id": SESSION}


def _square(x: float, y: float):
    return {"type": "Polygon", "coordinates": [[
        [x, y], [x + 0.01, y], [x + 0.01, y + 0.01], [x, y + 0.01], [x, y],
    ]]}


def _fc():
    """种子数据：typed 边界全覆盖（独立手算 oracle 见各断言）。

    cat 维度: a×4 / b×3 / c×2（c 是低频类别）; pop: 0..450 步进 50
    + NaN/Inf/字符串/缺失各 1。
    """
    feats = []
    # (cat, pop) 序列 —— 手算表：
    plan = [
        ("a", 0.0), ("a", 50.0), ("a", 100.0), ("a", 150.0),
        ("b", 100.0), ("b", 200.0), ("b", 300.0),
        ("c", 400.0), ("c", 450.0),
    ]
    for i, (cat, pop) in enumerate(plan):
        feats.append({
            "type": "Feature",
            "properties": {
                "_wfid": f"g46-{i}", "cat": cat, "pop": pop, "idx": i,
            },
            "geometry": _square(116.0 + i * 0.02, 39.0),
        })
    # typed 边界行（E06/E07/E09）
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-n1", "cat": 1, "pop": 250.0}, "geometry": _square(116.5, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-s1", "cat": "1", "pop": 250.0}, "geometry": _square(116.52, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-b1", "cat": True, "pop": 50.0}, "geometry": _square(116.54, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-null", "cat": None, "pop": 350.0}, "geometry": _square(116.56, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-miss", "pop": 350.0}, "geometry": _square(116.58, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-empty", "cat": "", "pop": 100.0}, "geometry": _square(116.6, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-nan", "cat": "a", "pop": float("nan")},
        "geometry": _square(116.62, 39.0)})
    feats.append({"type": "Feature", "properties": {
        "_wfid": "g46-str", "cat": "b", "pop": "x"}, "geometry": _square(116.64, 39.0)})
    return {"type": "FeatureCollection", "crs": "EPSG:4326",
            "features": feats}


REF: str = ""


async def _seed() -> str:
    from app.services.session_data import session_data_manager

    global REF
    if not REF:
        REF = await session_data_manager.store(SESSION, _fc(), prefix="g46fc")
    return REF


def _cat_condition(values, include_null=False, chart="ch-cat", field="cat"):
    return contract.parse_condition({
        "kind": "category", "condition_id": f"cond-{chart}", "chart_id": chart,
        "field": field, "values": values, "include_null": include_null,
    })


def _rng_condition(**bounds):
    return contract.parse_condition({
        "kind": "range", "condition_id": "cond-ch-rng", "chart_id": "ch-rng",
        "field": "pop", **bounds,
    })


def _charts():
    return [
        contract.ChartSpec(chart_id="ch-cat", kind="category", field="cat"),
        contract.ChartSpec(chart_id="ch-rng", kind="range", field="pop", bins=5),
    ]


class TestInspectAndPreview:
    async def test_inspect_anchor_strong_revision(self):
        ref = await _seed()
        out = await service.inspect_source(SESSION, ref, ACTOR)
        assert out["anchor"]["ref_id"] == ref
        assert out["anchor"]["content_revision"] >= 1
        assert out["feature_count"] == 17

    async def test_combined_intersection_e02(self):
        """图 A 选类别 + 图 B 选区间 → 交集（手算）。"""
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(),
            [_cat_condition(["a"]), _rng_condition(gte=100, lte=150)], ACTOR)
        # 手算：cat=a 且 pop∈[100,150] → g46-2(100), g46-3(150) = 2
        assert out["match_count"] == 2
        assert out["match_count_exact"] is True
        assert out["map_filter"] is not None

    async def test_same_dimension_or_e03(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(), [_cat_condition(["a", "b"])], ACTOR)
        # 手算：cat∈{a,b} → a×4(+nan 行 cat=a) + b×3(+str 行 cat=b) = 9
        assert out["match_count"] == 9

    async def test_typed_identity_e06(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(), [_cat_condition([1])], ACTOR)
        # 手算：只有 g46-n1 的 cat 是数值 1 → 1（"1"/True 不命中）
        assert out["match_count"] == 1
        out2 = await service.preview_conditions(
            SESSION, ref, _charts(), [_cat_condition(["1"])], ACTOR)
        assert out2["match_count"] == 1  # g46-s1
        out3 = await service.preview_conditions(
            SESSION, ref, _charts(), [_cat_condition([True])], ACTOR)
        assert out3["match_count"] == 1  # g46-b1

    async def test_null_and_missing_e07(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(),
            [_cat_condition([], include_null=True)], ACTOR)
        # 手算：显式 null（g46-null）+ 缺席（g46-miss）= 2
        assert out["match_count"] == 2

    async def test_breakpoint_bounds_e08(self):
        ref = await _seed()
        # 闭区间端点：pop∈[100,100] 恰好命中 g46-2(100) 与 g46-empty(100) 与 g46-4(100)
        out = await service.preview_conditions(
            SESSION, ref, _charts(), [_rng_condition(gte=100, lte=100)], ACTOR)
        # 手算：pop=100 → g46-2, g46-4, g46-empty = 3
        assert out["match_count"] == 3
        # 开下界：pop>100 且 <=150 → g46-3(150) = 1
        out2 = await service.preview_conditions(
            SESSION, ref, _charts(), [_rng_condition(gt=100, lte=150)], ACTOR)
        assert out2["match_count"] == 1

    async def test_nan_inf_disclosed_e09(self):
        ref = await _seed()
        rng_chart = [contract.ChartSpec(
            chart_id="ch-rng", kind="range", field="pop", bins=2)]
        out = await service.preview_conditions(
            SESSION, ref, rng_chart, [], ACTOR)
        full = out["charts"][0]["full"]["statistics"]
        # 手算：有限数值 15 个；nan 1；字符串 1；缺失 0（pop 都有，除……全部有 pop 键）
        assert full["valid_count"] == 15
        assert full["invalid_count"] == 1   # g46-nan
        assert full["non_numeric_count"] == 1  # g46-str
        assert full["matched"] == 17

    async def test_empty_set_e10(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(),
            [_cat_condition(["a"]), _rng_condition(gte=10000)], ACTOR)
        assert out["match_count"] == 0
        assert out["match_count_exact"] is True

    async def test_facet_denominator_e11(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(),
            [_cat_condition(["a"]), _rng_condition(gte=100)], ACTOR)
        rng = next(c for c in out["charts"] if c["chart_id"] == "ch-rng")
        # facet（排除自身区间条件）：cat=a → 5 行（含 nan 行）
        assert rng["facet"]["statistics"]["matched"] == 5
        # full（全条件）：cat=a 且 pop>=100 → g46-2(100), g46-3(150) = 2
        assert rng["full"]["statistics"]["matched"] == 2
        cat = next(c for c in out["charts"] if c["chart_id"] == "ch-cat")
        # cat 图 facet（排除自身分类条件）：pop>=100 → 手算：100×3,150,200,300,400,450,250,250,350,350 = 12
        assert cat["facet"]["statistics"]["matched"] == 12
        assert cat["full"]["statistics"]["matched"] == 2

    async def test_other_bucket_typed_values_e13(self):
        """低频类别 c 溢出到 other（max_groups 缩小场景用直接分桶测试）。
        这里用全量分桶验证 __other__ 真实类别分立（E12）+ 溢出披露结构。"""

        ref = await _seed()
        frozen_stats = await service.preview_conditions(
            SESSION, ref, _charts(), [], ACTOR)
        cat_stats = frozen_stats["charts"][0]["full"]["statistics"]
        # 手算类别分桶：a=5(含 nan 行), b=4(含 str 行), c=2, null=2(显式+缺席),
        # 1=1, "1"=1, true=1, ""=1 → 8 组（无溢出，max=64）
        assert cat_stats["group_count"] == 8
        assert cat_stats["truncated"] is False
        labels = {r["label"] for r in cat_stats["rows"]}
        assert "（空值）" in labels

    async def test_selection_handle_created_e14(self):
        ref = await _seed()
        out = await service.preview_conditions(
            SESSION, ref, _charts(), [_cat_condition(["a"])], ACTOR,
            create_handle=True)
        handle = out["selection_handle"]
        assert handle["handle"]
        assert handle["count"] == 5
        assert handle["count_exact"] is True


class TestViewPersistence:
    async def test_save_open_roundtrip_e19(self):
        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = {
            "schema_version": 2,
            "anchor": {
                "ref_id": ref,
                "content_revision": inspect["anchor"]["content_revision"],
                "content_fingerprint": inspect["anchor"]["content_fingerprint"],
            },
            "charts": [
                {"chart_id": "ch-cat", "kind": "category", "field": "cat",
                 "aggregation": "count", "value_field": None, "title": "",
                 "bins": 10, "layer_id": None},
                {"chart_id": "ch-rng", "kind": "range", "field": "pop",
                 "aggregation": "count", "value_field": None, "title": "",
                 "bins": 5, "layer_id": None},
            ],
            "conditions": [
                {"kind": "category", "condition_id": "cond-ch-cat",
                 "chart_id": "ch-cat", "field": "cat", "values": ["a"],
                 "include_null": False},
                {"kind": "range", "condition_id": "cond-ch-rng",
                 "chart_id": "ch-rng", "field": "pop", "gte": 100},
            ],
            "base_scope_note": "",
        }
        view = await service.save_view(
            SESSION, ACTOR, title="两图一地图视图", plan_raw=plan)
        assert view["revision"] == 1

        opened = await service.open_view(view["view_id"], SESSION, ACTOR)
        assert opened["state"] == "ready"
        # 重放预览与直接 preview 一致（同代）
        # 手算：cat=a（5 行：0,50,100,150,nan）且 pop>=100（nan 不命中）
        # → g46-2(100), g46-3(150) = 2
        assert opened["preview"]["match_count"] == 2

    async def test_stale_after_source_advance_e20_e21(self):
        """独立 session/ref：源推进（overwrite）会污染共享 ref 内容，
        本用例自建隔离源（不与其它用例共享 REF）。"""
        from app.services.session_data import session_data_manager

        sid = "g46-journey-stale"
        ref = await session_data_manager.store(sid, _fc(), prefix="g46stale")
        actor = {**ACTOR, "trusted_session_id": sid}
        inspect = await service.inspect_source(sid, ref, actor)
        plan = {
            "schema_version": 2,
            "anchor": inspect["anchor"] if "ref_id" in inspect["anchor"] else {
                "ref_id": ref,
                "content_revision": inspect["anchor"]["content_revision"],
                "content_fingerprint": inspect["anchor"]["content_fingerprint"],
            },
            "charts": [{"chart_id": "c", "kind": "category", "field": "cat",
                        "aggregation": "count", "value_field": None,
                        "title": "", "bins": 10, "layer_id": None}],
            "conditions": [], "base_scope_note": "",
        }
        view = await service.save_view(sid, actor, title="锚视图", plan_raw=plan)
        # 源推进（overwrite → revision +1，ABA：同内容也推进 generation）
        fc2 = _fc()
        fc2["features"] = fc2["features"][:5]
        ok = await session_data_manager.overwrite(sid, ref, fc2)
        assert ok is True
        opened = await service.open_view(view["view_id"], sid, actor)
        assert opened["state"] == "stale"
        assert opened["stale_reason"]["code"] == "SOURCE_ADVANCED"
        # 保存也要求重新预览
        with pytest.raises(LinkedExplorationError) as ei:
            await service.save_view(sid, actor, title="旧锚保存",
                                    plan_raw=plan)
        assert ei.value.code == "STALE_SOURCE"

    async def test_cas_conflict_e25(self):
        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [], "conditions": [], "base_scope_note": "",
        }
        view = await service.save_view(SESSION, ACTOR, title="t", plan_raw=plan)
        # 并发修改 → 第二个 CAS 失败
        await service.update_view(
            view["view_id"], SESSION, ACTOR, title="v2", plan_raw=None,
            status=None, expected_revision=1)
        with pytest.raises(LinkedExplorationError) as ei:
            await service.update_view(
                view["view_id"], SESSION, ACTOR, title="v3", plan_raw=None,
                status=None, expected_revision=1)
        assert ei.value.code == "STALE_VIEW_REVISION"
        assert ei.value.detail["current_revision"] == 2

    async def test_cross_session_denied_e27(self):
        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = {"schema_version": 2, "anchor": inspect["anchor"],
                "charts": [], "conditions": "", "base_scope_note": ""}
        plan["conditions"] = []
        view = await service.save_view(SESSION, ACTOR, title="x", plan_raw=plan)
        with pytest.raises(LinkedExplorationError) as ei:
            await service.get_view(view["view_id"], OTHER_SESSION, ACTOR)
        assert ei.value.code == "VIEW_NOT_FOUND"
        with pytest.raises(LinkedExplorationError):
            await service.open_view(view["view_id"], OTHER_SESSION, ACTOR)

    async def test_weak_source_rejected_e22(self):
        """无强版本的源（非 ref:）→ typed 拒绝。"""
        with pytest.raises(LinkedExplorationError) as ei:
            await service.inspect_source(SESSION, "https://tiles.example/x", ACTOR)
        assert ei.value.code in ("NO_STRONG_REVISION",)


class TestApplyToMapspec:
    async def test_apply_writes_components_and_rollback_e26(self):
        from app.services.mapspec_store import mapspec_store

        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [
                {"chart_id": "chA", "kind": "category", "field": "cat",
                 "aggregation": "count", "value_field": None, "title": "A",
                 "bins": 10, "layer_id": None},
            ],
            "conditions": [], "base_scope_note": "",
        }
        view = await service.save_view(SESSION, ACTOR, title="apply 视图",
                                       plan_raw=plan)
        result = await service.apply_view_to_mapspec(
            view["view_id"], SESSION, ACTOR)
        assert result["state"] == "applied"
        assert result["count"] == 1
        spec = await mapspec_store.get_mapspec(SESSION)
        components = ((spec.get("layout") or {}).get("components")
                      if isinstance(spec, dict) else None) or []
        if isinstance(components, dict):
            components = list(components.values())
        ids = [c.get("id") if isinstance(c, dict) else str(c) for c in components]
        assert "exploration-chart-chA" in ids, ids


class TestExport:
    async def test_export_svg_readback_e30(self):
        from app.services.mapspec_store import mapspec_store
        from app.services.export_paths import exports_root

        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        # 先给会话一个有真实图层的 MapSpec（导出链需要图层渲染面）
        base_spec = await mapspec_store.get_mapspec(SESSION) or {
            "version": "2", "view": {"bounds": [116.0, 38.9, 116.8, 39.1]},
            "layers": [], "layout": {"components": []},
        }
        base_spec = dict(base_spec)
        base_spec["layers"] = [{
            "id": "g46-layer", "type": "fill",
            "source": {"ref_id": ref},
            "paint": {"fill-color": "#3388aa", "fill-opacity": 0.7},
        }]
        await mapspec_store.save_mapspec(SESSION, base_spec)

        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [
                {"chart_id": "ch-cat", "kind": "category", "field": "cat",
                 "aggregation": "count", "value_field": None, "title": "类别",
                 "bins": 10, "layer_id": "g46-layer"},
            ],
            "conditions": [
                {"kind": "category", "condition_id": "cond-ch-cat",
                 "chart_id": "ch-cat", "field": "cat", "values": ["a"],
                 "include_null": False},
            ],
            "base_scope_note": "",
        }
        view = await service.save_view(SESSION, ACTOR, title="导出视图",
                                       plan_raw=plan)
        from app.services.linked_exploration.export import export_exploration

        result = await export_exploration(
            SESSION, ACTOR, fmt="svg", view_id=view["view_id"])
        assert result["success"] is True
        assert result["match_count"] == 5
        assert result["filtered_layer_ids"] == ["g46-layer"]
        # 回读：SVG 文件存在且可解析（filter 生效由 manifest/feature_count 承载）
        url = result["download_url"]
        fname = url.rsplit("/", 1)[-1].split("?")[0]
        candidates = list(Path(exports_root()).rglob(fname))
        assert candidates, f"exported file not found: {fname}"
        svg_text = candidates[0].read_text(encoding="utf-8")
        assert "联合探索导出" in svg_text


class TestExportAllFormats:
    """E30：三格式真实回读（重资源阶段 —— 同仓统一锁）。"""

    @staticmethod
    def _heavy_lock():
        import fcntl
        import subprocess as sp

        lock_dir = sp.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, check=True).stdout.strip()
        lock_path = Path(lock_dir) / "webgis-heavy-stage.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return fh

    async def test_png_pdf_bytes_e30(self):
        from app.services.linked_exploration.export import export_exploration
        from app.services.mapspec_store import mapspec_store

        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        spec = await mapspec_store.get_mapspec(SESSION)
        assert spec is not None  # 前一用例已建立含 g46-layer 的 spec
        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [
                {"chart_id": "ch-cat", "kind": "category", "field": "cat",
                 "aggregation": "count", "value_field": None, "title": "类别",
                 "bins": 10, "layer_id": "g46-layer"},
            ],
            "conditions": [
                {"kind": "category", "condition_id": "cond-ch-cat",
                 "chart_id": "ch-cat", "field": "cat", "values": ["a"],
                 "include_null": False},
            ],
            "base_scope_note": "",
        }
        view = await service.save_view(SESSION, ACTOR, title="三格式视图",
                                       plan_raw=plan)
        lock = self._heavy_lock()
        try:
            png = await export_exploration(
                SESSION, ACTOR, fmt="png", view_id=view["view_id"], dpi=150)
            pdf = await export_exploration(
                SESSION, ACTOR, fmt="pdf", view_id=view["view_id"], dpi=150)
        finally:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        assert png["success"] and png["format"] == "png"
        assert pdf["success"] and pdf["format"] == "pdf"
        # 三格式同计划/同版本：digest 与匹配数一致（E30 核心）
        assert png["conditions_digest"] == pdf["conditions_digest"]
        assert png["match_count"] == pdf["match_count"] == 5
        from app.services.export_paths import exports_root

        png_name = png["download_url"].rsplit("/", 1)[-1].split("?")[0]
        pdf_name = pdf["download_url"].rsplit("/", 1)[-1].split("?")[0]
        png_file = next(iter(Path(exports_root()).rglob(png_name)))
        pdf_file = next(iter(Path(exports_root()).rglob(pdf_name)))
        png_bytes = png_file.read_bytes()
        pdf_bytes = pdf_file.read_bytes()
        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 魔数（真实栅格化）
        assert pdf_bytes[:5] == b"%PDF-"               # PDF 魔数（真实出版）
        # PNG 尺寸头（IHDR）：非零宽高
        import struct

        w, h = struct.unpack(">II", png_bytes[16:24])
        assert w > 0 and h > 0


class TestReviewRegressions:
    """独立 review P1/P2 修复的回归锚。"""

    async def _plan(self, ref, inspect, chart_ids):
        return {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [
                {"chart_id": cid, "kind": "category", "field": "cat",
                 "aggregation": "count", "value_field": None, "title": cid,
                 "bins": 10, "layer_id": None}
                for cid in chart_ids
            ],
            "conditions": [], "base_scope_note": "",
        }

    async def test_apply_cas_chain_multi_chart_p1_1(self):
        """P1-1：多图表 + expected_revision CAS 链必须全量成功。

        此前 revision 键误读取（不存在的 "revision"）→ 第二个组件永拿旧
        CAS 令牌被拒 → 整体回滚。
        """
        from app.services.mapspec_store import mapspec_store

        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = await self._plan(ref, inspect, ["chA", "chB"])
        view = await service.save_view(SESSION, ACTOR, title="两图 CAS",
                                       plan_raw=plan)
        # 真实 CAS 基线（session_data 的 _cartographic_mutation_revision；
        # 前序用例可能已推进 —— 不能假设 1）
        from app.services.session_data import session_data_manager

        base_rev = await session_data_manager.get_state_field(
            SESSION, "_cartographic_mutation_revision")
        base_rev = int(base_rev or 0)
        result = await service.apply_view_to_mapspec(
            view["view_id"], SESSION, ACTOR, expected_revision=base_rev)
        assert result["state"] == "applied"
        assert result["count"] == 2
        spec = await mapspec_store.get_mapspec(SESSION)
        ids = [c.get("id") for c in (spec.get("layout") or {}).get("components", [])
               if isinstance(c, dict)]
        assert "exploration-chart-chA" in ids and "exploration-chart-chB" in ids

    async def test_pi_views_list_branch_p1_2(self):
        """P1-2：list_views 返回 list（工具 list 分支契约锚）。"""
        ref = await _seed()
        inspect = await service.inspect_source(SESSION, ref, ACTOR)
        plan = await self._plan(ref, inspect, ["c1"])
        await service.save_view(SESSION, ACTOR, title="v", plan_raw=plan)
        listed = await service.list_views(SESSION, ACTOR)
        assert isinstance(listed, list) and len(listed) >= 1

    async def test_update_view_revalidates_anchor_p2_1(self):
        """P2-1：PATCH 换 plan 必须重新冻结核对锚（不可绕过 save 面）。"""
        from app.services.session_data import session_data_manager

        sid = "g46-journey-p21"
        ref = await session_data_manager.store(sid, _fc(), prefix="g46p21")
        actor = {**ACTOR, "trusted_session_id": sid}
        inspect = await service.inspect_source(sid, ref, actor)
        plan = await self._plan(ref, inspect, ["c1"])
        view = await service.save_view(sid, actor, title="t", plan_raw=plan)
        # 源推进后 PATCH 旧锚计划 → STALE_SOURCE（与 POST 同面）
        fc2 = _fc()
        fc2["features"] = fc2["features"][:3]
        await session_data_manager.overwrite(sid, ref, fc2)
        with pytest.raises(LinkedExplorationError) as ei:
            await service.update_view(
                view["view_id"], sid, actor, title=None, plan_raw=plan,
                status=None, expected_revision=1)
        assert ei.value.code == "STALE_SOURCE"

    async def test_export_production_layer_shape_p1_3(self):
        """P1-3：生产形态图层（source=key + sources 表 ref entry）导出过滤生效。"""
        from app.services.export_paths import exports_root
        from app.services.linked_exploration.export import export_exploration
        from app.services.mapspec_store import mapspec_store

        sid = "g46-journey-p13"
        from app.services.session_data import session_data_manager

        ref = await session_data_manager.store(sid, _fc(), prefix="g46p13")
        actor = {**ACTOR, "trusted_session_id": sid}
        inspect = await service.inspect_source(sid, ref, actor)
        # 生产形态：source 是 sources 表 key；entry 带 ref（mutation_handlers 同款）
        await mapspec_store.save_mapspec(sid, {
            "version": "2", "view": {"bounds": [116.0, 38.9, 116.8, 39.1]},
            "sources": {"src-1": {"type": "geojson", "ref": ref,
                                   "ref_id": ref}},
            "layers": [{"id": "prod-layer", "type": "fill", "source": "src-1",
                        "paint": {"fill-color": "#3388aa"}}],
            "layout": {"components": []},
        })
        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [{"chart_id": "ch-cat", "kind": "category", "field": "cat",
                        "aggregation": "count", "value_field": None, "title": "类别",
                        "bins": 10, "layer_id": "prod-layer"}],
            "conditions": [
                {"kind": "category", "condition_id": "cond-ch-cat",
                 "chart_id": "ch-cat", "field": "cat", "values": ["a"],
                 "include_null": False}],
            "base_scope_note": "",
        }
        view = await service.save_view(sid, actor, title="生产形态导出",
                                       plan_raw=plan)
        result = await export_exploration(sid, actor, fmt="svg",
                                          view_id=view["view_id"])
        assert result["filtered_layer_ids"] == ["prod-layer"]
        assert result["match_count"] == 5  # oracle：cat=a 5 行
        # SVG 真实渲染了过滤面（非空图层）
        fname = result["download_url"].rsplit("/", 1)[-1].split("?")[0]
        svg_file = next(iter(Path(exports_root()).rglob(fname)))
        svg = svg_file.read_text(encoding="utf-8")
        assert "联合探索导出" in svg
        assert "mapspec-vector-layers" in svg

    async def test_export_no_target_layer_rejected(self):
        """P1-3 披露面：有条件但 committed MapSpec 无引用层 → typed 拒绝。"""
        from app.services.linked_exploration.export import export_exploration
        from app.services.session_data import session_data_manager

        sid = "g46-journey-p13b"
        ref = await session_data_manager.store(sid, _fc(), prefix="g46p13b")
        actor = {**ACTOR, "trusted_session_id": sid}
        inspect = await service.inspect_source(sid, ref, actor)
        from app.services.mapspec_store import mapspec_store

        await mapspec_store.save_mapspec(sid, {
            "version": "2", "view": {"bounds": [116.0, 38.9, 116.8, 39.1]},
            "layers": [], "layout": {"components": []},
        })
        plan = {
            "schema_version": 2, "anchor": inspect["anchor"],
            "charts": [], "conditions": [
                {"kind": "category", "condition_id": "c", "chart_id": "ch",
                 "field": "cat", "values": ["a"], "include_null": False}],
            "base_scope_note": "",
        }
        with pytest.raises(LinkedExplorationError) as ei:
            await export_exploration(sid, actor, fmt="svg", plan_raw=plan)
        assert ei.value.code == "EXPORT_NO_TARGET_LAYER"
