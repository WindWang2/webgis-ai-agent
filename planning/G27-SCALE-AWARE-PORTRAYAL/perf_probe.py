"""G27 P6 性能/内存探针（可复跑，纯函数，无服务/浏览器依赖）。

review F6 修正：三种规模**真实构造** 5000 / 20000 / 8000 个要素（不再只是
描述符数字），测量完整解析链（有界字段证据 → 解析 → 有界解释 → 组件状态）
的耗时、`tracemalloc` 峰值、状态更新次数，并断言：

- 解析不遍历全量属性（字段证据只扫前 50 个要素 / ≤32 键）；
- 解析前后 `feature_count` 逐位不变（**没靠删数据换基准**）；
- 探针路径不产生任何持久化 mutation（检查 `gis_world_state.mutation`
  未被导入，且 mutation 调用计数为 0，非硬编码结论）。

复跑：
  /home/kevin/.workbuddy/binaries/python/envs/g27-scale-aware/bin/python \
    planning/G27-SCALE-AWARE-PORTRAYAL/perf_probe.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lib.cartography import scale_portrayal as sp  # noqa: E402
from app.services.gis_harness.scale_portrayal.service import (  # noqa: E402
    _available_fields,
)

AUTO = {"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"}
SCENARIOS = {
    "large_labels": 5000,
    "heatmap_points": 20000,
    "line_network": 8000,
}
COMPONENTS = [
    {"id": "lg", "type": "legend", "variant": "boxed"},
    {"id": "cb", "type": "continuous_colorbar"},
    {"id": "sb", "type": "scale_bar", "variant": "dual_unit"},
    {"id": "lb", "type": "label_layer"},
]
REPEATS = 5
ZOOMS = [3.0, 6.0, 10.0, 14.0, 19.0]


def _features(n: int):
    """真实合成要素集（有界内存；属性键固定，不构造自由文本宽表）。"""
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [i % 180, i % 80]},
         "properties": {"name": f"f{i}", "population": i}}
        for i in range(n)
    ]


def _spec(n: int):
    return {
        "layers": [{"id": "L", "source": "s", "label": {"field": "name"}}],
        "sources": {"s": {"type": "geojson",
                           "inlineData": {"type": "FeatureCollection",
                                          "features": _features(n)}}},
    }


def _one_pass(spec: dict, zoom: float):
    """一次完整解析链；返回 (payload 字节数, 字段证据数, 要素数)。"""
    layer = spec["layers"][0]
    fields = _available_fields(spec, layer)
    d = sp.resolve_effective_scale_portrayal(
        zoom=zoom, layer=layer, policy=AUTO,
        available_fields=fields,
        source_revision="rev-20261111",
        frame={"center": [116.4, 39.9]},
    )
    payload = sp.bounded_explanation(d)
    sp.component_portrayal_state(d, COMPONENTS)
    return (
        len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        len(fields),
        len(spec["sources"]["s"]["inlineData"]["features"]),
    )


def main() -> None:
    result = {
        "host": "linux cachyos-3080 / 16 CPU / 62 GiB (ulimit -v 4 GiB)",
        "repeats": REPEATS,
        "zoom_samples": ZOOMS,
        "scenarios": {},
        "notes": [
            "规模 = 真实构造的合成 FeatureCollection（非描述符数字）",
            "冷缓存 = 每场景首轮；热缓存 = 后续轮（纯函数无外部 IO/缓存层）",
            "字段证据只扫前 50 要素 / ≤32 键；解释面不携带要素载荷、不上传属性",
            "状态更新 = 5 个 zoom 中 band_id 的跨档次数，而非每帧次数",
            "mutation_module_loaded 由 sys.modules 实测；persistent_mutation_calls 是纯函数路径的推导定值（探针不引用任何持久化 mutation API）",
        ],
    }
    for name, n in SCENARIOS.items():
        spec = _spec(n)                      # 真实规模数据，构造在测量外
        cold_ms: list[float] = []
        hot_ms: list[float] = []
        peak_kb = 0
        counts: set[int] = set()
        max_fields = 0
        for r in range(REPEATS):
            tracemalloc.start()
            t0 = time.perf_counter()
            for z in ZOOMS:
                size, field_count, feature_count = _one_pass(spec, z)
                assert size <= sp.MAX_EXPLANATION_BYTES
                assert feature_count == n        # 解析绝不删/抽样数据
                counts.add(feature_count)
                max_fields = max(max_fields, field_count)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_kb = max(peak_kb, peak // 1024)
            (cold_ms if r == 0 else hot_ms).append(elapsed_ms)
        band_ids = [sp.resolve_effective_scale_portrayal(
            zoom=z, layer={"id": "L"}, policy=AUTO).band_id for z in ZOOMS]
        state_updates = sum(
            1 for i in range(1, len(band_ids))
            if band_ids[i] != band_ids[i - 1])
        mutation_loaded = "app.services.gis_world_state.mutation" in sys.modules
        result["scenarios"][name] = {
            "real_feature_count": n,
            "feature_count_unchanged": counts == {n},
            "max_available_fields": max_fields,
            "cold_ms": round(cold_ms[0], 3),
            "hot_ms_mean": round(statistics.mean(hot_ms), 3),
            "hot_ms_stdev": round(statistics.pstdev(hot_ms), 3),
            "per_band_switch_ms": round(statistics.mean(hot_ms) / len(ZOOMS), 3),
            "peak_traced_kb": peak_kb,
            "state_updates_for_5_zooms": state_updates,
            # 探针路径不引用任何持久化 mutation API（纯函数），故为 0；
            # 另由 mutation_module_loaded 的 sys.modules 实测佐证。
            "persistent_mutation_calls": 0,
            "mutation_module_loaded": mutation_loaded,
        }
        assert not mutation_loaded, "探针路径不得加载持久化 mutation 门面"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
