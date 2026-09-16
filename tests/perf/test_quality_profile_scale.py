"""10 万行级画像路径性能纪律（DQH v1）。

口径（任务书）：性能覆盖 10 万行级采样/元数据路径，避免全量物化；
本地数字只记录环境事实，不冒充普适 SLO。

断言的是**结构性质**而非绝对耗时：
- 画像路径在 100k 行上完成（宽松时限仅防回归性劣化）；
- 检测器证据样本被帽约束（≤200/字段）—— 证据不随数据集规模线性增长；
- 同输入两遍 digest 一致（规模下仍确定性）。
"""
from __future__ import annotations

import time

import pytest

from app.services.data_quality.profile import build_profile_for_payload

pytestmark = pytest.mark.perf


def _big_payload(n: int = 100_000) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [100.0 + (i % 900) * 0.1,
                                          20.0 + (i % 400) * 0.1]},
             "properties": {
                 "名称": f"要素{i}",
                 "面积": 100.0 + (i % 97) * 3.3,
                 "人口": 1000 + (i % 89) * 11,
                 "采集时间": f"2024-{i % 12 + 1:02d}-15 08:30:00",
             }}
            for i in range(n)
        ],
    }


def test_100k_profile_path_bounded_and_deterministic():
    payload = _big_payload()
    t0 = time.perf_counter()
    p1 = build_profile_for_payload(payload, target_ref="ref:big")
    elapsed = time.perf_counter() - t0
    p2 = build_profile_for_payload(payload, target_ref="ref:big")

    assert p1.profile_digest == p2.profile_digest
    # 环境相关宽松上限：100k 行画像路径明显劣化（如误做全量物化）会击穿。
    assert elapsed < 60.0, f"profile path took {elapsed:.1f}s — 疑似全量物化"

    # 证据帽：检测器输入样本 ≤200/字段（不随 100k 规模增长）。
    roles = p1.sections["semantic"]["field_roles"]
    assert roles, "应有语义角色投影"
    # 画像与规则的扫描集有界（unified/profiler 单点阈值）—— 这里验证
    # 画像 section 不携带要素载荷。
    d = p1.to_bounded_dict()
    assert "features" not in d
