"""#1951 S50-007..015: final context accounting and safe bounded views."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

import pytest

from app.services.chat.context_projections import ProjectionCache, bound_text
from app.services.chat.context_policy import condense_text_to_budget, retrim_messages_for_retry
from app.services.chat.context.history_compression import _estimate_tokens, _message_tokens
from app.services.chat.context_builder import build_last_analysis_context, _env_timestamp
from app.services.chat.event_resume import TurnEventBuffer, TurnResumeRegistry


def test_projection_fingerprint_metadata_and_values_evict_together():
    cache = ProjectionCache(max_entries=2)
    for i in range(100):
        cache.get_or_build("ns", str(i), str(i), lambda: "projected")
    assert len(cache._fps) == cache.stats()["entries"] == 2
    assert not cache._fingerprint_matches("ns", "0", "0")


def test_projection_overwrite_at_capacity_preserves_unrelated_entry():
    cache = ProjectionCache(max_entries=2)
    cache.get_or_build("ns", "a", "old", lambda: "old")
    cache.get_or_build("ns", "b", "other", lambda: "other")
    assert cache.get_or_build("ns", "b", "changed", lambda: "new") == ("new", False)
    assert cache.get_or_build("ns", "a", "old", lambda: "should not rebuild") == ("old", True)


@pytest.mark.parametrize("text", ["a" * 10000, "字" * 10000])
def test_condense_uses_actual_character_density_without_wasting_half_budget(text):
    output, changed = condense_text_to_budget(text, 1000)
    assert changed and 900 <= _estimate_tokens(output) <= 1000
    assert condense_text_to_budget(text, 1000) == (output, True)


@pytest.mark.parametrize("budget", [1, 2, 3, 10, 16])
def test_condense_small_budget_never_returns_a_64_character_overflow(budget):
    output, changed = condense_text_to_budget("文字 & ASCII" * 100, budget)
    assert changed and _estimate_tokens(output) <= budget


def test_bounded_untrusted_fences_remain_closed_and_entities_intact():
    text = "prefix <untrusted_layer_name><nested>" + "汉字&amp;&lt;payload&gt;" * 40 + "</nested></untrusted_layer_name>"
    for cap in range(0, 160):
        bounded = bound_text(text, cap)
        assert len(bounded) <= cap
        ET.fromstring("<root>" + bounded + "</root>")


def test_retrim_handles_null_and_multimodal_content_without_mutating_messages():
    messages = [{"role": "system", "content": None}, {"role": "user", "content": [
        {"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "https://example.test/image"}},
    ]}]
    before = deepcopy(messages)
    assert retrim_messages_for_retry(messages) is None
    assert messages == before


def test_retrim_returns_real_fold_only_progress_and_preserves_safety_receipt():
    from app.services.chat.context.history_compression import SAFETY_PIN_MARKERS
    receipt = next(iter(SAFETY_PIN_MARKERS)) + " must survive verbatim" + "P" * 10000
    tool_calls = [{"id": f"call-{i}", "function": {"name": "tool"}} for i in range(14)]
    messages = [{"role": "user", "content": "goal"}, {"role": "assistant", "content": None, "tool_calls": tool_calls}]
    messages.extend({"role": "tool", "tool_call_id": f"call-{i}", "content": receipt if i == 0 else "X" * 10000} for i in range(14))
    result = retrim_messages_for_retry(messages)
    assert result is not None and len(result) == len(messages)
    assert sum(_message_tokens(m) for m in result) < sum(_message_tokens(m) for m in messages)
    assert result[2]["content"] == receipt
    assert [m.get("tool_call_id") for m in result] == [m.get("tool_call_id") for m in messages]


def _old_multimodal_history():
    return [
        {"role": "user", "content": [{"type": "text", "text": "visible request"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATE_IMAGE"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "visible analysis ref:stats-custom_1"}]},
        {"role": "user", "content": "next"}, {"role": "assistant", "content": "next answer"},
        {"role": "user", "content": "latest"}, {"role": "assistant", "content": "latest answer"},
    ]


def test_analysis_summary_uses_text_blocks_and_extensible_store_ref_types():
    summary = build_last_analysis_context(_old_multimodal_history())
    assert "visible request" in summary and "visible analysis" in summary
    assert "ref:stats-custom_1" in summary
    assert "PRIVATE_IMAGE" not in summary and "image_url" not in summary and "'type'" not in summary


@pytest.mark.asyncio
async def test_new_ref_prefix_receives_reload_tombstone(monkeypatch):
    from app.services.chat.context_policy import build_evicted_refs_tombstone
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "1")
    store = SimpleNamespace(ref_exists=AsyncMock(return_value=False))
    result = await build_evicted_refs_tombstone("scoped", [{"role": "assistant", "content": "ref:stats-custom_1"}], store)
    assert "ref:stats-custom_1" in result and "已失效" in result
    store.ref_exists.assert_awaited_once_with("scoped", "ref:stats-custom_1")


def test_environment_time_is_explicitly_utc_and_stable_within_ttl():
    timestamp = _env_timestamp("review-low-time")
    assert timestamp.endswith(" UTC")
    assert _env_timestamp("review-low-time") == timestamp


def test_empty_resume_key_cannot_register_or_retrieve_even_a_known_cursor():
    registry = TurnResumeRegistry()
    private = TurnEventBuffer("", "known message")
    private.record('id: 2\nevent: token\ndata: {"token":"private"}\n\n')
    registry.register("", private)
    assert len(registry) == 0 and registry.get("") is None
    assert registry.find("", "known message", 1) == (None, False)
    # Defense in depth for legacy in-memory entries from a direct consumer.
    registry._buffers[""] = deque([private])
    assert registry.get("") is None and registry.find("", "known message", 1) == (None, False)


@pytest.mark.asyncio
async def test_empty_resume_route_rejects_before_replaying_known_tail(monkeypatch):
    from app.api.routes import chat_resume
    registry = TurnResumeRegistry()
    private = TurnEventBuffer("", "known message")
    private.record('id: 2\nevent: token\ndata: {"token":"PRIVATE_TAIL"}\n\n')
    registry._buffers[""] = deque([private])
    monkeypatch.setattr(chat_resume, "_turn_resume_registry", registry)
    output = "".join([event async for event in chat_resume._resume_generator_impl("", 1, "known message")])
    assert "PRIVATE_TAIL" not in output and '"resumed": false' in output


@pytest.mark.asyncio
async def test_estimated_tokens_describe_final_post_policy_messages(monkeypatch):
    from app.services.chat.context_assembler import ChatContextAssembler
    from app.services.session_data import SessionDataManager
    import app.services.chat.context_policy as policy
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "1")
    async def mutate_final_view(head, head_meta, **kwargs):
        head[0] = {"role": "system", "content": "condensed"}
        head.append({"role": "system", "content": "offloaded ref:stats-kept"})
        return {"executed": True}
    monkeypatch.setattr(policy, "execute_advice", mutate_final_view)
    result = await ChatContextAssembler(store=SessionDataManager()).assemble(
        "review-final-tokens", [{"role": "system", "content": "long" * 5000}, {"role": "user", "content": "question"}],
        tools_payload='{"name":"tool"}',
    )
    assert result.messages[0]["content"] == "condensed"
    assert result.estimated_tokens == sum(_message_tokens(m) for m in result.messages) + _estimate_tokens('{"name":"tool"}')
