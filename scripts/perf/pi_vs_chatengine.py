"""Pi vs ChatEngine benchmark harness (quality-e2e-v9, ADR-0146 P6).

WAYFINDER ticket: "性能基准：对比 Pi vs ChatEngine".

Design: the SAME task set drives two backends over HTTP — one launched with
``USE_NEW_AGENT=false`` (ChatEngine arm), one with ``USE_NEW_AGENT=true``
(Pi-bridge arm) — pointed at the SAME LLM provider (config LLM_BASE_URL +
LLM_API_KEY), so the delta isolates the orchestration layer (bridge/pool/turn
machinery vs engine/tool pipeline), not model variance.

Metrics per task (from the SSE stream):
  - first_token_ms:   time to the first ``token`` event (TTFT as users see it)
  - total_ms:         time to ``task_complete``
  - tool_call_ok:     a ``tool_call`` event arrived when the task expects one

Usage (requires a real LLM key in the backend env):
  python scripts/perf/pi_vs_chatengine.py --base http://localhost:8001 \\
      --write docs/dev/pi-vs-chatengine-benchmark.md

Self-check without a key (verifies wiring, no measurements):
  python scripts/perf/pi_vs_chatengine.py --check --base http://localhost:8001
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

REPO = Path(__file__).resolve().parents[2]

#: Same task set for both arms (deterministic intents; the real model decides
#: tool selection — repetition (ROUNDS) smooths provider variance).
TASKS: List[Dict[str, Any]] = [
    {"id": "simple-qa", "message": "用一句话介绍 hotspot_analysis 工具的用途。",
     "expect_tool": False},
    {"id": "buffer-analysis", "message": "对成都市中心 (104.06, 30.67) 做一个 5km 缓冲区分析并出图。",
     "expect_tool": True},
    {"id": "hotspot-analysis", "message": "对 ref:demo-poi 做热点分析，值字段 value。",
     "expect_tool": True},
]
ROUNDS = 3


async def run_task(client: httpx.AsyncClient, base: str, task: Dict[str, Any]) -> Dict[str, Any]:
    start = time.perf_counter()
    first_token: float | None = None
    tool_call = False
    completed = False
    async with client.stream("POST", f"{base}/api/v1/chat/stream",
                             json={"message": task["message"]},
                             timeout=180.0) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload_raw = line[6:].strip()
            if payload_raw == "[DONE]":
                break
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue
            event = payload.get("event") or ""
            if event == "token" and first_token is None:
                first_token = (time.perf_counter() - start) * 1000
            if event == "tool_call":
                tool_call = True
            if event == "task_complete":
                completed = True
                break
    return {
        "task": task["id"],
        "first_token_ms": round(first_token, 1) if first_token else None,
        "total_ms": round((time.perf_counter() - start) * 1000, 1),
        "tool_call": tool_call,
        "tool_call_ok": (tool_call == task["expect_tool"]) or completed,
        "completed": completed,
    }


async def run_arm(base: str, rounds: int = ROUNDS) -> Dict[str, Any]:
    """Run the full task set; per-task aggregate over rounds."""
    per_task: Dict[str, List[Dict[str, Any]]] = {}
    async with httpx.AsyncClient() as client:
        health = await client.get(f"{base}/api/v1/health")
        health.raise_for_status()
        for _ in range(rounds):
            for task in TASKS:
                result = await run_task(client, base, task)
                per_task.setdefault(task["id"], []).append(result)
    summary: Dict[str, Any] = {}
    for task_id, runs in per_task.items():
        ttfts = [r["first_token_ms"] for r in runs if r["first_token_ms"] is not None]
        totals = [r["total_ms"] for r in runs if r["completed"]]
        summary[task_id] = {
            "rounds": len(runs),
            "completed": sum(1 for r in runs if r["completed"]),
            "first_token_ms_median": round(statistics.median(ttfts), 1) if ttfts else None,
            "total_ms_median": round(statistics.median(totals), 1) if totals else None,
            "tool_call_rate": round(
                sum(1 for r in runs if r["tool_call"]) / max(1, len(runs)), 2),
        }
    return {"per_task": summary}


async def check(base: str) -> int:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{base}/api/v1/health")
        print(f"[check] backend {base}: health={health.status_code}")
        print("[check] harness wiring OK — measurements need a real LLM key in "
              "the backend env (LLM_API_KEY); see docs/dev/pi-vs-chatengine-benchmark.md")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[check] backend unreachable: {exc}")
        return 1


def render_markdown(chat_arm: Dict[str, Any], pi_arm: Dict[str, Any],
                    chat_base: str, pi_base: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Pi bridge vs ChatEngine 同任务集基准（P6）",
        "",
        f"- 生成时间：{now}",
        f"- ChatEngine arm：`{chat_base}`（USE_NEW_AGENT=false）",
        f"- Pi bridge arm：`{pi_base}`（USE_NEW_AGENT=true）",
        f"- 任务集：{len(TASKS)} 个确定性意图 × {ROUNDS} 轮；两臂指向**同一** LLM provider",
        "（`LLM_BASE_URL` + `LLM_API_KEY` 相同）—— 差值隔离编排层（bridge/turn 机制 vs",
        "engine/tool pipeline），而非模型差异。",
        "",
        "| 任务 | 臂 | 首 token 中位 (ms) | 总时长中位 (ms) | tool_call 命中率 | 完成率 |",
        "|---|---|---|---|---|---|",
    ]
    for task in TASKS:
        for label, arm in (("ChatEngine", chat_arm), ("Pi bridge", pi_arm)):
            s = arm["per_task"].get(task["id"], {})
            lines.append(
                f"| {task['id']} | {label} | {s.get('first_token_ms_median')} | "
                f"{s.get('total_ms_median')} | {s.get('tool_call_rate')} | "
                f"{s.get('completed')}/{s.get('rounds')} |")
    lines += [
        "",
        "## 复跑方式",
        "",
        "```bash",
        "# arm 1: ChatEngine",
        "USE_NEW_AGENT=false LLM_API_KEY=<key> uvicorn app.main:app --port 8001 &",
        "python scripts/perf/pi_vs_chatengine.py --base http://localhost:8001 --collect chat",
        "# arm 2: Pi bridge（vendor/pi submodule 需检出）",
        "USE_NEW_AGENT=true LLM_API_KEY=<key> uvicorn app.main:app --port 8002 &",
        "python scripts/perf/pi_vs_chatengine.py --base http://localhost:8002 --collect pi",
        "# 汇总落库",
        "python scripts/perf/pi_vs_chatengine.py --compose",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8001")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--collect", choices=["chat", "pi"], default=None)
    parser.add_argument("--compose", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        import asyncio

        return asyncio.run(check(args.base))

    state_file = REPO / ".agent-work" / "pi-vs-chatengine"
    state_file.mkdir(parents=True, exist_ok=True)

    if args.collect:
        import asyncio

        arm = asyncio.run(run_arm(args.base))
        (state_file / f"arm-{args.collect}.json").write_text(
            json.dumps(arm, indent=2), encoding="utf-8")
        print(json.dumps(arm, indent=2))
        return 0

    if args.compose or args.write:
        chat_arm = json.loads((state_file / "arm-chat.json").read_text(encoding="utf-8"))
        pi_arm = json.loads((state_file / "arm-pi.json").read_text(encoding="utf-8"))
        md = render_markdown(chat_arm, pi_arm,
                             "http://localhost:8001", "http://localhost:8002")
        target = REPO / "docs" / "dev" / "pi-vs-chatengine-benchmark.md"
        if args.write:
            target.write_text(md, encoding="utf-8")
            print(f"written: {target}")
        else:
            print(md)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
