"""Contract kernel 兼容回归（ADR-0216）。

锁定三件事：
1. **旧 import path 全部可用** —— services 侧 re-export 面（shim）与
   kernel 符号同一对象；下移不许破坏任何既有消费方。
2. **kernel 零依赖纪律** —— app.contracts 不 import 任何上层包
   （与 scripts/check_import_boundaries.py 双保险，这里做 import 级断言）。
3. **行为不变量** —— 下沉是"原样搬家"：估工公式单调性与数值、锁族前缀
   语义、finding 截断边界，在搬家前后必须逐位一致。
"""
from __future__ import annotations

import importlib
import sys

import pytest


# ── 1. 旧 import path 兼容（shim == kernel 同一对象）────────────────────────


@pytest.mark.parametrize(
    ("legacy_module", "names"),
    [
        (
            "app.services.governor.render_budget",
            ["RenderWorkInput", "export_dpi_factor", "render_work_units"],
        ),
        (
            "app.services.gis_harness.components",
            [
                "ComponentType", "Position", "ComponentPlacement",
                "CartographyComponent", "MULTI_INSTANCE_TYPES",
                "normalize_placement", "valid_variants_for_type",
                "coerce_variant", "build_default_components",
                "mutate_component", "remove_component", "duplicate_component",
                "rebind_component", "title_component", "label_layer_component",
                "validate_annotation_payload", "validate_chart_payload",
            ],
        ),
        (
            "app.services.mapspec.lifecycle_engine",
            [
                "SetViewIntent", "SetSceneIntent",
                "PatchLayerPresentationIntent",
                "LOCK_CONFLICT_CODE", "locked_layer_ids_of",
                "locked_component_ids_of", "is_entity_locked",
            ],
        ),
        (
            "app.services.gis_harness.completion.contracts",
            ["F_RENDER_APPLY_FAILED", "MAX_FINDING_DETAIL", "MapCompletionFinding"],
        ),
        ("app.services.gis_harness.map_completion", ["MapCompletionFinding"]),
        ("app.lib.runtime.context", ["RuntimeContext", "current_runtime_context", "bind_runtime_context"]),
        ("app.tools._utils", ["async_db_session"]),
    ],
)
def test_legacy_import_paths_still_work(legacy_module, names):
    mod = importlib.import_module(legacy_module)
    for name in names:
        assert hasattr(mod, name), f"{legacy_module}.{name} 消失 —— 旧 path 回归"


def test_shim_reexports_are_kernel_identity():
    """shim 暴露的契约符号与 kernel 是同一对象（不是副本/子类）。"""
    from app.services.gis_harness import components as legacy
    from app.contracts import cartography_components as kernel

    assert legacy.CartographyComponent is kernel.CartographyComponent
    assert legacy.ComponentType is kernel.ComponentType
    assert legacy.ComponentPlacement is kernel.ComponentPlacement
    assert legacy.MULTI_INSTANCE_TYPES is kernel.MULTI_INSTANCE_TYPES

    from app.services.governor import render_budget
    from app.contracts import render_work

    assert render_budget.RenderWorkInput is render_work.RenderWorkInput
    assert render_budget.render_work_units is render_work.render_work_units

    from app.services.mapspec import lifecycle_engine
    from app.contracts import mapspec_intents, workbench_locks

    assert lifecycle_engine.SetSceneIntent is mapspec_intents.SetSceneIntent
    assert lifecycle_engine.LOCK_CONFLICT_CODE is workbench_locks.LOCK_CONFLICT_CODE
    assert lifecycle_engine.is_entity_locked is workbench_locks.is_entity_locked

    from app.services.gis_harness.completion import contracts as completion
    from app.contracts import completion as kernel_completion

    assert completion.MapCompletionFinding is kernel_completion.MapCompletionFinding
    assert completion.F_RENDER_APPLY_FAILED == kernel_completion.F_RENDER_APPLY_FAILED


def test_kernel_modules_importable_without_services_side_effects():
    """kernel 子模块可独立 import —— import 期不拉起 services/lib 上层。"""
    for name in (
        "app.contracts.render_work",
        "app.contracts.cartography_components",
        "app.contracts.mapspec_intents",
        "app.contracts.workbench_locks",
        "app.contracts.completion",
        "app.contracts.session_access",
    ):
        mod = importlib.import_module(name)
        assert name in sys.modules


# ── 2. kernel 零依赖纪律（import 级双保险）───────────────────────────────────


@pytest.mark.parametrize(
    "kernel_module",
    [
        "app.contracts.render_work",
        "app.contracts.cartography_components",
        "app.contracts.mapspec_intents",
        "app.contracts.workbench_locks",
        "app.contracts.completion",
    ],
)
def test_kernel_module_does_not_import_upper_layers(kernel_module):
    """源码级断言（import 级会受同进程其他测试污染，gate 脚本已覆盖子进程版）。"""
    mod = importlib.import_module(kernel_module)
    import ast as _ast
    from pathlib import Path

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                ("app.services", "app.api", "app.lib", "app.tools",
                 "app.core", "app.schemas")
            ), f"{kernel_module} 反向 import 上层: {node.module}"
        if isinstance(node, _ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(
                    ("app.services", "app.api", "app.lib", "app.tools",
                     "app.core", "app.schemas")
                ), f"{kernel_module} 反向 import 上层: {alias.name}"


# ── 3. 行为不变量（原样搬家）────────────────────────────────────────────────


class TestRenderWorkInvariants:
    def test_formula_values_unchanged(self):
        from app.contracts.render_work import RenderWorkInput, render_work_units

        # master 原实现逐位对照值（系数 render_formula.v1 未动）
        empty = render_work_units(RenderWorkInput())
        assert empty == pytest.approx(200_000.0)
        one_layer = render_work_units(RenderWorkInput(layer_count=1))
        assert one_layer == pytest.approx(200_000.0 + 150_000.0)

    def test_defensive_clamping(self):
        from app.contracts.render_work import RenderWorkInput

        inp = RenderWorkInput(layer_count=-5, pixel_width=-1, symbol_complexity=99)
        assert inp.layer_count == 0
        assert inp.pixel_width == 0
        assert inp.symbol_complexity == 8.0  # cap

    def test_monotonicity(self):
        from app.contracts.render_work import RenderWorkInput, render_work_units

        base = RenderWorkInput(layer_count=1, feature_count=10)
        assert render_work_units(base) < render_work_units(
            RenderWorkInput(layer_count=2, feature_count=10)
        )
        assert render_work_units(base) < render_work_units(
            RenderWorkInput(layer_count=1, feature_count=20)
        )

    def test_dpi_factor(self):
        from app.contracts.render_work import export_dpi_factor

        assert export_dpi_factor(None) == 1.0
        assert export_dpi_factor(0) == 1.0
        assert export_dpi_factor(96) == 1.0
        assert export_dpi_factor(192) == pytest.approx(1.2 * 4.0)


class TestWorkbenchLockInvariants:
    def test_family_prefix_matching_is_bidirectional(self):
        from app.contracts.workbench_locks import is_entity_locked

        locked = frozenset({"roads"})
        assert is_entity_locked("roads", locked)
        assert is_entity_locked("roads-1", locked)      # 物理层挂逻辑锁
        assert is_entity_locked("roads__phys", locked)
        assert is_entity_locked("roads-1-label", locked)
        assert not is_entity_locked("road", locked)      # 前缀撞词不算
        assert not is_entity_locked("highways", locked)
        # 双向：locked 存物理层，目标是逻辑层
        assert is_entity_locked("roads", frozenset({"roads-1"}))

    def test_reader_bounds_and_shape(self):
        from app.contracts.workbench_locks import locked_layer_ids_of

        assert locked_layer_ids_of(None) == []
        assert locked_layer_ids_of({}) == []
        assert locked_layer_ids_of({"workbench": {}}) == []
        assert locked_layer_ids_of(
            {"workbench": {"lockedLayerIds": ["a", 1, "", "b"]}}
        ) == ["a", "b"]
        big = {"workbench": {"lockedLayerIds": [f"l{i}" for i in range(100)]}}
        assert len(locked_layer_ids_of(big)) == 64  # 有界披露

    def test_intent_dataclasses_shape(self):
        from app.contracts.mapspec_intents import (
            PatchLayerPresentationIntent,
            SetSceneIntent,
            SetViewIntent,
        )

        v = SetViewIntent(center=[1.0, 2.0], zoom=10)
        assert v.pitch is None and v.bearing is None
        p = PatchLayerPresentationIntent(layer_id="l1", visible=False, opacity=0.5)
        assert (p.layer_id, p.visible, p.opacity) == ("l1", False, 0.5)
        s = SetSceneIntent()
        assert s.scene is None


class TestCompletionFindingInvariants:
    def test_truncation_bounds(self):
        from app.contracts.completion import F_RENDER_APPLY_FAILED, MapCompletionFinding

        f = MapCompletionFinding(
            code=F_RENDER_APPLY_FAILED, severity="warning",
            target="t" * 100, detail="d" * 500,
        )
        d = f.to_dict()
        assert len(d["target"]) == 64
        assert len(d["detail"]) == 160
        assert d["code"] == "render_apply_failed"


class TestSessionAccessContract:
    async def test_runtime_checkable_protocol_accepts_async_history_service(self):
        from app.contracts.session_access import SessionMetaReader
        from app.services.history_service_async import AsyncHistoryService

        svc = AsyncHistoryService(db=None)
        assert isinstance(svc, SessionMetaReader)

    def test_unwired_factory_fails_explicitly(self, monkeypatch):
        import app.core.auth as auth

        monkeypatch.setattr(auth, "_session_meta_reader_factory", None)
        with pytest.raises(RuntimeError, match="composition root"):
            auth._get_session_meta_reader(db=None)

    async def test_guard_404_semantics_unchanged(self, monkeypatch):
        """守卫语义回归：读不到 → 统一 HTTPException(404, 'Session not found')。"""
        import app.core.auth as auth
        from fastapi import HTTPException

        class _FakeReader:
            async def get_session_meta(self, session_id, *, user_id=None, owner_token=None):
                return None

        monkeypatch.setattr(
            auth, "_session_meta_reader_factory", lambda db: _FakeReader()
        )
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_session_owner(db=None, session_id="missing")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Session not found"
