#!/usr/bin/env python
"""ads-v1 first-round baseline measurement (DS0.5, ADR-0170).

Measures the DS8 ratchet anchors offline, honestly:

- **acquisition latency P50/P95** — in-process acquisition against the A11
  fixture layer (OGC API fake), measured through the real adapter query path;
- **inline cache hit/miss** — ``ref_payload_cache`` hit vs miss latency and
  hit rate across repeated identical requests;
- **external source availability** — real ``probe()`` against registered
  profiles is attempted but any dial is expected to fail offline; recorded as
  ``unavailable`` (never faked), per task book §0.5.

Writes docs/dev/ads-v1-baseline.md (markdown table) — provisional until DS8
re-measures on the same protocol.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT = REPO / "docs" / "dev" / "ads-v1-baseline.md"
N_SAMPLES = 60


def _percentile(xs: List[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[idx]


def _fake_session(n_features: int = 200):
    """A requests.Session backed by the A11 fixture layer (no network)."""
    from tests.fixtures.data_fabric.fake_server import make_response, session_with_fake
    from tests.data.fabric_fixtures import geojson_feature_collection, _feature

    feats = [_feature(str(i)) for i in range(n_features)]

    def handler(req):
        return make_response(req.url, json_body=geojson_feature_collection(feats, matched=len(feats)))

    return session_with_fake(("/ogc", handler))


def measure_acquisition_latency(session) -> Dict[str, Any]:
    """Real adapter query path against the fake OGC API source."""
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

    profile = ConnectionProfile(
        source_type="ogc_api",
        endpoint_url="https://ads-baseline.invalid/ogc",
        name="ads_baseline_ogc",
    )
    adapter = OGCAPIAdapter(profile)
    adapter.session = session

    spec = QuerySpec(limit=200)
    latencies: List[float] = []
    rows = 0
    for _ in range(N_SAMPLES):
        t0 = time.perf_counter()
        result = adapter.query("lake_depth", spec)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        rows = len(result.features)
    return {
        "n": N_SAMPLES,
        "rows_per_call": rows,
        "p50_ms": round(_percentile(latencies, 0.50), 2),
        "p95_ms": round(_percentile(latencies, 0.95), 2),
    }


def measure_inline_cache() -> Dict[str, Any]:
    """ref_payload_cache hit vs miss latency and hit rate."""
    from app.services.ref_payload_cache import ref_payload_cache

    cache = ref_payload_cache
    payload = {"type": "FeatureCollection", "features": [{"i": i} for i in range(200)]}
    approx_bytes = 40_000
    miss_lat: List[float] = []
    hit_lat: List[float] = []
    keys = []
    for i in range(N_SAMPLES // 2):
        key = ("ads-baseline-session", f"ref-{i}")
        keys.append(key)
        t0 = time.perf_counter()
        cache.put(key[0], key[1], payload, approx_bytes)
        miss_lat.append((time.perf_counter() - t0) * 1000.0)
    hits = 0
    for sid, rid in keys:
        t0 = time.perf_counter()
        got = cache.get(sid, rid)
        hit_lat.append((time.perf_counter() - t0) * 1000.0)
        if got is not None:
            hits += 1
    # 清场：不把基线键留给同进程后续测试
    for sid, rid in keys:
        cache.invalidate(sid, rid)
    return {
        "n": len(keys),
        "hit_rate": round(hits / max(1, len(keys)), 4),
        "put_p50_ms": round(_percentile(miss_lat, 0.50), 3),
        "get_hit_p50_ms": round(_percentile(hit_lat, 0.50), 3),
        "get_hit_p95_ms": round(_percentile(hit_lat, 0.95), 3),
    }


def measure_external_availability() -> Dict[str, Any]:
    """Probe a couple of real public endpoints — expected offline-unavailable.

    Honesty rule: no faking. In the sandbox these record ``unavailable``; on a
    networked machine the same script records the real availability.
    """
    import socket as _socket

    results = []
    for name, host, port in [
        ("overpass-api.de", "overpass-api.de", 443),
        ("services.arcgis.com", "services.arcgis.com", 443),
        ("planetarycomputer.microsoft.com", "planetarycomputer.microsoft.com", 443),
    ]:
        t0 = time.perf_counter()
        try:
            s = _socket.create_connection((host, port), timeout=3)
            s.close()
            ok = True
        except Exception:
            ok = False
        results.append(
            {
                "endpoint": name,
                "available": ok,
                "probe_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            }
        )
    return {"endpoints": results, "note": "sandbox default = unavailable (not faked)"}


def main() -> int:
    session = _fake_session(200)
    acq = measure_acquisition_latency(session)
    cache = measure_inline_cache()
    ext = measure_external_availability()

    lines = [
        "# ads-v1 首轮基线（DS0.5 / ADR-0170 · provisional）",
        "",
        f"> 测量脚本：`scripts/ads_baseline.py`（离线可复跑，{N_SAMPLES} 样本/项）。",
        "> DS8 用同一协议复测并转定稿；本轮数值仅作 ratchet 锚点。",
        "",
        "## 取数时延（adapter.query，OGC API fixture，200 要素/次）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 样本数 | {acq['n']} |",
        f"| 每次 rows | {acq['rows_per_call']} |",
        f"| P50 | {acq['p50_ms']} ms |",
        f"| P95 | {acq['p95_ms']} ms |",
        "",
        "## 内联缓存（ref_payload_cache）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 命中率 | {cache['hit_rate']} |",
        f"| put P50 | {cache['put_p50_ms']} ms |",
        f"| get(hit) P50 | {cache['get_hit_p50_ms']} ms |",
        f"| get(hit) P95 | {cache['get_hit_p95_ms']} ms |",
        "",
        "## 外部源可用率（真实 probe，不伪造）",
        "",
        "| 端点 | 可用 | probe 耗时 |",
        "|---|---|---|",
    ]
    for e in ext["endpoints"]:
        lines.append(f"| {e['endpoint']} | {'yes' if e['available'] else '**unavailable**'} | {e['probe_ms']} ms |")
    lines += ["", f"> {ext['note']}", ""]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(json.dumps({"acquisition": acq, "inline_cache": cache}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
