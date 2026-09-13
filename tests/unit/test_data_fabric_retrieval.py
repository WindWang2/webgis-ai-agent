"""ads-v1 DS2 retrieval unit tests (ADR-0172): hybrid retrieval mechanics.

Covers: tokenizer, BM25 determinism, structured filters, factor-based
reasoning, degraded (keyword-only) mode, low-confidence clarification path,
D1 upgrade seam, and the intent-detector candidate seam.
"""
from __future__ import annotations

import pytest

from app.services.data_fabric.retrieval import get_retrieval_service
from app.services.data_fabric.retrieval.cards import DatasetCard, build_cards
from app.services.data_fabric.retrieval.embeddings import (
    KeywordOnlyProvider,
    cosine,
    register_provider,
)
from app.services.data_fabric.retrieval.keyword import BM25Index, tokenize
from app.services.data_fabric.retrieval.ranker import LOW_CONFIDENCE_THRESHOLD, rank_card
from app.services.data_fabric.retrieval.service import RetrievalFilters


@pytest.fixture(autouse=True)
def _keyword_only_mode():
    """Force keyword-only (degraded) retrieval for unit determinism; restore."""
    register_provider(KeywordOnlyProvider())
    yield
    register_provider(None)


# ── tokenizer / BM25 ─────────────────────────────────────────────────────────


def test_tokenizer_ascii_and_cjk_bigrams():
    tokens = tokenize("空气质量 PM2.5监测")
    assert "pm2.5" in tokens and "监测" in tokens and "空气" in tokens
    assert all(t == t.lower() for t in tokens)


def test_bm25_deterministic_and_ranked():
    triples = [
        ("a", "空气质量监测站点数据", ["空气"]),
        ("b", "道路交通流量", ["交通"]),
        ("c", "全球生物多样性观测", ["物种"]),
    ]
    index = BM25Index(triples)
    r1 = index.search("空气质量 监测", top_k=3)
    r2 = index.search("空气质量 监测", top_k=3)
    assert r1 == r2 and r1 and r1[0][0] == "a"


# ── ranking factors / reasons ─────────────────────────────────────────────────


def _card(**kw) -> DatasetCard:
    base = dict(
        card_id="src/ds", source_id="src", dataset_id="ds", title="测试数据集",
        keywords=["测试"], verified=True, local=False,
    )
    base.update(kw)
    return DatasetCard(**base)


def test_rank_card_breakdown_and_top_factor():
    breakdown = rank_card(_card(local=True), relevance=0.9)
    assert 0.0 < breakdown["score"] <= 1.0
    assert set(breakdown["factors"]) == {"relevance", "coverage", "freshness", "cost", "trust"}
    assert breakdown["top_factor"] in breakdown["factors"]
    # local asset gets the top cost factor
    assert breakdown["factors"]["cost"] == 1.0


def test_rank_card_unverified_downweights_trust():
    verified = rank_card(_card(verified=True), relevance=0.8)
    unverified = rank_card(_card(verified=False), relevance=0.8)
    assert verified["score"] > unverified["score"]


def test_coverage_bbox_filter_match_and_miss():
    hit = rank_card(_card(bbox=[100.0, 20.0, 130.0, 50.0]), relevance=0.8, bbox=[110.0, 30.0, 115.0, 35.0])
    miss = rank_card(_card(bbox=[100.0, 20.0, 130.0, 50.0]), relevance=0.8, bbox=[0.0, 0.0, 1.0, 1.0])
    assert hit["factors"]["coverage"] == 1.0
    assert miss["factors"]["coverage"] == 0.0


# ── end-to-end retrieval over the real registry ───────────────────────────────


def test_retrieve_returns_reason_and_confidence():
    resp = get_retrieval_service().retrieve("北京空气质量监测", top_k=3)
    assert resp.hits
    top = resp.hits[0]
    assert top.reason and 0.0 <= top.confidence <= 1.0
    assert resp.degraded is True  # keyword-only provider registered
    assert "degraded" in top.reason


def test_retrieve_never_silently_returns_weak_first_hit():
    resp = get_retrieval_service().retrieve("xyzzy 完全无关词表 zzz", top_k=3)
    # either no hits or the clarification flag — never a confident-looking
    # silent first pick
    if resp.hits:
        assert resp.clarification_needed
        assert resp.hits[0].confidence < LOW_CONFIDENCE_THRESHOLD
        assert resp.clarification_reason


def test_structured_filters_exclude():
    svc = get_retrieval_service()
    # a query with only non-local matches → local_only correctly excludes all
    air = svc.retrieve("空气质量", top_k=10)
    assert air.hits and all(not h.card["source_id"].startswith("local_") for h in air.hits)
    assert svc.retrieve("空气质量", top_k=10, filters=RetrievalFilters(local_only=True)).hits == []
    # a query that matches a local asset → local_only keeps it
    local_hits = svc.retrieve(
        "县域GDP统计", top_k=10, filters=RetrievalFilters(local_only=True)
    ).hits
    assert local_hits and all(h.card["source_id"].startswith("local_") for h in local_hits)


def test_cards_build_from_registry_and_cover_local_assets():
    cards = build_cards()
    ids = {c.card_id for c in cards}
    assert len(cards) >= 40  # DS1 declared datasets + local assets
    assert "local_osm/pois" in ids and "local_yearbook/township_stats" in ids


def test_card_to_d1_upgrade():
    card = next(c for c in build_cards() if c.card_id == "local_osm/pois")
    d1 = card.to_d1()
    assert d1.id == "pois"
    assert d1.quality_signals.verified is True
    assert d1.cost_hint.local is True
    assert d1.temporal_coverage is None or d1.temporal_coverage.declared_only


def test_embedding_provider_cosine_helper():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([], [1.0]) == 0.0


# ── intent detector seam (A3 upgrade) ─────────────────────────────────────────


def test_intent_detector_candidates_via_retrieval():
    from app.services.explorer.intent_detector import IntentDetector

    candidates = IntentDetector().retrieve_dataset_candidates("北京空气质量监测数据", top_k=3)
    assert candidates and "clarification" not in candidates[0]
    assert candidates[0]["source_id"] == "beijing_gov"


def test_intent_detector_detect_path_still_works():
    from app.services.explorer.intent_detector import IntentDetector

    decision = IntentDetector().detect("北京有哪些学校？", [], [])
    assert decision.decision in {"auto_execute", "ask_user", "skip"}
