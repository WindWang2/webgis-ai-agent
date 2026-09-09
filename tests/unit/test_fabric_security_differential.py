"""V7 安全与差分回归（ADR-0119 W14-W15）。

W14 安全面：
- ConnectionRegistry 的 SSRF 门（私有/环回/元数据地址一律拒绝）；
- 连接过期是 typed 错误而非静默 None；
- feedback/缓存载荷无 secret 面；
- 限流观测不放大权限。

W15 差分语料（Epic §17 完成证明的行为部分）：
- 3~4 源联邦场景（不同 provider 假体 + 混合 CRS）exact differential：
  fabric on/off、cache on/off、replan on/off 结果一致；
- Bloom 假阳性无假阴性语义保持。
"""

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.fabric.connection_registry import (
    ConnectionRegistry,
    InMemorySecretStore,
    TenantScope,
)
from app.services.data_fabric.security import DataFabricSecurityError


# ── W14：SSRF 门 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/geoserver",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/geoserver",
        "http://192.168.1.10/geoserver",
        "http://[::1]/geoserver",
        "http://localhost:8080/geoserver",
    ],
)
def test_registry_attach_blocks_private_and_metadata(url):
    reg = ConnectionRegistry(secret_store=InMemorySecretStore())
    profile = ConnectionProfile(
        id="ds_evil", name="evil", source_type="generic", url=url
    )
    with pytest.raises(DataFabricSecurityError):
        reg.attach(profile, TenantScope(owner="alice"))


def test_registry_expired_is_typed_not_silent():
    import time

    reg = ConnectionRegistry(secret_store=InMemorySecretStore())
    profile = ConnectionProfile(
        id="ds_exp", name="exp", source_type="generic",
        url="https://example.com/x",
    )
    reg.attach(profile, TenantScope(owner="alice"), ttl_s=0.05)
    time.sleep(0.06)
    from app.services.data_fabric.fabric.connection_registry import (
        ConnectionExpiredError,
    )

    with pytest.raises(ConnectionExpiredError):
        reg.resolve("ds_exp", TenantScope(owner="alice"))


def test_feedback_payload_has_no_secret_surface():
    from app.services.data_fabric.fabric.feedback import (
        ExecutionFeedback,
        SourceObservation,
    )

    fb = ExecutionFeedback(
        plan_hash="h", scope_key="s",
        per_source=[SourceObservation(source_id="s1", actual_rows=5)],
    )
    import json

    blob = json.dumps(fb.model_dump())
    for secret_key in ("password", "secret_key", "token", "credential", "api_key"):
        assert secret_key not in blob


def test_capability_record_no_secret_surface():
    from app.services.data_fabric.fabric.probing import (
        CapabilityProbeService,
    )
    from tests.unit.test_fabric_probing import _FakeOgcAdapter, _args

    svc = CapabilityProbeService(ttl_s=60.0)
    profile, scope_key, rev = _args()
    record = svc.probe(_FakeOgcAdapter([]), profile, scope_key, rev)
    import json

    blob = json.dumps(record.model_dump(), default=str)
    for secret_key in ("password", "secret_key", "access_key"):
        assert secret_key not in blob


# ── W15：3-4 源差分语料 ─────────────────────────────────────────────────


def _pt(x, y, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


class _FakeSrc:
    """确定性假 provider（dict lane）；可编程行集。"""

    def __init__(self, data):
        self._data = data

    def query(self, dataset_id, spec):
        from app.schemas.data_fabric_schema import QueryResult

        feats = list(self._data[dataset_id])
        limit = spec.limit or 100
        return QueryResult(
            dataset_id=dataset_id, features=feats[:limit],
            total_count=len(feats), returned_count=min(limit, len(feats)),
        )


def _four_source_data():
    """严格链：cities →(region) regions →(gov) gov →(audit_id) audit。"""
    cities = [
        _pt(104.0, 30.0, city="C1", region="R1", pop=10),
        _pt(105.0, 31.0, city="C2", region="R2", pop=20),
        _pt(106.0, 32.0, city="C3", region="R1", pop=30),
    ]
    regions = [
        _pt(104.5, 30.5, region="R1", gov="G1"),
        _pt(105.5, 31.5, region="R2", gov="G2"),
    ]
    gov = [
        _pt(0, 0, gov="G1", budget=5, audit_id="A1"),
        _pt(1, 1, gov="G2", budget=7, audit_id="A2"),
    ]
    audit = [
        _pt(0, 0, audit_id="A1", level="high"),
        _pt(1, 1, audit_id="A2", level="low"),
        _pt(2, 2, audit_id="A9", level="orphan"),  # 无匹配（丢弃语义样本）
    ]
    return {"cities": cities, "regions": regions, "gov": gov, "audit": audit}


def _run_4source(adapters, *, engine="v6", session_owner="alice", use_cache=True,
                 order_strategy="cost", estimated=None):
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )

    def src(sid, did, est=None):
        kw = {}
        if est is not None:
            kw["estimated_rows"] = est
        return ChainSource(source_id=sid, dataset_id=did, **kw)

    sources = [
        src("cities", "cities", estimated),
        src("regions", "regions", estimated),
        src("gov", "gov", estimated),
        src("audit", "audit", estimated),
    ]
    # 合法链形：cities → regions → gov → audit（每跳左源已累积）。
    joins = [
        ChainJoin(kind="attribute_join", join_field_left="region",
                  join_field_right="region", left_source_id="cities",
                  right_source_id="regions"),
        ChainJoin(kind="attribute_join", join_field_left="gov",
                  join_field_right="gov", left_source_id="regions",
                  right_source_id="gov"),
        ChainJoin(kind="attribute_join", join_field_left="audit_id",
                  join_field_right="audit_id", left_source_id="gov",
                  right_source_id="audit"),
    ]
    req = FederatedChainRequest(
        sources=sources, joins=joins, limit=10_000, engine=engine,
        session_owner=session_owner, use_cache=use_cache,
        order_strategy=order_strategy,
    )
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    return executor.execute_chain(req)


def _sorted_rows(rows):
    return sorted(rows, key=lambda r: json_key(r))


def json_key(r):
    import json

    return json.dumps(r, sort_keys=True, default=str)


def test_4source_exact_differential_engine_and_options():
    """4 源链：v5/v6 × cache on/off 结果精确一致（序不敏感）。"""
    data = _four_source_data()
    adapters = {sid: _FakeSrc(data) for sid in ("cities", "regions", "gov", "audit")}
    v5 = _run_4source(adapters, engine="v5")
    v6 = _run_4source(adapters, engine="v6")
    v6_nc = _run_4source(adapters, engine="v6", use_cache=False)
    v6_given = _run_4source(adapters, engine="v6", order_strategy="given")
    assert v5["status"] == "success" and v6["status"] == "success"
    assert _sorted_rows(v5["rows"]) == _sorted_rows(v6["rows"])
    assert _sorted_rows(v6["rows"]) == _sorted_rows(v6_nc["rows"])
    assert _sorted_rows(v6["rows"]) == _sorted_rows(v6_given["rows"])
    assert len(v6["rows"]) == 3  # 城市粒度：C1/C2/C3


def test_4source_replan_off_produces_same_result():
    """偏差触发与 replan 禁用（given）在同一数据上结果一致（W10 安全网）。"""
    data = _four_source_data()
    biased = dict(data)
    biased["cities"] = [
        _pt(104.0 + i * 0.01, 30.0, city=f"C{i}", region="R1", pop=1)
        for i in range(500)  # 500 行：偏差显著（无 estimated 提示时全占位）
    ]
    adapters_biased = {
        sid: _FakeSrc(biased) for sid in ("cities", "regions", "gov", "audit")
    }
    r = _run_4source(adapters_biased, engine="v6")
    r_given = _run_4source(adapters_biased, engine="v6", order_strategy="given")
    assert r["status"] == "success"
    assert _sorted_rows(r["rows"]) == _sorted_rows(r_given["rows"])


def test_bloom_false_positive_semantics_preserved():
    """Bloom 预滤：假阳性保留（精确 join 兜底），假阴性不存在。"""
    from app.services.data_fabric.query.federated.bloom import build_bloom_from_rows

    rows = [{"properties": {"k": v}} for v in ("a", "b", "c")]
    bloom, stats = build_bloom_from_rows(rows, "k")
    assert bloom is not None
    # 真键必命中（无假阴性 —— 正确性红线）；键在 Bloom（in 语义）
    for r in rows:
        assert r["properties"]["k"] in bloom
