"""性能冒烟（§三十三）—— 纯函数热点在有界合成数据上的确定性验收。

约定：与 tests/perf 基线闸解耦（不占 baselines.json），在主 lane 运行，
只用宽上限断言（结构正确性优先，墙钟仅作粗护栏）；数据全部合成、
有界，不碰网络/docker。
"""
import time

from app.lib.data.large_data import (
    DataSizeClass,
    access_policy,
    classify_features,
    classify_size,
    llm_view_allowed,
)
from app.lib.data.profile import profile_features, profile_rows
from app.lib.data.versioning import build_version_chain
from app.services.data_catalog.catalog import CatalogEntry, CatalogFilter
from app.services.data_catalog.lineage_query import SessionLineageQuery


def _features(n: int):
    return [
        {"geometry": {"type": "Point", "coordinates": [100.0 + (i % 500) * 0.01,
                                                       20.0 + (i % 700) * 0.01]},
         "properties": {"v": i % 1000, "c": f"cat{i % 20}", "s": f"name{i % 5000}"}}
        for i in range(n)
    ]


class TestProfileScale:
    def test_100k_features_bounded_and_fast(self):
        feats = _features(100_000)
        t0 = time.perf_counter()
        vp, quality = profile_features(feats, max_scan_rows=50_000)
        elapsed = time.perf_counter() - t0
        assert vp.row_count == 100_000
        assert vp.scanned_rows == 50_000          # 采样闸生效
        assert quality.value == "sampled"
        assert elapsed < 10.0                     # 宽护栏（本机 Debug 口径）

    def test_10k_rows_table_profile(self):
        rows = [{"id": i, "city": f"c{i % 50}", "v": i * 1.5} for i in range(10_000)]
        t0 = time.perf_counter()
        tp, _ = profile_rows(rows)
        assert tp.row_count == 10_000
        assert time.perf_counter() - t0 < 5.0


class TestLineageScale:
    def test_wide_graph_traversal_bounded(self):
        # 1000 记录的链式图（100 段 × 10 宽度）→ 视图遍历有界
        records = {}
        for i in range(1000):
            parents = [f"r{i - 10}"] if i >= 10 else []
            records[f"r{i}"] = {
                "artifact_id": f"r{i}", "inputs": parents, "status": "valid",
                "artifact_type": "feature_collection", "revision": 0,
                "created_at": float(i), "metadata": {},
            }

        class _Rec(dict):
            __getattr__ = dict.get

        recs = {k: _Rec(v) for k, v in records.items()}
        q = SessionLineageQuery(recs)
        t0 = time.perf_counter()
        view = q.view("r999", max_depth=8)
        elapsed = time.perf_counter() - t0
        assert len(view.nodes) <= 256                    # 视图节点封顶
        assert elapsed < 5.0

    def test_version_chain_1000_long(self):
        records = [
            {"artifact_id": f"v{i}", "replaces": (f"v{i-1}" if i else None),
             "revision": i, "created_at": float(i), "metadata": {}}
            for i in range(1000)
        ]

        class _Rec(dict):
            __getattr__ = dict.get

        t0 = time.perf_counter()
        chain = build_version_chain([_Rec(r) for r in records], head_artifact_id="v999")
        assert chain.truncated                    # 32 上限截断声明
        assert len(chain.chain) == 32
        assert time.perf_counter() - t0 < 2.0


class TestCatalogScale:
    def test_filter_over_10k_entries(self):
        entries = [
            CatalogEntry(
                entry_id=f"e{i}", scope="session",
                name=f"dataset {i}", category="vector" if i % 2 else "raster",
                logical_role="observation", lifecycle="ready",
                extent=[100.0 + (i % 100) * 0.1, 20.0, 101.0, 30.0],
                feature_count=i,
            )
            for i in range(10_000)
        ]
        flt = CatalogFilter(category="vector", role="observation", keyword="dataset 9")
        t0 = time.perf_counter()
        matched = [e for e in entries if flt.matches(e)]
        elapsed = time.perf_counter() - t0
        assert 0 < len(matched) < 10_000
        assert elapsed < 2.0


class TestLargeDataPolicy:
    def test_classify_boundaries(self):
        assert classify_size(512 * 1024) is DataSizeClass.SMALL
        assert classify_size(10 * 1024 * 1024) is DataSizeClass.MEDIUM
        assert classify_size(2 * 1024 ** 3) is DataSizeClass.LARGE
        assert classify_size(None) is DataSizeClass.UNKNOWN

    def test_feature_heuristic_matches_descriptor_estimate(self):
        # 150k 要素 ≈ 15 MB（100B/要素）→ medium（与会话 store 预算一致）
        assert classify_features(150_000) is DataSizeClass.MEDIUM
        assert classify_features(1_000_000) is DataSizeClass.LARGE

    def test_policy_matrix(self):
        assert access_policy(DataSizeClass.SMALL) == "direct_read"
        assert access_policy(DataSizeClass.MEDIUM) == "profile_chunked"
        assert access_policy(DataSizeClass.LARGE) == "ref_metadata"
        assert access_policy(DataSizeClass.UNKNOWN) == "profile_chunked"  # 未知按保守
        assert llm_view_allowed(DataSizeClass.SMALL)
        assert not llm_view_allowed(DataSizeClass.LARGE)
        assert not llm_view_allowed(DataSizeClass.UNKNOWN)
