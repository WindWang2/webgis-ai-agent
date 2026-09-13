"""ads-v1 DS6 semantic parsing unit tests (ADR-0176).

Covers: time parser determinism + reference-now injection, granularity
vocabulary, field-role inference, the frozen slot contract, and the
end-to-end integration with the DS2 retrieval filters (粒度解析与检索层打通).
"""
from __future__ import annotations

from datetime import date

from app.services.data_fabric.retrieval.service import RetrievalFilters
from app.services.data_fabric.semantic.field_roles import infer_field_roles
from app.services.data_fabric.semantic.granularity import (
    GRANULARITY_ORDER,
    normalize_granularity,
    parse_granularity,
)
from app.services.data_fabric.semantic.slots import SLOT_VOCABULARY, to_intent_slots
from app.services.data_fabric.semantic.time_parser import parse_time_expr

NOW = date(2026, 9, 13)


# ── time parser ──────────────────────────────────────────────────────────────


def test_reference_now_is_injectable():
    r1 = parse_time_expr("近五年", now=NOW)
    r2 = parse_time_expr("近五年", now=date(2030, 1, 1))
    assert r1.start == "2022-01-01"
    assert r2.start == "2026-01-01"


def test_unparseable_returns_none():
    assert parse_time_expr("今天天气不错", now=NOW) is None
    assert parse_time_expr("", now=NOW) is None


def test_quarter_inclusive_end():
    r = parse_time_expr("2024年第一季度", now=NOW)
    assert (r.start, r.end) == ("2024-01-01", "2024-03-31")


def test_half_year():
    r = parse_time_expr("2024年下半年", now=NOW)
    assert (r.start, r.end) == ("2024-07-01", "2024-12-31")


def test_until_range_opens_at_past():
    r = parse_time_expr("截至2022", now=NOW)
    assert r.start == "0001-01-01" and r.end == "2022-12-31"


def test_deterministic():
    a = parse_time_expr("2015年以来", now=NOW)
    b = parse_time_expr("2015年以来", now=NOW)
    assert a == b


# ── granularity ──────────────────────────────────────────────────────────────


def test_granularity_vocabulary_is_canonical():
    assert set(GRANULARITY_ORDER) >= {"province", "city", "county", "township", "grid", "basin"}


def test_parse_granularity_bilingual():
    assert parse_granularity("按县汇总")[0] == "county"
    assert parse_granularity("city level data")[0] == "city"
    assert parse_granularity("流域尺度")[0] == "basin"
    assert parse_granularity("天气预报") is None  # 「报」不触发任何级别词


def test_normalize_granularity_idempotent():
    assert normalize_granularity("county") == "county"
    assert normalize_granularity("县级") == "county"
    assert normalize_granularity(None) is None


# ── field roles ──────────────────────────────────────────────────────────────


def test_infer_field_roles_precedence():
    roles = infer_field_roles([
        {"name": "stat_year", "type": "int"},
        {"name": "city_name", "type": "str"},
        {"name": "adcode", "type": "str"},
        {"name": "gdp", "type": "float"},
        {"name": "object_id", "type": "int"},
    ])
    assert roles["time"] == ["stat_year"]
    assert roles["geo"] == ["city_name", "adcode"]
    assert roles["measure"] == ["gdp"]
    assert roles["id"] == ["object_id"]


def test_infer_field_roles_empty_is_honest():
    assert infer_field_roles([]) == {"time": [], "geo": [], "measure": [], "id": []}


# ── slot contract (frozen) ───────────────────────────────────────────────────


def test_slot_vocabulary_frozen_shape():
    slots = to_intent_slots(
        dataset_candidates=[{"source_id": "s", "dataset_id": "d", "title": "t", "confidence": 0.9}],
        time_range=parse_time_expr("近五年", now=NOW),
        granularity="province",
        measures=["gdp"],
    )
    assert set(slots) <= SLOT_VOCABULARY
    assert slots["time_range"] == {"start": "2022-01-01", "end": "2026-09-13", "granularity": "year"}
    assert slots["granularity"] == "province"
    assert slots["dataset_candidates"][0]["dataset_id"] == "d"
    assert slots["clarification"] == ""


# ── integration: parsed query feeds retrieval filters (端到端打通) ────────────


def test_parsed_query_feeds_retrieval_filters():
    from app.services.data_fabric.retrieval import get_retrieval_service

    query = "近五年按省汇总的PM2.5数据"
    time_range = parse_time_expr(query, now=NOW)
    gran = parse_granularity(query)
    filters = RetrievalFilters(
        temporal=[time_range.start, time_range.end] if time_range else None,
        granularity=gran[0] if gran else None,
    )
    resp = get_retrieval_service().retrieve("PM2.5", top_k=5, filters=filters)
    assert isinstance(resp.hits, list)  # the parse feeds a live retrieval call
    assert time_range.granularity == "year" and gran[0] == "province"
