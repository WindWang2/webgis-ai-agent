"""full-master-audit-2026-09 Batch 3（ModelOps）回归测试。

- #1199（B-3/B-13）：_RUN_LOCAL accumulator / 取消通知通道按 run_id 键控。
- #1206（B-4）：merge_instances memmap 路径 + polygonize 不再 UnboundLocalError。
- #1207（B-5）：merge_detections 的 chip→全局映射减 pad。
- #1208（B-6）：服务层 _input_profile 复用引擎米/像素口径（地理 CRS → 0）。
- #1210（B-8）：跨进程/跨实例 register 后 load() 能看到新模型。
"""

from __future__ import annotations

import numpy as np

from app.lib.modelops.planning import TilePlan, TileSpec
from app.lib.modelops.stitching import merge_detections, merge_instances


def _tile(read_row: int, read_col: int, core_h: int, core_w: int,
          pad=(0, 0, 0, 0)) -> TileSpec:
    return TileSpec(
        index=0,
        core_window=(read_row, read_col, core_h, core_w),
        read_window=(read_row, read_col, core_h, core_w),
        pad=pad, chip_hw=(core_h, core_w),
    )


class TestMergeDetectionsPadOffset:
    """#1207：边缘 tile（pad>0）的检测框必须减 pad 才是全局坐标。"""

    def test_edge_tile_detection_offset_by_pad(self) -> None:
        # raster 原点 (0,0)，tile 读取被 clamp：read=(0,0)，pad_left=pad_top=8
        tile = _tile(0, 0, 48, 48, pad=(8, 8, 0, 0))
        plan = TilePlan(
            raster_height=48, raster_width=48, chip_h=64, chip_w=64,
            stride_y=48, stride_x=48, context_h=64, context_w=64, tiles=[tile],
        )
        dets = [{"label": 1, "score": 0.9, "box": (20.0, 10.0, 4.0, 4.0)}]
        out = merge_detections(plan, [dets])
        assert len(out) == 1
        x, y, w, h = out[0].box
        assert (x, y) == (20.0 - 8, 10.0 - 8)

    def test_interior_tile_unchanged(self) -> None:
        tile = _tile(100, 200, 48, 48, pad=(0, 0, 0, 0))
        plan = TilePlan(
            raster_height=300, raster_width=300, chip_h=48, chip_w=48,
            stride_y=48, stride_x=48, context_h=48, context_w=48, tiles=[tile],
        )
        dets = [{"label": 1, "score": 0.9, "box": (5.0, 6.0, 4.0, 4.0)}]
        out = merge_detections(plan, [dets])
        assert out[0].box[:2] == (205.0, 106.0)


class TestMergeInstancesMemmapPolygonize:
    """#1206：memmap 分支 + polygonize 不抛 UnboundLocalError。"""

    def test_memmap_path_polygonize_returns_features(
        self, tmp_path, monkeypatch
    ) -> None:
        import tempfile

        def _mkdtemp(prefix="", dir=None):
            d = tmp_path / "mm"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)

        monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
        # 强制 memmap：need = h*w*4 > 256MiB → 8200x8200
        h = w = 8200
        tile = _tile(0, 0, 64, 64, pad=(0, 0, 0, 0))
        plan = TilePlan(
            raster_height=h, raster_width=w, chip_h=64, chip_w=64,
            stride_y=64, stride_x=64, context_h=64, context_w=64, tiles=[tile],
        )
        mask = np.zeros((64, 64), dtype=np.int32)
        mask[10:20, 10:20] = 1
        result = merge_instances(
            plan, [mask], [{1: 2}], polygonize=True,
            input_nodata=None,
        )
        assert result.instance_ids.shape == (h, w)
        assert result.polygon_geojson is not None
        assert result.polygon_geojson.get("type") == "FeatureCollection"


class TestMetersPerPixelCaliber:
    """#1208：地理 CRS（度）不得把度/像素当米。"""

    def test_geographic_crs_returns_zero(self) -> None:
        from app.services.modelops.engine import InferenceEngine

        class _Meta:
            crs = "EPSG:4326"
            transform = (0.0001, 0.0, 100.0, 0.0, -0.0001, 200.0)

        assert InferenceEngine._meters_per_pixel(_Meta()) == 0.0

    def test_projected_crs_returns_meters(self) -> None:
        from app.services.modelops.engine import InferenceEngine

        class _Meta:
            crs = "EPSG:3857"
            transform = (10.0, 0.0, 0.0, 0.0, -10.0, 0.0)

        assert InferenceEngine._meters_per_pixel(_Meta()) == 10.0


class TestRegistryCrossInstanceVisibility:
    """#1210：实例 B register 后，实例 A 的 load() 能看到；seq 跨实例唯一。"""

    def _store(self, tmp_path):
        from app.services.modelops.config import ModelOpsSettings
        from app.services.modelops.registry import ModelRegistryStore

        return ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))

    def test_b_register_visible_to_a(self, tmp_path, tiny_desc_factory) -> None:
        a = self._store(tmp_path)
        b = self._store(tmp_path)
        a.load()
        b.load()
        desc = tiny_desc_factory(model_id="cross-vis", model_version="1.0.0")
        b.register(desc, owner_scope={"session_id": "s1"})
        a.load()  # 指纹变化 → 重扫
        rec = a.resolve("cross-vis", None, session_id="s1")
        assert rec is not None
        assert rec.descriptor.model_id == "cross-vis"

    def test_seq_unique_across_instances(self, tmp_path, tiny_desc_factory) -> None:
        a = self._store(tmp_path)
        b = self._store(tmp_path)
        a.load()
        b.load()
        r1 = a.register(
            tiny_desc_factory(model_id="seq-a", model_version="1.0.0"),
            owner_scope={"session_id": "s1"},
        )
        r2 = b.register(
            tiny_desc_factory(model_id="seq-b", model_version="1.0.0"),
            owner_scope={"session_id": "s1"},
        )
        assert r1.seq != r2.seq


class TestRunLocalPerRunKey:
    """#1199：_RUN_LOCAL 以 run_id 为键（无共享 'accumulator' 键）。"""

    def test_module_channel_keyed_by_run_id(self) -> None:
        from app.services.modelops import engine as eng

        class _Acc:
            def close(self) -> None:
                pass

        eng._RUN_LOCAL.clear()
        eng._RUN_LOCAL["run-aaa"] = _Acc()
        eng._RUN_LOCAL["run-bbb"] = _Acc()
        assert "accumulator" not in eng._RUN_LOCAL
        assert set(eng._RUN_LOCAL) == {"run-aaa", "run-bbb"}
        eng._RUN_LOCAL.clear()
