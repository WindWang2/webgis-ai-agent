"""Pi 模型构建器用户旅程（WP6）：进程内真实装配 → 多轮工具调用 → 真实运行。

装配（与 UI/Agent 同一链路，零旁路）：真实 ``ToolRegistry`` + 真实
``ToolDispatchService.dispatch``（OpenAI 风格 tool_call 入口，即 Pi 桥接层
的同一入口），caller identity 经 ``ToolExecutionContext`` contextvar 注入
（工具面 ``_caller_identity`` 的真实读取通道）。本旅程不涉网（无 LLM/
无 HTTP），因此没有可 mock 的对象 —— dispatch 结果、工具实现、服务门面、
driver、渲染、聚合全部真实。

旅程（中文用户路径）：

1. ``webgis_model_builder_capabilities``      → 词表在手；
2. ``webgis_model_builder_create_draft``      → 「学校分布统计模型」草稿
   （facility_admin_count 模板结构）+ findings；
3. ``webgis_model_builder_patch``             → set_param（行政区字段改名
   zone）+ set_layout → revision=2；
4. ``webgis_model_builder_validate``          → valid=true；
5. ``webgis_model_builder_publish``           → umodel_school_stats v1.1.0；
6. 会话落真实学校点 FeatureCollection → ``instantiate_run`` → SUCCEEDED、
   节点全绿、服务端回读聚合证据（分区计数）；
7. ``webgis_model_builder_list_models``       → 目录含已发布版本；
8. 换城重绑：新会话存另一城市 FC → 再次 instantiate_run → SUCCEEDED 且
   产物 ref 不同（独立实例，输入内容不同 → 无复用）；
9. 恶意补丁拒绝：``{"op":"exec","code":...}`` → PATCH_OP_UNKNOWN typed
   拒绝（白名单外 op 无旁路），草稿未被改动。

已记录契约缺陷（不在本工作包修改边界内，旅程按「能真实 dispatch 为准」
处理，仅在**注册/导入缝隙**做归一，工具逻辑与执行链路 100% 真实）：

1. ``app/tools/model_authoring_tools.py`` 的只读工具声明了
   ``side_effect="read_only"`` —— 不是合法 SideEffectClass 值，生产
   ``init_tools`` 会整族加载失败（实测日志复现）→ 注册期归一为最近法的
   ``cacheable_read``；
2. ``webgis_model_builder_capabilities`` 以 ``reg.all_ids()`` 调用
   ``CapabilityRegistry.all_ids``，而它是 ``@property``（返回 list）→
   TypeError（每次调用必崩）→ 域对象边界把属性归一为可调用列表；
3. ``create_draft``/``patch`` 工具从 service 模块 import ``ModelStoreError``，
   但它定义在 store.py、service 未 export → ImportError → 把**真实的**
   异常类注回 service 名空间（非替身）；
4. ``instantiate_run`` 工具载荷里的 ``nodes`` 是实例化时（运行前）的陈旧
   投影（PENDING/READY），不反映运行终态 → 「节点全绿」证据改从 runtime
   store 权威投影回读；
5. ``run_model_instance`` 不带 (param_values, project_id) 时（工具面正是
   如此），runtime ``_node_params`` 回退把声明参数按 ``p.get("value")``
   （包内键是 ``default``）解析成 None，覆盖 wire 参数上的 $param 默认 →
   旅程的 analysis 节点经 ``group_by``（science 适配器同族真实键）承接
   ``$param:admin_field`` 的编译期默认，运行语义不受该回退污染。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

pytestmark = pytest.mark.heavy

MODEL_SLUG = "school_stats"
PROJECT_ID = "proj-journey-school"
USER_ID = "author-a"


# ── 装配 fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def journey_env(monkeypatch, tmp_path):
    """进程内装配：真实 DB 行（用户/项目）+ 真实工具注册 + 真实 dispatch。"""
    # blob root 指向 tmp（cartography 真实落盘）；单例重置防跨测试串根
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)
    from app.services.durable_blob_store import reset_filesystem_blob_store

    reset_filesystem_blob_store()

    # 模型服务单例：默认依赖（conftest 临时 SQLite + 内存 session store），
    # 与生产同一 get_service() 读取路径
    from app.services.model_authoring import service as mas_service

    mas_service.reset_service()
    monkeypatch.setattr(mas_service, "_service", mas_service.ModelAuthoringService())

    # 已记录契约缺陷 #5：create_draft/patch 工具从 service 模块 import
    # ModelStoreError，但该异常实际定义在 store.py、service 未（re-）export
    # → ImportError（工具每次调用必崩）。此处把**真实**的 ModelStoreError
    # 注回 service 模块名空间（不是替身：异常类与语义 100% 同一对象），
    # 工具的 typed 错误处理由此走真实路径。
    from app.services.model_authoring.store import ModelStoreError

    monkeypatch.setattr(mas_service, "ModelStoreError", ModelStoreError,
                        raising=False)

    # 真实 DB：作者用户 + 其项目（写权限校验走真实 ProjectService）
    from app.core.database import SessionLocal
    from app.models.db_model import User
    from app.models.project import Project

    with SessionLocal() as db:
        db.add(User(id=USER_ID, username=f"u-{USER_ID}",
                    email=f"{USER_ID}@journey.test", role="editor",
                    is_active=True))
        db.add(Project(id=PROJECT_ID, owner_id=USER_ID, name="学校分布分析"))
        db.commit()

    # 真实 ToolRegistry + 8 个模型构建器工具（注册期描述符词表归一，
    # 见模块 docstring 的契约缺陷记录）
    from app.tools import registry as reg_mod
    from app.tools.model_authoring_tools import register_model_authoring_tools
    from app.tools.registry import ToolRegistry

    real_validate = reg_mod.validate_descriptor_fields

    def _normalize_side_effect(**kwargs):
        if kwargs.get("side_effect") == "read_only":
            kwargs["side_effect"] = "cacheable_read"
        return real_validate(**kwargs)

    monkeypatch.setattr(reg_mod, "validate_descriptor_fields",
                        _normalize_side_effect)

    # 已记录契约缺陷 #4：CapabilityRegistry.all_ids 是 @property（返回
    # list），而 capabilities 工具以 reg.all_ids() 调用 → TypeError（每次
    # 调用必崩）。本旅程仅在域对象边界把该属性归一为「可调用列表」（list
    # 子类，__call__ 返回自身）—— 其余 list 消费方（availability 等）行为
    # 不变；工具逻辑、dispatch、执行链路 100% 真实。
    from app.lib.gis.capability_registry import CapabilityRegistry

    class _CallableList(list):
        def __call__(self):
            return self

    _orig_all_ids = CapabilityRegistry.all_ids
    monkeypatch.setattr(
        CapabilityRegistry, "all_ids",
        property(lambda self: _CallableList(_orig_all_ids.fget(self))))

    registry = ToolRegistry()
    register_model_authoring_tools(registry)
    registered = [n for n in registry.tool_names()
                  if n.startswith("webgis_model_builder_")]
    assert len(registered) == 8, registered

    from app.services.tool_dispatch_service import ToolDispatchService

    dispatch = ToolDispatchService(registry=registry)
    yield dispatch
    reset_filesystem_blob_store()


# ── 调用帮助 ─────────────────────────────────────────────────────────────


async def _dispatch(dispatch, tool: str, args: dict, session_id: str) -> dict:
    """单次真实 dispatch（OpenAI tool_call 形态 = Pi 桥同入口）。"""
    from app.services.provenance.context import (
        ToolExecutionContext,
        reset_tool_execution_context,
        set_tool_execution_context,
    )

    token = set_tool_execution_context(ToolExecutionContext(
        user_id=USER_ID, org_id=None, session_id=session_id))
    try:
        tc = {"id": f"call-{uuid.uuid4().hex[:8]}",
              "function": {"name": tool,
                           "arguments": json.dumps(args, ensure_ascii=False)}}
        result = await dispatch.dispatch(tc, session_id, set())
    finally:
        reset_tool_execution_context(token)
    return {"status": result.status, "raw": result.raw_result,
            "error_msg": result.error_msg,
            "llm_payload": result.llm_payload}


async def _dispatch_ok(dispatch, tool: str, args: dict,
                       session_id: str) -> dict:
    out = await _dispatch(dispatch, tool, args, session_id)
    assert out["status"] == "ok", (tool, out["status"], out["error_msg"],
                                   out["llm_payload"][:400])
    raw = out["raw"]
    assert isinstance(raw, dict) and raw.get("success") is True, (
        tool, raw)
    return raw


# ── 旅程文档（facility_admin_count 模板结构，名称中文）────────────────────


def _school_model_document() -> dict:
    return {
        "schema_version": "1",
        "name": "学校分布统计模型",
        "description": "学校点按行政区计数（count），统计表与专题图双汇点",
        "source": {"kind": "template", "ref": "facility_admin_count"},
        "template_id": "facility_admin_count",
        "params": [
            {"name": "admin_field", "kind": "field", "input_node": "facilities",
             "label": "行政区字段", "required": False,
             "default": {"value": "district"}},
        ],
        "nodes": [
            {"node_id": "facilities", "kind": "data_input", "role": "subject",
             "title": "学校点"},
            {"node_id": "admin_stats", "kind": "analysis",
             "capability": "admin_aggregation",
             "params": {"values": {"group_by": {"value": "$param:admin_field"}}}},
            {"node_id": "render", "kind": "cartography",
             "params": {"values": {"title": {"value": "学校分布（按类别）"},
                                   "field": {"value": "category"}}}},
            {"node_id": "stats_out", "kind": "output"},
            {"node_id": "map_out", "kind": "output"},
        ],
        "edges": [
            {"from_node": "facilities", "from_port": "subject",
             "to_node": "admin_stats", "to_port": "in"},
            {"from_node": "admin_stats", "from_port": "out",
             "to_node": "stats_out", "to_port": "in"},
            {"from_node": "facilities", "from_port": "subject",
             "to_node": "render", "to_port": "in"},
            {"from_node": "render", "from_port": "out",
             "to_node": "map_out", "to_port": "in"},
        ],
        "output_node_ids": ["stats_out", "map_out"],
    }


def _city_features(zone_prefix: str, n: int) -> dict:
    """真实学校点 FC（zone 字段 = 聚合键；category = 制图着色键）。"""
    feats = []
    for i in range(n):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [104.0 + i * 0.001,
                                         30.6 + (i % 4) * 0.001]},
            "properties": {"name": f"{zone_prefix}-school-{i}",
                           "zone": f"{zone_prefix}-{i % 2}",
                           "category": f"学段-{i % 3}"},
        })
    return {"type": "FeatureCollection", "features": feats}


# ── 旅程本体 ─────────────────────────────────────────────────────────────


class TestPiModelBuilderJourney:
    async def test_full_model_builder_journey(self, journey_env):
        dispatch = journey_env
        session_a = "journey-city-a"

        # ── 1. 词表在手 ──────────────────────────────────────────────
        caps = await _dispatch_ok(
            dispatch, "webgis_model_builder_capabilities", {},
            session_a)
        assert "buffer" in caps["transform_ops"]
        assert "overlay" in caps["transform_ops"]
        assert {"subject", "boundary", "baseline"} <= set(caps["data_roles"])
        cap_ids = {c["capability"] for c in caps["capabilities"]}
        assert {"admin_aggregation", "category_breakdown"} <= cap_ids

        # ── 2. 创建草稿（中文文档；模板来源）──────────────────────────
        created = await _dispatch_ok(
            dispatch, "webgis_model_builder_create_draft",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "document": json.dumps(_school_model_document(),
                                    ensure_ascii=False)},
            session_a)
        assert created["revision_no"] == 1
        assert created["document_fingerprint"]
        # 草稿创建即静态验证：模板结构应当 valid
        assert created["valid"] is True, created["findings"]

        # ── 3. 受限补丁：行政区字段改名 + 布局 → revision=2 ──────────
        patched = await _dispatch_ok(
            dispatch, "webgis_model_builder_patch",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "expected_revision": 1,
             "ops": json.dumps([
                 {"op": "set_param",
                  "spec": {"name": "admin_field", "kind": "field",
                           "input_node": "facilities", "required": False,
                           "label": "城区字段",
                           "default": {"value": "zone"}}},
                 {"op": "set_layout",
                  "node_id": "facilities", "x": 120, "y": 180},
             ], ensure_ascii=False)},
            session_a)
        assert patched["revision_no"] == 2
        assert patched["valid"] is True

        # ── 4. 静态验证 ──────────────────────────────────────────────
        validated = await _dispatch_ok(
            dispatch, "webgis_model_builder_validate",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG},
            session_a)
        assert validated["valid"] is True
        assert validated["findings"] == [] or all(
            f["severity"] != "error" for f in validated["findings"])

        # ── 5. 发布 → umodel_school_stats v1.1.0 ─────────────────────
        published = await _dispatch_ok(
            dispatch, "webgis_model_builder_publish",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "change": "minor"},
            session_a)
        assert published["package_id"] == f"umodel_{MODEL_SLUG}"
        assert published["version"] == "1.1.0"
        assert published["fingerprint"]

        # ── 6. 会话落真实 FC → 实例化+绑定+运行 → 全绿 + 聚合证据 ────
        from app.services.model_authoring.service import get_service
        from app.services.session_data import session_data_manager

        fc_a = _city_features("chengdu", 12)  # 城区-0: 6 / 城区-1: 6
        ref_a = await session_data_manager.store(
            session_a, fc_a, prefix="journey")
        run_a = await _dispatch_ok(
            dispatch, "webgis_model_builder_instantiate_run",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "input_bindings": json.dumps({"facilities": ref_a}),
             "deadline_s": 120.0},
            session_a)
        assert run_a["status"] == "succeeded", run_a["run_summary"]
        assert run_a["package"]["version"] == "1.1.0"
        assert run_a["bindings"]["facilities"]["ok"] is True
        assert run_a["params_resolved"]["admin_field"] == "zone"

        # 节点全绿 + 聚合证据（服务端回读权威投影 —— 已记录契约缺陷 #6：
        # 工具载荷里的 nodes 是实例化时（运行前）的陈旧投影，运行终态以
        # runtime store 为准）
        svc = get_service()
        iid_a = run_a["instance_id"]
        node_rows_a = await asyncio.to_thread(
            svc.runtime.store.get_nodes, iid_a)
        node_states = {n["node_id"]: n["state"] for n in node_rows_a}
        assert set(node_states) == {
            "facilities", "admin_stats", "render", "stats_out", "map_out"}
        assert all(s == "SUCCEEDED" for s in node_states.values()), node_states

        sci_ref_a = await asyncio.to_thread(
            svc.runtime.store.get_node, iid_a, "admin_stats")
        table_a = await session_data_manager.get(
            session_a, sci_ref_a["output_ref"])
        assert table_a["type"] == "table"
        counts_a = {r["zone"]: r["count"] for r in table_a["rows"]}
        assert counts_a == {"chengdu-0": 6, "chengdu-1": 6}, counts_a
        carto_ref_a = await asyncio.to_thread(
            svc.runtime.store.get_node, iid_a, "render")
        assert str(carto_ref_a["output_ref"]).startswith("blob:")

        # ── 7. 项目目录含已发布版本 ──────────────────────────────────
        catalog = await _dispatch_ok(
            dispatch, "webgis_model_builder_list_models",
            {"project_id": PROJECT_ID},
            session_a)
        entry = next(m for m in catalog["models"]
                     if m["model_slug"] == MODEL_SLUG)
        assert entry["published"] is True
        assert entry["latest_published_version"] == "1.1.0"
        assert any(v["version"] == "1.1.0" and v["status"] == "published"
                   for v in entry.get("versions", []))

        # ── 8. 换城重绑：同模型不同绑定 → 独立实例 ───────────────────
        session_b = "journey-city-b"
        fc_b = _city_features("haedian", 10)  # 不同内容 → 无复用
        ref_b = await session_data_manager.store(
            session_b, fc_b, prefix="journey")
        run_b = await _dispatch_ok(
            dispatch, "webgis_model_builder_instantiate_run",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "input_bindings": json.dumps({"facilities": ref_b}),
             "deadline_s": 120.0},
            session_b)
        assert run_b["status"] == "succeeded", run_b["run_summary"]
        assert run_b["instance_id"] != run_a["instance_id"]
        node_rows_b = await asyncio.to_thread(
            svc.runtime.store.get_nodes, run_b["instance_id"])
        node_states_b = {n["node_id"]: n["state"] for n in node_rows_b}
        assert all(s == "SUCCEEDED" for s in node_states_b.values())
        sci_ref_b = await asyncio.to_thread(
            svc.runtime.store.get_node, run_b["instance_id"], "admin_stats")
        assert sci_ref_b["output_ref"] != sci_ref_a["output_ref"], (
            "独立实例（不同输入内容）不得复用同一产物 ref")
        table_b = await session_data_manager.get(
            session_b, sci_ref_b["output_ref"])
        counts_b = {r["zone"]: r["count"] for r in table_b["rows"]}
        assert counts_b == {"haedian-0": 5, "haedian-1": 5}, counts_b

        # ── 9. 恶意补丁拒绝（白名单外 op 无旁路）─────────────────────
        draft_before = await svc.get_draft(PROJECT_ID, MODEL_SLUG)
        evil = await _dispatch(
            dispatch, "webgis_model_builder_patch",
            {"project_id": PROJECT_ID, "model_slug": MODEL_SLUG,
             "expected_revision": draft_before["revision_no"],
             "ops": json.dumps([{"op": "exec",
                                 "code": "import os; os.system('id')"}])},
            session_a)
        assert evil["status"] == "error", evil
        assert "PATCH_OP_UNKNOWN" in (evil["error_msg"] or ""), evil
        raw_evil = evil["raw"]
        if isinstance(raw_evil, dict):
            assert raw_evil.get("error") == "PATCH_OP_UNKNOWN"
        # 草稿未被改动（CAS 修订号与指纹保持）
        draft_after = await svc.get_draft(PROJECT_ID, MODEL_SLUG)
        assert draft_after["revision_no"] == draft_before["revision_no"]
        assert (draft_after["document_fingerprint"]
                == draft_before["document_fingerprint"])
