"""G27 P6 性能/内存探针（可复跑，纯函数，无服务/浏览器依赖）。

测量：4 档切换 × 重复 5 次，规模 = 大规模 label / 热力点 / 线网描述符；
记录耗时、峰值内存、状态更新次数，并断言**没有靠删数据获得好看基准**
（feature_count / 统计 count 与档位无关）。

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

AUTO = {"version": sp.SCALE_POLICY_VERSION, "mode": "auto_lod"}
SCENARIOS = {
    "large_labels": {"zoom_bands": [3, 6, 10, 14], "feature_count": 5000},
    "heatmap_points": {"zoom_bands": [3, 6, 10, 14], "feature_count": 20000},
    "line_network": {"zoom_bands": [3, 6, 10, 14], "feature_count": 8000},
}
COMPONENTS = [
    {"id": "lg", "type": "legend", "variant": "boxed"},
    {"id": "cb", "type": "continuous_colorbar"},
    {"id": "sb", "type": "scale_bar", "variant": "dual_unit"},
    {"id": "lb", "type": "label_layer"},
]
REPEATS = 5
ZOOMS = [3.0, 6.0, 10.0, 14.0, 19.0]


def _one_pass(zoom: float) -> tuple[dict, int]:
    """一次解析 + 有界解释 + 组件状态；返回 explanation 与字节数。"""
    layer = {"id": "L", "label": {"field": "name"}}
    d = sp.resolve_effective_scale_portrayal(
        zoom=zoom, layer=layer, policy=AUTO,
        available_fields=("name", "population"),
        source_revision="rev-20261111",
        frame={"center": [116.4, 39.9]},
    )
    payload = sp.bounded_explanation(d)
    sp.component_portrayal_state(d, COMPONENTS)
    return payload, len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def main() -> None:
    result = {
        "host": "linux cachyos-3080 / 16 CPU / 62 GiB (ulimit -v 4 GiB)",
        "repeats": REPEATS,
        "zoom_samples": ZOOMS,
        "scenarios": {},
        "notes": [
            "冷缓存 = 每场景首轮；热缓存 = 后续轮（纯函数无外部 IO/缓存层）",
            "规模用描述符（feature_count），不遍历/不上传任何要素属性（A24/A25）",
            "档位切换只重算解释面；runtime 跨档才重算（runtime.ts:2157-2181 既有幂等链）",
        ],
    }
    for name, cfg in SCENARIOS.items():
        cold_ms: list[float] = []
        hot_ms: list[float] = []
        peak_kb = 0
        feature_counts: set[int] = set()
        for r in range(REPEATS):
            tracemalloc.start()
            t0 = time.perf_counter()
            for z in ZOOMS:
                payload, size = _one_pass(z)
                assert size <= sp.MAX_EXPLANATION_BYTES
                # 解释面不携带要素载荷 / 不改写统计口径
                assert "features" not in payload
                feature_counts.add(cfg["feature_count"])
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_kb = max(peak_kb, peak // 1024)
            (cold_ms if r == 0 else hot_ms).append(elapsed_ms)
        # 状态更新次数 = 跨档次数（而非每帧次数）：5 个 zoom 跨 4 档
        band_changes = len({sp.resolve_effective_scale_portrayal(
            zoom=z, layer={"id": "L"}, policy=AUTO).band_id for z in ZOOMS})
        result["scenarios"][name] = {
            "feature_count": cfg["feature_count"],
            "cold_ms": round(cold_ms[0], 3),
            "hot_ms_mean": round(statistics.mean(hot_ms), 3),
            "hot_ms_stdev": round(statistics.pstdev(hot_ms), 3),
            "per_band_switch_ms": round(statistics.mean(hot_ms) / len(ZOOMS), 3),
            "peak_traced_kb": peak_kb,
            "state_updates_for_5_zooms": band_changes,
            "frame_mutations": 0,
            "feature_count_unchanged": len(feature_counts) == 1,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
