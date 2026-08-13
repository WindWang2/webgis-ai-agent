"""Baseline runner — executes in the pristine master checkout, in-place.

Run from the master worktree root (read-only checkout; nothing is written
here, the module itself lives in the v3 worktree and is resolved via
``PYTHONPATH``):

    cd /home/kevin/projects/webgis/webgis-ai-agent
    PYTHONPATH=<v3>/tests/benchmarks /opt/miniconda3/bin/python -m _master_context_baseline

Prints one JSON object on stdout with:

- ``registry`` — the real 149-tool registry build (master code; identical
  tier/domain annotations to v3, since the tool modules are committed files).
- ``selection`` — per-turn tool-schema selection for the three benchmark
  turns (count / serialized chars / chars/4 tokens / active domains).
- ``ctx_4turn`` — assembled context block breakdown for the representative
  4-turn session (master: no ``tools_payload_chars``, ``estimated_tokens``
  excludes the tools payload; ``[最近对话上下文]`` scans the full history).
- ``ctx_2turn`` — same, for a 2-turn session with realistic-length messages
  (used to measure the ``[最近对话上下文]`` duplication master emits).
"""
from __future__ import annotations

import asyncio
import json
import os
import time

# Must be set before importing app modules (session_data singleton factory).
os.environ["USE_REDIS"] = "false"

from app.tools import init_tools  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402
from app.services.tool_catalog import ToolCatalog  # noqa: E402
from app.services.chat.context_assembler import ChatContextAssembler  # noqa: E402

import _ctx_fixture  # noqa: E402  (resolved via PYTHONPATH from the v3 worktree)


def measure_selection(catalog: ToolCatalog) -> dict:
    cases = {
        "a_no_domain_first_turn": "帮我分析一下当前地图上的数据情况",
        "b_network_turn": "帮我算一下从 A 到 B 的最短路径，看看开车多久能到",
        "c_multi_domain_turn": "对比成都各区县的医院分布热点，评估通勤可达性，并结合遥感 NDVI 影像",
    }
    out = {}
    for key, text in cases.items():
        sid = f"bench-base-sel-{key}"
        schemas = catalog.select_schemas(text, session_id=sid, declared_domains=None)
        payload = json.dumps(schemas, ensure_ascii=False)
        out[key] = {
            "count": len(schemas),
            "chars": len(payload),
            "tokens": int(len(payload) / 4),
            "active_domains": sorted(catalog.active_domains(sid)),
        }
    return out


def measure_rep_turn_selection(catalog: ToolCatalog, session_id: str) -> dict:
    """The selection _select_tools would make for the assembled fixture turn."""
    schemas = catalog.select_schemas(
        "把这些图层都显示出来",
        session_id=session_id,
        declared_domains={"statistics", "core"},
    )
    payload = json.dumps(schemas, ensure_ascii=False)
    return {
        "count": len(schemas),
        "chars": len(payload),
        "tokens": int(len(payload) / 4),
    }


async def assemble_baseline(n_turns: int, long_msgs: bool) -> dict:
    sid = f"bench-base-{n_turns}t-{int(long_msgs)}"
    messages = await _ctx_fixture.build_fixture(
        session_id=sid, n_turns=n_turns, long_msgs=long_msgs
    )
    res = await ChatContextAssembler().assemble(sid, messages)
    bb = _ctx_fixture.block_breakdown(res.messages)
    bb["estimated_tokens"] = res.estimated_tokens
    bb["plan_block_chars"] = _ctx_fixture.plan_block_chars(res.messages)
    bb["last_analysis_chars"] = _ctx_fixture.last_analysis_chars(res.messages)
    bb["history_turns_included"] = res.history_turns_included
    return bb


def main() -> None:
    t0 = time.perf_counter()
    registry = ToolRegistry()
    init_tools(registry)
    catalog = ToolCatalog(registry)
    out = {
        "registry": {
            "n_tools": len(registry.list_tools()),
            "build_seconds": round(time.perf_counter() - t0, 2),
        },
        "selection": measure_selection(catalog),
        "rep_turn_selection": measure_rep_turn_selection(catalog, _ctx_fixture.SESSION_ID),
        "ctx_4turn": asyncio.run(assemble_baseline(n_turns=4, long_msgs=False)),
        "ctx_2turn": asyncio.run(assemble_baseline(n_turns=2, long_msgs=True)),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
