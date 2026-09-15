"""主动空间记忆引擎测试（ADR-0190，agent-06）。

锁定红线（全部有断言）：
- 租户隔离 100%：org A 的私有空间记忆在 awake / resolve_place / 卡片渲染 /
  索引分区 / consolidator 五条通道上对 org B 绝对不可达（fail-closed，
  无 org 不检索）；
- 模糊代词消歧：「上次看的那个地块」「本市开发区」高置信唯一召回
  resolved_place；并列/低置信返回 None（消歧绝不静默赌博）；
- 生命周期：hit_count≥2 + 强证据晋升；显式偏好固化（永不过期）；
  过期零命中记忆失效不召回；失效避坑类不晋升（短命证据不固化）；
- 检索评分：BM25 语义 + bbox 地理邻近 + Half-Life 时间衰减，确定性排序，
  每条命中带可读理由；热路径亚毫秒级（宽松 CI 上限 50ms）；
- 挂载：turn_context 主动切片 fail-open；intent 消歧回填 scope + 审计 +
  0.9 折扣；resolve_map_request_intent 纯函数纪律不破坏。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import Base
from app.services.gis_memory import associative_index as ai
from app.services.gis_memory import memory_consolidator as mc
from app.services.gis_memory import proactive_retriever as pr
from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_PROVIDER_FAILURE,
    KIND_RESOLVED_PLACE,
    KIND_SUCCESSFUL_STRATEGY,
    KIND_USER_CARTO_PREF,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_REVIEW_PASSED,
    SOURCE_TOOL_FAILURE,
    SOURCE_USER_CORRECTION,
    SOURCE_USER_DECISION,
    MemoryEvidence,
    MemoryWriteRequest,
    SpatialMemoryRecord,
)
from app.services.gis_harness.intent import (
    MapRequestIntent,
    ScopeIntent,
    apply_proactive_memory_hints,
    resolve_intent_adaptive,
    resolve_map_request_intent,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture()
def retriever(factory):
    """干净索引 + 绑定测试 db 工厂的独立检索器（与进程单例隔离）。"""
    return pr.ProactiveRetriever(db_factory=factory)


def _place(
    subject: str,
    *,
    org_id: str = "org-a",
    scope: str = SCOPE_SESSION,
    scope_id: str = "sess-1",
    bbox=None,
    level: str = "district",
    confidence: float = 0.85,
    aliases=None,
    sensitive: bool = False,
    evidence_source: str = SOURCE_INTENT_RESOLUTION,
) -> MemoryWriteRequest:
    value: dict = {"name": subject, "level": level}
    if bbox is not None:
        value["bbox"] = list(bbox)
    if aliases:
        value["aliases"] = list(aliases)
    return MemoryWriteRequest(
        kind=KIND_RESOLVED_PLACE,
        scope=scope,
        scope_id=scope_id,
        subject=subject,
        value=value,
        evidence=MemoryEvidence(source=evidence_source),
        confidence=confidence,
        org_id=org_id,
        # 写入门铁律：user 作用域必须显式 user_id（匿名不写用户记忆）
        user_id=scope_id if scope == SCOPE_USER else None,
        sensitive=sensitive,
    )


def _seed(db, *reqs: MemoryWriteRequest) -> list:
    rows = []
    for req in reqs:
        row = s.record_memory(db, req)
        assert row is not None, f"seed 被写入门拒绝: {req.kind}/{req.subject}"
        rows.append(row)
    db.commit()
    return rows


def _mk_record(subject, *, memory_id=None, bbox=None, org_id="org-a",
               confidence=0.8, pinned=False, age_days=0.0,
               scope=SCOPE_SESSION, kind=KIND_RESOLVED_PLACE, aliases=None,
               validated_at=None):
    scope_id = (
        "sess-1" if scope == SCOPE_SESSION
        else ("proj-1" if scope == SCOPE_PROJECT else "u-1")
    )
    value: dict = {"name": subject}
    if bbox is not None:
        value["bbox"] = list(bbox)
    if aliases:
        value["aliases"] = list(aliases)
    return SpatialMemoryRecord(
        id=memory_id or f"m-{subject}",
        kind=kind,
        scope=scope,
        scope_id=scope_id,
        org_id=org_id,
        subject=subject,
        value=value,
        confidence=confidence,
        last_validated_at=(
            validated_at
            or (_now() - timedelta(days=age_days)).isoformat()
        ),
        evidence={
            "source": (
                "explicit_user_decision" if pinned else "intent_resolution"
            ),
        },
    )


def _mk_entry(subject, **kwargs):
    return pr.entry_from_record(_mk_record(subject, **kwargs), now_ts=time.time())


# ── A. 关联索引：词元 / BM25 / 空间 / 衰减 / 确定性 / 延迟 ────────────────


def test_tokenize_cjk_bigram_ascii_and_symmetry():
    toks = ai.tokenize("天府软件园 GIS")
    assert "天府" in toks and "软件" in toks and "件园" in toks
    assert "gis" in toks
    # 数字串任意长度保留（地块编号 1 vs 488 的判别信号就在数字上）
    assert "5" in ai.tokenize("地块编号5")
    assert "488" in ai.tokenize("地块编号488")
    # 索引侧与查询侧同一词元器（不对称即 bug）
    assert ai.tokenize("高新区") == ai.tokenize("高新区 ")


def test_bm25_orders_relevant_subject_first():
    idx = ai.AssociativeIndex()
    idx.upsert(_mk_entry("成都市高新区"))
    idx.upsert(_mk_entry("成都市"))
    idx.upsert(_mk_entry("北京朝阳区"))
    hits = idx.search("org-a", "高新区", now=time.time())
    assert hits, "应命中"
    assert hits[0].entry.subject == "成都市高新区"
    assert hits[0].reasons, "每条命中必须带可读理由"


def test_bbox_proximity_orders_overlapping_first():
    idx = ai.AssociativeIndex()
    idx.upsert(_mk_entry("地块A", bbox=(104.0, 30.5, 104.1, 30.6)))
    idx.upsert(_mk_entry("地块B", bbox=(120.0, 30.5, 120.1, 30.6)))
    hits = idx.search(
        "org-a", "地块", bbox=(103.95, 30.45, 104.12, 30.65), now=time.time()
    )
    assert hits[0].entry.subject == "地块A"
    assert hits[0].score > hits[1].score
    assert any("geo" in r for r in hits[0].reasons), "邻近度分量必须可解释"


def test_half_life_decay_monotonic_and_pinned_immortal():
    fresh = ai.half_life_decay(age_days=0.0)
    old = ai.half_life_decay(age_days=56.0)  # 恰 4 个半衰期
    assert fresh > old
    assert abs(old - 0.0625) < 1e-6
    # 显式偏好永不忘：pinned 条目半衰期无穷大
    idx = ai.AssociativeIndex()
    idx.upsert(_mk_entry("地方高斯投影", pinned=True, age_days=560.0,
                         kind=KIND_USER_CARTO_PREF))
    idx.upsert(_mk_entry("临时投影", pinned=False, age_days=56.0,
                         kind="crs_resolution"))
    hits = idx.search("org-a", "投影", now=time.time())
    assert hits[0].entry.subject == "地方高斯投影"


def test_search_deterministic_and_tie_breaks_lexicographic():
    idx = ai.AssociativeIndex()
    # 严格同分构造：同一 validated_at / 置信 / 作用域，唯一差异是 subject
    same_ts = (_now() - timedelta(hours=1)).isoformat()
    idx.upsert(_mk_entry("开发区乙", memory_id="m-b",
                         validated_at=same_ts))
    idx.upsert(_mk_entry("开发区甲", memory_id="m-a",
                         validated_at=same_ts))
    first = idx.search("org-a", "开发区", now=time.time())
    second = idx.search("org-a", "开发区", now=time.time())
    ids = [h.entry.memory_id for h in first]
    assert ids == [h.entry.memory_id for h in second], "同输入必须同序"
    # 严格同分时 subject 字典序稳定（乙 U+4E59 < 甲 U+7532）
    assert first[0].entry.subject == "开发区乙"


def test_hot_query_latency_loose_ci_bound():
    idx = ai.AssociativeIndex()
    for i in range(1000):
        idx.upsert(_mk_entry(f"地块编号{i}", memory_id=f"m-{i}",
                             bbox=(104 + i * 0.001, 30.0,
                                   104.01 + i * 0.001, 30.01)))
    idx.search("org-a", "地块编号1", now=time.time())  # 预热
    t0 = time.perf_counter()
    for _ in range(20):
        hits = idx.search("org-a", "地块编号1", now=time.time())
    elapsed = (time.perf_counter() - t0) / 20
    assert hits, "热查询应命中"
    assert hits[0].entry.subject == "地块编号1", \
        "数字后缀必须把目标条目从 1000 条共享前缀候选中判别出来"
    assert elapsed < 0.05, f"热查询 {elapsed * 1000:.1f}ms 超出宽松上限"


def test_index_org_partition_is_physical():
    idx = ai.AssociativeIndex()
    idx.upsert(_mk_entry("我司园区", org_id="org-a"))
    idx.upsert(_mk_entry("B 私有地块", org_id="org-b"))
    assert [h.entry.subject for h in idx.search("org-a", "我司园区",
                                                now=time.time())] == ["我司园区"]
    assert [h.entry.subject for h in idx.search("org-b", "私有地块",
                                                now=time.time())] == ["B 私有地块"]
    # 对方的私有记忆在本租户任何 query 下都不可达
    assert idx.search("org-a", "私有地块 B", now=time.time()) == []
    assert idx.search("org-b", "我司园区", now=time.time()) == []
    # 空 org 拒绝（fail-closed）
    assert idx.search("", "我司园区", now=time.time()) == []


def test_sensitive_memory_never_enters_index(db):
    _seed(db, _place("涉密地块", sensitive=True))
    audit_rows = s.get_active_memories(
        db, "org-a", SCOPE_SESSION, "sess-1", include_sensitive=True)
    assert audit_rows, "store 审计面仍可见"
    r = pr.ProactiveRetriever(db_factory=None)
    n = r.sync_org("org-a", db, session_id="sess-1")
    assert n == 0, "sensitive 记忆不得进入热索引"


# ── B. 主动唤醒管道：租户隔离 100% ────────────────────────────────────────


def test_awake_cross_org_fully_blocked(factory):
    with factory() as db:
        _seed(db, _place("我司园区", bbox=(104.0, 30.5, 104.1, 30.6)))
    r = pr.ProactiveRetriever(db_factory=factory)
    hit = r.awake("分析我司园区周边配套", org_id="org-a", session_id="sess-1")
    assert hit.resolved_place is not None
    assert hit.resolved_place["name"] == "我司园区"
    # 租户 B：同 query 同文本 → 三通道全空
    miss = r.awake("分析我司园区周边配套", org_id="org-b", session_id="sess-1")
    assert miss.cards == []
    assert miss.resolved_place is None
    assert "我司园区" not in pr.render_cards(miss.cards)
    assert r.resolve_place("我司园区", org_id="org-b", session_id="sess-1") is None
    # 无 org（匿名）→ 拒绝检索
    anon = r.awake("我司园区", org_id="")
    assert anon.cards == [] and anon.resolved_place is None
    assert anon.trace.get("reason") == "no_org"


def test_sync_org_loads_only_tenant_rows(factory):
    with factory() as db:
        _seed(db,
              _place("A 园区", org_id="org-a"),
              _place("B 园区", org_id="org-b"))
    r = pr.ProactiveRetriever(db_factory=factory)
    n = r.sync_org("org-b", factory(), session_id="sess-1")
    assert n == 1
    hit = r.awake("A 园区 B 园区", org_id="org-b", session_id="sess-1",
                  sync=False)
    titles = {c.title for c in hit.cards}
    assert "B 园区" in titles
    assert "A 园区" not in titles


def test_awake_touches_hit_counts_for_consolidation(factory):
    with factory() as db:
        (row,) = _seed(db, _place("我司园区"))
        mem_id = row.id
    r = pr.ProactiveRetriever(db_factory=factory)
    r.awake("我司园区", org_id="org-a", session_id="sess-1")
    r.awake("我司园区 周边配套", org_id="org-a", session_id="sess-1")
    entry = r.index.get("org-a", mem_id)
    assert entry is not None
    assert entry.hit_count >= 2, "命中计数必须回写索引供 consolidator 晋升判定"


# ── C. 模糊指代消歧 ───────────────────────────────────────────────────────


def test_vague_reference_signals_detection():
    signals = pr.vague_reference_signals("分析上次看的那个地块的周边配套")
    assert signals, "回指词必须被检出"
    assert not pr.vague_reference_signals("分析成都市的人口密度分布")


def test_resolve_place_unique_session_memory(factory):
    with factory() as db:
        _seed(db, _place("天府软件园",
                         bbox=(104.06, 30.54, 104.08, 30.56)))
    r = pr.ProactiveRetriever(db_factory=factory)
    place = r.resolve_place("上次看的那个地块", org_id="org-a",
                            session_id="sess-1")
    assert place is not None
    assert place["name"] == "天府软件园"
    assert place["bbox"] == [104.06, 30.54, 104.08, 30.56]


def test_resolve_place_alias_token_match(factory):
    with factory() as db:
        _seed(db, _place("成都经济技术开发区", scope=SCOPE_USER, scope_id="u-1",
                         aliases=["开发区", "经开区"]))
    r = pr.ProactiveRetriever(db_factory=factory)
    place = r.resolve_place("本市开发区今年新供的地块", org_id="org-a",
                            user_id="u-1")
    assert place is not None
    assert place["name"] == "成都经济技术开发区"


def test_resolve_place_tie_returns_none(factory):
    with factory() as db:
        _seed(db, _place("天府软件园"), _place("科学城"))
    r = pr.ProactiveRetriever(db_factory=factory)
    place = r.resolve_place("上次看的那个地块", org_id="org-a",
                            session_id="sess-1")
    assert place is None, "并列候选必须返回 None 交澄清，绝不静默赌博"


def test_resolve_place_no_memory_returns_none(factory):
    r = pr.ProactiveRetriever(db_factory=factory)
    assert r.resolve_place("上次看的那个地块", org_id="org-a",
                           session_id="sess-9") is None


def test_awake_crs_preference_card_from_user_scope(factory):
    with factory() as db:
        _seed(db, MemoryWriteRequest(
            kind=KIND_USER_CARTO_PREF,
            scope=SCOPE_USER, scope_id="u-1", subject="投影坐标系",
            value={"value": "CGCS2000 / 3-degree Gauss-Kruger CM 105E"},
            evidence=MemoryEvidence(source=SOURCE_USER_DECISION),
            confidence=0.9, org_id="org-a", user_id="u-1",
        ))
    r = pr.ProactiveRetriever(db_factory=factory)
    result = r.awake("把我们园区去年的变化画出来（沿用常用投影）",
                     org_id="org-a", user_id="u-1", session_id="sess-1")
    families = {c.family for c in result.cards}
    assert "crs_preference" in families
    assert any(c.pinned for c in result.cards), "显式偏好必须带 pinned 标记"
    assert result.signals, "pinned 命中应产出可读信号"
    assert result.resolved_place is None, "无空间实体命中时不得硬造地名"


# ── D. 记忆更新与淘汰（consolidator）────────────────────────────────────


def _load_into_index(retriever: pr.ProactiveRetriever, db, org_id: str,
                     session_id: str) -> list:
    rows = s.get_active_memories(db, org_id, SCOPE_SESSION, session_id,
                                 include_sensitive=True)
    for rec in rows:
        retriever.index.upsert(pr.entry_from_record(rec, now_ts=time.time()))
    return [rec.id for rec in rows]


def test_consolidation_promotes_strategy_to_user_scope(factory, retriever):
    with factory() as db:
        _seed(db, MemoryWriteRequest(
            kind=KIND_SUCCESSFUL_STRATEGY,
            scope=SCOPE_SESSION, scope_id="sess-1", subject="morans_i 全局检验",
            value={"recipe": "moran", "note": "通过评审"},
            evidence=MemoryEvidence(source=SOURCE_REVIEW_PASSED),
            confidence=0.8, org_id="org-a", user_id="u-1",
        ))
        mem_ids = _load_into_index(retriever, db, "org-a", "sess-1")
        assert len(mem_ids) == 1
        for _ in range(2):
            retriever.index.touch("org-a", mem_ids)
        result = mc.consolidate_session_sync(
            "sess-1", org_id="org-a", user_id="u-1", project_id=None,
            db=db, retriever=retriever,
        )
        assert result["promoted"] == 1
        user_rows = s.get_active_memories(db, "org-a", SCOPE_USER, "u-1")
        assert len(user_rows) == 1
        promoted = user_rows[0]
        assert promoted.subject == "morans_i 全局检验"
        assert promoted.scope == SCOPE_USER
        assert promoted.value.get("consolidated_from", {}).get(
            "from_session") == "sess-1"
        # 原 session 行保留（审计链不断，到 TTL 自然失效）
        assert len(s.get_active_memories(
            db, "org-a", SCOPE_SESSION, "sess-1",
            kinds=(KIND_SUCCESSFUL_STRATEGY,))) == 1


def test_consolidation_entity_promotes_to_project_only(factory, retriever):
    with factory() as db:
        _seed(db, _place("我司园区"))
        mem_ids = _load_into_index(retriever, db, "org-a", "sess-1")
        for _ in range(3):
            retriever.index.touch("org-a", mem_ids)
        # 无 project_id：实体类不晋升（不造孤儿作用域）
        r0 = mc.consolidate_session_sync(
            "sess-1", org_id="org-a", user_id="u-1", project_id=None,
            db=db, retriever=retriever,
        )
        assert r0["promoted"] == 0
        # 有 project_id：晋升到 project
        r1 = mc.consolidate_session_sync(
            "sess-1", org_id="org-a", user_id="u-1", project_id="proj-9",
            db=db, retriever=retriever,
        )
        assert r1["promoted"] == 1
        proj_rows = s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-9")
        assert len(proj_rows) == 1 and proj_rows[0].subject == "我司园区"


def test_consolidation_pins_explicit_preference(factory, retriever):
    with factory() as db:
        _seed(db, MemoryWriteRequest(
            kind=KIND_USER_CARTO_PREF,
            scope=SCOPE_SESSION, scope_id="sess-1", subject="分级阈值",
            value={"value": "行业分类用 natural breaks 5 级"},
            evidence=MemoryEvidence(source=SOURCE_USER_CORRECTION),
            confidence=0.9, org_id="org-a", user_id="u-1",
            ttl_s=3600,
        ))
        (before,) = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
        assert before.expires_at is not None
        result = mc.consolidate_session_sync(
            "sess-1", org_id="org-a", user_id="u-1", project_id=None,
            db=db, retriever=retriever,
        )
        assert result["pinned"] == 1
        (after,) = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
        assert after.expires_at is None, "显式偏好必须固化（永不过期）"


def test_consolidation_rejects_failure_memory_promotion(factory, retriever):
    with factory() as db:
        _seed(db, MemoryWriteRequest(
            kind=KIND_PROVIDER_FAILURE,
            scope=SCOPE_SESSION, scope_id="sess-1", subject="某平台字段缺失",
            value={"failure_class": "field_missing"},
            evidence=MemoryEvidence(source=SOURCE_TOOL_FAILURE),
            confidence=0.7, org_id="org-a", user_id="u-1",
        ))
        mem_ids = _load_into_index(retriever, db, "org-a", "sess-1")
        for _ in range(5):
            retriever.index.touch("org-a", mem_ids)
        result = mc.consolidate_session_sync(
            "sess-1", org_id="org-a", user_id="u-1", project_id="proj-9",
            db=db, retriever=retriever,
        )
        assert result["promoted"] == 0, \
            "失效避坑类（短命证据）高频使用也不得晋升为长效记忆"
        assert s.get_active_memories(db, "org-a", SCOPE_USER, "u-1") == []
        assert s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-9") == []


def test_expired_low_hit_memory_not_recalled(factory, retriever):
    with factory() as db:
        _seed(db, MemoryWriteRequest(
            kind=KIND_PROVIDER_FAILURE,
            scope=SCOPE_SESSION, scope_id="sess-1", subject="过期失败经验",
            value={"failure_class": "timeout"},
            evidence=MemoryEvidence(source=SOURCE_TOOL_FAILURE),
            confidence=0.7, org_id="org-a", user_id="u-1",
            ttl_s=3600,
        ))
        future = _now() + timedelta(hours=2)
        assert s.sweep_expired(db, now=future) == 1
        db.commit()
        retriever.sync_org("org-a", db, session_id="sess-1")
    hit = retriever.awake("过期失败经验", org_id="org-a", session_id="sess-1",
                          sync=False)
    assert hit.cards == [], "失效记忆绝不再召回"


def test_consolidation_never_crosses_tenant(factory, retriever):
    with factory() as db:
        _seed(db, _place("我司园区"))
        mem_ids = _load_into_index(retriever, db, "org-a", "sess-1")
        for _ in range(2):
            retriever.index.touch("org-a", mem_ids)
        # org-b 的身份不得把 org-a 的记忆晋升过去
        result = mc.consolidate_session_sync(
            "sess-1", org_id="org-b", user_id="u-b", project_id="proj-b",
            db=db, retriever=retriever,
        )
        assert result["promoted"] == 0
        assert s.get_active_memories(db, "org-b", SCOPE_PROJECT, "proj-b") == []
        assert s.get_active_memories(db, "org-b", SCOPE_USER, "u-b") == []


# ── E. 挂载：intent 消歧与 turn_context 切片 ─────────────────────────────


def _place_card(subject="天府软件园", bbox=(104.06, 30.54, 104.08, 30.56)):
    return pr.MemoryContextCard(
        card_id="m-1", family="spatial_entity", title=subject,
        summary=f"{subject}（district）",
        resolved_place={"name": subject, "level": "district",
                        "bbox": list(bbox)},
        bbox=bbox, scope=SCOPE_SESSION, confidence=0.85, score=0.9,
        reasons=["subject_exact"], refs=[], pinned=False,
    )


def test_apply_memory_hints_backfills_scope_with_audit():
    intent = MapRequestIntent(query="分析上次看的那个地块周边配套",
                              confidence=0.62)
    hinted = apply_proactive_memory_hints(
        intent, [_place_card()],
        resolved_place={"name": "天府软件园", "level": "district",
                        "bbox": [104.06, 30.54, 104.08, 30.56]},
        signals=("vague_reference:那个",),
    )
    assert hinted.scope.name == "天府软件园"
    assert hinted.scope.level == "district"
    assert any(r.startswith("memory_proactive_scope:")
               for r in hinted.matched_rules)
    pm = hinted.intent_evidence["proactive_memory"]
    assert pm["resolved_place"]["name"] == "天府软件园"
    assert pm["applied"] is True
    assert "vague_reference:那个" in pm["signals"]
    assert hinted.confidence == pytest.approx(0.62 * 0.9)
    assert any("记忆" in a for a in hinted.assumptions)


def test_apply_memory_hints_never_overrides_fresh_scope():
    intent = MapRequestIntent(query="分析成都市高新区周边配套",
                              scope=ScopeIntent(name="成都市", level="city"),
                              confidence=0.7)
    hinted = apply_proactive_memory_hints(
        intent, [_place_card(subject="别的园区")],
        resolved_place={"name": "别的园区", "level": "district",
                        "bbox": [104.0, 30.0, 104.1, 30.1]})
    assert hinted.scope.name == "成都市", "fresh 解析永远优先"
    assert hinted.scope.level == "city"
    assert hinted.confidence == intent.confidence, "未回填不得折扣"
    assert hinted.intent_evidence["proactive_memory"]["applied"] is False


def test_apply_memory_hints_without_place_only_records_families():
    intent = MapRequestIntent(query="分析周边配套")
    card = pr.MemoryContextCard(
        card_id="m-2", family="crs_preference", title="投影坐标系",
        summary="CGCS2000", resolved_place=None, bbox=None,
        scope=SCOPE_USER, confidence=0.9, score=0.8, reasons=[], refs=[],
        pinned=True,
    )
    hinted = apply_proactive_memory_hints(intent, [card])
    assert hinted.scope.name == ""
    assert hinted.intent_evidence["proactive_memory"][
        "families_present"] == ["crs_preference"]
    assert hinted.confidence == intent.confidence


def test_resolve_map_request_intent_stays_pure():
    """纯函数纪律：无显式记忆注入时绝不能有任何记忆回填副作用。"""
    intent = resolve_map_request_intent("分析上次看的那个地块")
    assert intent.scope.name == ""
    assert not any(r.startswith("memory_proactive_scope")
                   for r in intent.matched_rules)
    assert "proactive_memory" not in (intent.intent_evidence or {})


def test_adaptive_entry_probes_memory_end_to_end(factory):
    with factory() as db:
        _seed(db, _place("天府软件园", bbox=(104.06, 30.54, 104.08, 30.56)))
    pr.set_db_factory(factory)
    pr.default_proactive_retriever.reset()
    try:
        intent, _request = resolve_intent_adaptive(
            "分析上次看的那个地块的产业分布",
            use_llm=False, session_id="sess-1",
            org_id="org-a", user_id="u-1",
        )
        assert intent.scope.name == "天府软件园"
        assert intent.intent_evidence["proactive_memory"]["signals"], \
            "消歧必须留痕"
        # 关闭记忆探查 → 无回填（回归保护）
        intent_off, _ = resolve_intent_adaptive(
            "分析上次看的那个地块的产业分布",
            use_llm=False, session_id="sess-1",
            org_id="org-a", user_id="u-1", use_memory=False,
        )
        assert intent_off.scope.name == ""
    finally:
        pr.set_db_factory(None)
        pr.default_proactive_retriever.reset()


def test_adaptive_entry_memory_failure_fail_open(factory, monkeypatch):
    pr.set_db_factory(factory)
    pr.default_proactive_retriever.reset()
    try:
        def _boom(*a, **k):
            raise RuntimeError("retriever down")

        monkeypatch.setattr(
            "app.services.gis_harness.intent._probe_proactive_memory", _boom)
        intent, _ = resolve_intent_adaptive(
            "分析上次看的那个地块", use_llm=False, session_id="sess-1",
            org_id="org-a", use_memory=True,
        )
        assert intent.task, "记忆探查失败绝不能炸意图解析"
        assert intent.scope.name == ""
        assert "proactive_memory" not in (intent.intent_evidence or {})
    finally:
        pr.set_db_factory(None)
        pr.default_proactive_retriever.reset()


@pytest.mark.asyncio
async def test_turn_context_proactive_slice(factory, monkeypatch):
    from app.services.gis_memory import queries as q
    from app.services.gis_situation import turn_context as tc

    class _FakeSD:
        def __init__(self):
            self.state = {}

        async def get_map_state(self, sid):
            return dict(self.state.get(sid, {}))

        async def set_map_state(self, sid, key, value, seq=None):
            self.state.setdefault(sid, {})[key] = value
            return True

    fake = _FakeSD()
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake)
    pr.set_db_factory(factory)
    pr.default_proactive_retriever.reset()
    try:
        await fake.set_map_state("sess-1", q.MEMORY_ORG_STATE_KEY, "org-a")
        await fake.set_map_state("sess-1", q.MEMORY_USER_STATE_KEY, "u-1")
        with factory() as db:
            _seed(db, _place("天府软件园",
                             bbox=(104.06, 30.54, 104.08, 30.56)))
        slice_text = await tc.build_proactive_memory_slice(
            "sess-1", query_text="分析上次看的那个地块")
        assert "[GIS_MEMORY_PROACTIVE]" in slice_text
        assert "天府软件园" in slice_text
        # 无烙印身份 → 无切片（fail-closed）
        assert await tc.build_proactive_memory_slice("sess-unknown") == ""
    finally:
        pr.set_db_factory(None)
        pr.default_proactive_retriever.reset()


def _stub_situation_chain(monkeypatch, tc, text="SITUATION\n"):
    monkeypatch.setattr(tc, "situation_enabled", lambda: True)

    async def _anything(*a, **k):
        return object()

    monkeypatch.setattr(tc, "compile_situation", _anything)
    monkeypatch.setattr(tc, "load_snapshot", _anything)
    monkeypatch.setattr(tc, "diff_situation", lambda prev, cur: object())
    monkeypatch.setattr(tc, "advance_snapshot", _anything)

    class _Proj:
        pass

    _Proj.text = text
    monkeypatch.setattr(tc, "render_situation_for_context",
                        lambda s, delta=None: _Proj)


@pytest.mark.asyncio
async def test_build_situation_turn_context_appends_slice(monkeypatch):
    from app.services.gis_situation import turn_context as tc

    async def fake_slice(sid, query_text=""):
        return "[GIS_MEMORY_PROACTIVE] 天府软件园\n"

    _stub_situation_chain(monkeypatch, tc)
    monkeypatch.setattr(tc, "build_proactive_memory_slice", fake_slice)
    text = await tc.build_situation_turn_context(
        "sess-1", query_text="那个地块")
    assert text is not None
    assert text.startswith("SITUATION")
    assert "[GIS_MEMORY_PROACTIVE]" in text, "主动切片必须追加在情境投影之后"


@pytest.mark.asyncio
async def test_situation_turn_context_fail_open_when_slice_raises(
        monkeypatch):
    from app.services.gis_situation import turn_context as tc

    async def _boom(sid, query_text=""):
        raise RuntimeError("slice down")

    _stub_situation_chain(monkeypatch, tc)
    monkeypatch.setattr(tc, "build_proactive_memory_slice", _boom)
    text = await tc.build_situation_turn_context("sess-1")
    assert text == "SITUATION\n", "切片失败只丢切片，绝不阻断情境块（fail-open）"


@pytest.mark.asyncio
async def test_situation_disabled_returns_none(monkeypatch):
    from app.services.gis_situation import turn_context as tc

    monkeypatch.setattr(tc, "situation_enabled", lambda: False)
    assert await tc.build_situation_turn_context("sess-1") is None


# ── F. 卡片渲染：预算 / 转义 / 有界 ──────────────────────────────────────


def test_render_cards_budget_and_fencing():
    cards = [
        pr.MemoryContextCard(
            card_id=f"m-{i}", family="spatial_entity", title=f"地块编号{i}",
            summary=f"地块编号{i}（district）bbox≈[104.0,30.0,104.1,30.1]",
            resolved_place={"name": f"地块编号{i}", "level": "district",
                            "bbox": [104.0, 30.0, 104.1, 30.1]},
            bbox=(104.0, 30.0, 104.1, 30.1), scope=SCOPE_SESSION,
            confidence=0.8, score=0.7 + i * 0.01, reasons=[], refs=[],
            pinned=False,
        )
        for i in range(40)
    ]
    block = pr.render_cards(cards, char_budget=900)
    assert block.startswith("[GIS_MEMORY_PROACTIVE]")
    assert len(block) <= 900 + 8, "有界块必须守预算"
    assert "省略" in block
    # 不可信文本必须转义（存储型注入防线）
    evil = pr.MemoryContextCard(
        card_id="m-evil", family="spatial_entity",
        title="<script>alert(1)</script>",
        summary="x", resolved_place=None, bbox=None, scope=SCOPE_SESSION,
        confidence=0.8, score=0.9, reasons=[], refs=[], pinned=False,
    )
    fenced = pr.render_cards([evil])
    assert "<script>" not in fenced
    assert "alert(1)" in fenced, "内容保留但尖括号必须被转义"


def test_awake_result_bounded(factory):
    with factory() as db:
        for i in range(12):
            _seed(db, _place(f"候选地块{i}"))
    r = pr.ProactiveRetriever(db_factory=factory)
    result = r.awake("候选地块", org_id="org-a", session_id="sess-1", limit=6)
    assert len(result.cards) <= 6
    assert result.latency_ms >= 0.0
    assert isinstance(result.trace, dict)
