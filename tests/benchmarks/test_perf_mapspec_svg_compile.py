"""SVG 编译器结构性性能预算（perf，count-based + 结构性 wall-clock 上限）。

W11：锁定 W4 引入的阈值执行在真实量级下的行为与代价 —— 合成 20k 点
FeatureCollection（内存生成，不落大文件），differential 断言：

(a) max_features=2000：``<circle`` 元素数 ≤ 2000 且诊断含 features_truncated；
(b) 无 cap：feature_count == 20000（声明字段不再零消费）；
(c) 20k 编译 wall-clock < 15s —— CI 安全的**结构性上限**（预算放宽到能吸收
    CI 抖动；不是精细 benchmark，防回归"意外变成 O(n²)"这类结构性劣化）；
(d) timeout_ms=50：协作式中止触发 export_timeout_partial，不跑完全量。

Run:
    .venv/bin/pytest -m perf --no-cov tests/benchmarks/test_perf_mapspec_svg_compile.py -q
Unfiltered runs self-skip per tests/conftest.py #664（perf 基线要求隔离运行）。
"""
import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

_N_POINTS = 20000
_FC_CACHE: dict = {}


def _points_fc(n: int) -> dict:
    """确定性合成 FeatureCollection（内存生成，坐标网格铺开便于投影）。"""
    cached = _FC_CACHE.get(n)
    if cached is not None:
        return cached
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        116.0 + (i % 200) * 0.001,
                        39.0 + (i // 200) * 0.001,
                    ],
                },
                "properties": {"id": i},
            }
            for i in range(n)
        ],
    }
    _FC_CACHE[n] = fc
    return fc


def _points_spec(n: int) -> dict:
    return {
        "version": "1.0",
        "sources": {"pts": {"type": "geojson", "inlineData": _points_fc(n)}},
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "pts",
                "paint": {"circle-color": "#3b82f6", "circle-radius": 4},
            }
        ],
    }


@pytest.mark.perf
def test_perf_svg_compile_cap_2000_of_20k_truncates_with_diagnostic():
    """(a) cap=2000：元素数 ≤ 2000、诊断含 features_truncated、计数恰为 cap。"""
    comp = compile_mapspec_to_svg_detailed(
        _points_spec(_N_POINTS), target_dpi=72, max_features=2000)
    assert comp.svg.count("<circle") <= 2000
    assert comp.truncated_features is True
    assert comp.feature_count == 2000  # 全部命中 circle 分支 → 恰等于 cap
    assert any(d["code"] == "features_truncated" for d in comp.diagnostics)
    assert not comp.timed_out


@pytest.mark.perf
def test_perf_svg_compile_uncapped_full_feature_count():
    """(b) 无 cap（默认 50000）：全量 20k 要素入图，无降级诊断。"""
    comp = compile_mapspec_to_svg_detailed(
        _points_spec(_N_POINTS), target_dpi=72)
    assert comp.feature_count == _N_POINTS
    assert comp.truncated_features is False
    assert comp.timed_out is False
    assert comp.diagnostics == []


@pytest.mark.perf
def test_perf_svg_compile_20k_within_structural_budget():
    """(c) 20k 编译 wall-clock < 15s —— CI 安全的结构性上限，非精细基准。"""
    import time

    start = time.perf_counter()
    comp = compile_mapspec_to_svg_detailed(
        _points_spec(_N_POINTS), target_dpi=72)
    elapsed = time.perf_counter() - start
    assert comp.feature_count == _N_POINTS
    assert elapsed < 15.0, f"20k compile took {elapsed:.2f}s — structural budget is 15s"
    print(f"\n[perf-svg-compile] 20k points compiled in {elapsed:.3f}s")


@pytest.mark.perf
def test_perf_svg_compile_cooperative_timeout_50ms():
    """(d) timeout_ms=50：协作式中止提前退出，产物诚实标注部分导出。"""
    import time

    start = time.perf_counter()
    comp = compile_mapspec_to_svg_detailed(
        _points_spec(_N_POINTS), target_dpi=72, timeout_ms=50)
    elapsed = time.perf_counter() - start
    assert comp.timed_out is True
    assert comp.feature_count < _N_POINTS
    assert any(d["code"] == "export_timeout_partial" for d in comp.diagnostics)
    # 协作中止必须真的提前返回（而非跑完 20k 才补记诊断）
    assert elapsed < 15.0
    print(f"\n[perf-svg-compile-timeout] stopped after {comp.feature_count} "
          f"features in {elapsed:.3f}s")
