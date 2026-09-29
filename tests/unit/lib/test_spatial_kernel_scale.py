"""10 万级要素合成基准 smoke（H06 Wave F，heavy marker）。

稳定阈值（工程常数，非机器速度断言）：

- 100k 点 Moran's I（kNN k=8，99 次置换）在 inline 路径**成功完成**；
- tracemalloc 峰值 < 1.5GiB（受限内存不拖死进程的宽松上界；实际量级
  ~百 MiB —— 稀疏 kNN 权重 + O(n) 数组）。

数据是确定性合成（default_rng(seed) 双簇结构），不下载任何外部数据集。
"""
import time
import tracemalloc

import numpy as np
import pytest

pytestmark = pytest.mark.heavy


def _synthetic_clustered_fc(n_points=100_000, seed=42):
    """确定性双簇 + 噪声点场（值与空间相关 → Moran 显著为正）。"""
    rng = np.random.default_rng(seed)
    n_cluster = int(n_points * 0.8)
    centers = [(116.39, 39.90, 100.0), (116.42, 39.92, 100.0),
               (116.39, 39.95, 1.0), (116.42, 39.95, 1.0)]
    feats = []
    per = n_cluster // len(centers)
    for cx, cy, val in centers:
        xs = cx + rng.normal(0, 3e-4, per)
        ys = cy + rng.normal(0, 3e-4, per)
        for x, y in zip(xs.tolist(), ys.tolist()):
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [x, y]},
                          "properties": {"val": val}})
    # 噪声：均匀散布 + 中间值（仍保持空间自相关的可检出性）
    n_noise = n_points - len(feats)
    xs = rng.uniform(116.30, 116.50, n_noise)
    ys = rng.uniform(39.85, 40.00, n_noise)
    vs = rng.uniform(40.0, 60.0, n_noise)
    for x, y, v in zip(xs.tolist(), ys.tolist(), vs.tolist()):
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [x, y]},
                      "properties": {"val": v}})
    return {"type": "FeatureCollection", "features": feats}


def test_100k_feature_moran_inline_scale_smoke():
    from app.lib.geo_analysis.statistics import moran_i_narrated

    fc = _synthetic_clustered_fc(100_000)
    assert len(fc["features"]) == 100_000

    tracemalloc.start()
    res = moran_i_narrated(fc, "val", permutations=99)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert res.success, res.summary
    d = res.data
    # 空间结构上结果必须是显著聚类（合成数据性质，非实现断言）
    assert d["pattern"] == "clustering"
    assert d["moran_i"] > 0.5
    assert d["n_features"] == 100_000

    # review P2-3：只断言 tracemalloc 峰值（本测试自身足迹）。ru_maxrss 是
    # 进程生命周期最大值（同进程先前测试可污染）；wall 时钟在共享 runner
    # 上会 flake —— 两者都不作断言（实测值记录在 PR body）。
    peak_gib = peak / 1024**3
    assert peak_gib < 1.5, f"tracemalloc peak {peak_gib:.2f} GiB"


def test_inline_ceiling_rejects_500k_before_compute():
    """超限在解析/投影之前 typed 拒绝 —— 拒绝本身 O(n) 计数，零投影成本。"""
    from app.lib.gis.scientific_errors import ResourceScaleMismatch
    from app.lib.geo_analysis.context import validate_spatial_input

    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature"}] * (500_000)}
    t0 = time.monotonic()
    with pytest.raises(ResourceScaleMismatch):
        validate_spatial_input(fc)
    assert time.monotonic() - t0 < 2.0  # 拒绝是即时的（不解析几何）
