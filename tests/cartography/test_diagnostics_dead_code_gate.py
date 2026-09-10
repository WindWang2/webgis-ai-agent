"""V6（ADR-0120 W1）诊断死码门 + 多帧聚合 Sink 契约。

死码门（R1-M2 收口）：词表内每个码必须在 EMITTER_REGISTRY 登记至少一个
真实发射点，且发射点可定位（后端模块可导入且源码含此码；前端文件存在且
源码含此码）。新增码未登记/发射点漂移 → 本文件红灯。
"""
from pathlib import Path

import pytest

from app.lib.cartography.render_diagnostics import (
    MAX_DIAGNOSTICS_PER_EXPORT,
    MAX_DIAGNOSTICS_PER_FRAME,
    RENDER_DIAGNOSTICS,
    DiagnosticSink,
    diagnostic,
    normalize_render_diagnostics,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_emitter_path(ref: str) -> Path:
    if ref.startswith("frontend/"):
        return REPO_ROOT / ref
    return REPO_ROOT / Path(*ref.split(".").__iter__()).with_suffix(".py")


class TestDeadCodeGate:
    def test_every_code_has_emitter_registration(self):
        missing = sorted(set(RENDER_DIAGNOSTICS) - set(__import__(
            "app.lib.cartography.render_diagnostics", fromlist=["EMITTER_REGISTRY"]
        ).EMITTER_REGISTRY))
        assert missing == [], f"诊断码缺少发射器注册（死码）: {missing}"

    def test_no_phantom_registry_entries(self):
        from app.lib.cartography.render_diagnostics import EMITTER_REGISTRY

        phantom = sorted(set(EMITTER_REGISTRY) - set(RENDER_DIAGNOSTICS))
        assert phantom == [], f"注册表含词表外码: {phantom}"

    @pytest.mark.parametrize("code", sorted(RENDER_DIAGNOSTICS))
    def test_emitter_sites_exist_and_reference_code(self, code):
        from app.lib.cartography.render_diagnostics import EMITTER_REGISTRY

        refs = EMITTER_REGISTRY[code]
        assert refs, f"{code} 注册表为空"
        for ref in refs:
            path = _resolve_emitter_path(ref)
            assert path.exists(), f"{code} 发射点不存在: {ref}"
            source = path.read_text(encoding="utf-8")
            assert f'"{code}"' in source or f"'{code}'" in source, (
                f"{code} 在发射点 {ref} 中无引用（发射器漂移/死码）"
            )

    @pytest.mark.parametrize("code", sorted(RENDER_DIAGNOSTICS))
    def test_every_code_constructible_with_real_message(self, code):
        d = diagnostic(code, detail="探针")
        assert d is not None
        assert d.severity in ("info", "warning", "error")
        assert d.message
        assert "探针" in d.message or d.detail == "探针"


class TestDiagnosticSink:
    def test_global_cap_truncates_with_meta_disclosure(self):
        sink = DiagnosticSink(max_total=5)
        for i in range(10):
            sink.add(diagnostic("features_truncated", detail=str(i)))
        items = sink.items()
        # cap+1 语义：5 条内容 + 1 条 meta 锚点（meta 不占内容配额）
        assert len(items) == 6
        assert sum(1 for i in items if i.code == "features_truncated") == 5
        assert sink.truncated is True
        # 元披露必须真的进入产物（而非只置标志）
        assert items[-1].code == "diagnostics_truncated"

    def test_frame_subquota(self):
        sink = DiagnosticSink()
        frame_items = [
            diagnostic("label_truncated", detail=str(i)) for i in range(MAX_DIAGNOSTICS_PER_FRAME + 3)
        ]
        accepted = sink.extend_frame([i for i in frame_items if i])
        assert accepted == MAX_DIAGNOSTICS_PER_FRAME
        assert sink.truncated is True
        assert sink.items()[-1].code == "diagnostics_truncated"

    def test_no_truncation_no_meta(self):
        sink = DiagnosticSink()
        sink.add(diagnostic("features_truncated", detail="1"))
        assert sink.truncated is False
        assert [i.code for i in sink.items()] == ["features_truncated"]

    def test_payload_shape_matches_normalize_contract(self):
        sink = DiagnosticSink()
        sink.add(diagnostic("features_truncated", detail="42", layer_id="L1"))
        payload = sink.to_payload()
        accepted, rejected = normalize_render_diagnostics(payload)
        assert rejected == []
        assert accepted[0]["code"] == "features_truncated"

    def test_global_cap_constant_unchanged(self):
        assert MAX_DIAGNOSTICS_PER_EXPORT == 64
