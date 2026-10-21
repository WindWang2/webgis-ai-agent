"""G05 TaskScenarioRunner —— 经真实生产缝驱动 task 场景。

执行面（与 tests/test_harness_kernel_e2e_scenarios.py 同模式，蓝本不 import）：
- 真实 ``dispatch_tool``（Pi dispatch 缝：webgis_execute 代理解包、
  ToolDispatchService、repeat-intercept、session plan 步骤证据）；
- 真实 harness kernel runtime（begin_turn / register_active_pi_turn /
  begin_step）；
- 真实 turn 结算入口 ``harness_kernel.settle.settle_turn``（E1..E6，
  含 maybe_finalize_map_product → goal_satisfaction 投影）；
- 真实 session_data_manager（ref 存取）与 MapSpecStore 产物。

不引入任何 LLM / Node Pi 子进程 / 网络：NL→tool-call 由场景的
scripted steps 代替（诚实边界见 contracts.py 模块 docstring）。
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent_pi_bridge import (
    PiToolRequest,
    _cleanup_turn_state,
    dispatch_tool,
    register_active_pi_turn,
    set_tool_registry,
    take_session_plan_sse,
    unregister_active_pi_turn,
)
from app.services.harness_kernel import get_runtime
from app.services.harness_kernel import settle as hk_settle
from app.services.session_data import session_data_manager
from app.tools import init_tools
from app.tools.registry import ToolRegistry

from . import fixtures_data
from .contracts import AgentStep, CheckResult, ScenarioContract, ScenarioRunResult, StepKind, TurnSpec

_STEP_RESULT_KEYS = ("step_result", "result", "data")


def build_registry() -> ToolRegistry:
    r = ToolRegistry()
    init_tools(r)
    set_tool_registry(r)
    return r


#: 进程级 registry 缓存：根 conftest 的 autouse 复位会在每个测试前把
#: ``agent_pi_bridge._tool_registry`` 清回 None（tests/conftest.py
#: _reset_chat_engine_singletons），因此 fixture 必须按测试重注入 —— 这里
#: 缓存构建产物（init_tools ~秒级），注入本身是 O(1)。
_CACHED_REGISTRY: "ToolRegistry | None" = None


def ensure_registry_injected() -> ToolRegistry:
    """构建（一次）并向 bridge 注入 registry；每测试调用以对抗 autouse 复位。"""
    global _CACHED_REGISTRY
    if _CACHED_REGISTRY is None:
        _CACHED_REGISTRY = ToolRegistry()
        init_tools(_CACHED_REGISTRY)
    set_tool_registry(_CACHED_REGISTRY)
    return _CACHED_REGISTRY


class _StepOutcome:
    __slots__ = ("call_id", "ok", "is_error", "payload", "text", "layer_ids")

    def __init__(self, call_id: str, ok: bool, is_error: bool,
                 payload: Any, text: str, layer_ids: List[str]):
        self.call_id = call_id
        self.ok = ok
        self.is_error = is_error
        self.payload = payload
        self.text = text
        self.layer_ids = layer_ids


class TaskScenarioRunner:
    """单场景执行器（每场景独立 session；进程级 registry 复用）。"""

    def __init__(self, registry: ToolRegistry, *, tmp_root: Optional[Path] = None):
        self._registry = registry
        # 栅格 fixture 必须落在 validate_data_path 的允许区（./tmp 或 ./data）
        # —— 缺省 cwd 下 ./tmp/g05-rasters（gitignored）。
        self._tmp_root = tmp_root or (Path.cwd() / "tmp" / "g05-rasters")

    async def run(self, scenario: ScenarioContract) -> ScenarioRunResult:
        started = time.monotonic()
        sid = f"sess-g05-{scenario.id}-{uuid.uuid4().hex[:8]}"
        await session_data_manager.clear_session(sid)
        refs: Dict[str, str] = {}
        step_outcomes: Dict[str, _StepOutcome] = {}
        auto_layers: List[str] = []
        error = ""
        settle_records: List[Dict[str, Any]] = []
        evidence: Optional[Dict[str, Any]] = None
        try:
            await asyncio.wait_for(
                self._run_turns(scenario, sid, refs, step_outcomes,
                                auto_layers, settle_records),
                timeout=scenario.timeout_s,
            )
            # 证据必须在 clear_session 之前收集（session 数据目录即证据源）
            from .projections import build_task_evidence
            evidence = await build_task_evidence(sid, scenario)
        except asyncio.TimeoutError:
            error = f"scenario timeout after {scenario.timeout_s}s"
        except Exception as exc:  # noqa: BLE001 — runner 级失败如实上报
            error = f"runner error: {type(exc).__name__}: {exc}"[:400]
        finally:
            try:
                await session_data_manager.clear_session(sid)
            except Exception:  # noqa: BLE001 — 清理失败不影响结果
                pass

        checks: List[CheckResult] = []
        if not error and evidence is not None:
            from .reporting import evaluate_expectations
            checks = evaluate_expectations(scenario, evidence)
        result = ScenarioRunResult(
            scenario_id=scenario.id, family=scenario.family,
            negative=scenario.negative, checks=checks, error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            session_id=sid, refs=dict(refs), settle_records=settle_records,
        )
        return result

    # ── turn 循环 ────────────────────────────────────────────────────────

    async def _run_turns(self, scenario, sid, refs, step_outcomes,
                         auto_layers, settle_records) -> None:
        rt = get_runtime(sid)
        first = True
        for turn in scenario.turns:
            if not first:
                _cleanup_turn_state(sid)   # 生产语义：新 turn 清理去重集
            first = False
            await rt.begin_turn(turn.turn_id, host="pi", message=turn.message)
            await register_active_pi_turn(sid, turn.turn_id)
            for step in turn.steps:
                outcome = await self._exec_step(
                    sid, turn, step, refs, step_outcomes)
                if not outcome.is_error and not step.expect_error:
                    auto_layers.extend(outcome.layer_ids)
            if turn.leave_unsettled:
                # 进程死亡/中断语义：turn 不结算（kernel 对账留给下一 turn）
                await unregister_active_pi_turn(sid, turn.turn_id)
                continue
            await unregister_active_pi_turn(sid, turn.turn_id)
            outcome = await hk_settle.settle_turn(hk_settle.HarnessSettleInput(
                session_id=sid, turn_id=turn.turn_id, host="pi",
                cause=turn.settle_cause, failure_class=turn.failure_class,
                failure_detail=turn.failure_detail[:200],
            ))
            settle_records.append({
                "turn_id": turn.turn_id,
                "kernel_status": outcome.kernel_status,
                "settle_class": outcome.settle_class,
                "disposition": outcome.disposition,
                "receipts": outcome.receipt_line(),
                "has_map_product_payload": outcome.map_product is not None,
            })

    # ── 单步执行（真实 dispatch 缝）────────────────────────────────────

    async def _exec_step(self, sid: str, turn: TurnSpec, step: AgentStep,
                         refs: Dict[str, str],
                         step_outcomes: Dict[str, _StepOutcome]) -> _StepOutcome:
        call_id = f"{turn.turn_id}::{step.call_id}"

        if step.kind == StepKind.STORE_DATA:
            return await self._store_dataset(sid, step, refs, call_id)

        name, arguments = self._resolve_call(step, turn, refs, step_outcomes)
        # 镜像生产 HTTP 路由（app/api/routes/pi_tools.py:70-71）：token 验证
        # 后写入 verifiedTurnId，dispatch_tool 据此绑定 turn 级 RuntimeContext
        # （requirement IR / 记忆等 session 绑定缝依赖该上下文）。
        req = PiToolRequest(
            toolCallId=call_id, name=name, arguments=arguments,
            sessionId=sid, verifiedTurnId=turn.turn_id,
        )
        res = await dispatch_tool(req)
        # 消费 step 级 SSE 缓存（生产 rendezvous；不取会滞留缓冲）
        try:
            take_session_plan_sse(call_id, sid)
        except Exception:  # noqa: BLE001
            pass
        is_error = bool(res.isError)
        payload = _extract_payload(res)
        outcome = _StepOutcome(
            call_id=step.call_id, ok=not is_error, is_error=is_error,
            payload=payload, text=_content_text(res),
            layer_ids=_extract_layer_ids(payload),
        )
        step_outcomes[step.call_id] = outcome
        # 行为层期望：expect_error 注入步必须真的失败（否则负例是假注入）
        if step.expect_error and not is_error:
            raise AssertionError(
                f"step {step.call_id} expected dispatch error but succeeded "
                f"(negative injection is not effective)")
        if not step.allow_error and not step.expect_error and is_error:
            raise AssertionError(
                f"step {step.call_id} dispatch failed unexpectedly: "
                f"{outcome.text[:200]}")
        return outcome

    async def _store_dataset(self, sid: str, step: AgentStep,
                             refs: Dict[str, str], call_id: str) -> _StepOutcome:
        kind = str(step.arguments.get("dataset_kind") or step.tool or "")
        name = str(step.arguments.get("name") or "")
        if not kind or not name:
            raise AssertionError(
                f"store_data step {step.call_id} requires arguments "
                "{name, dataset_kind}")
        if kind in fixtures_data.VECTOR_FACTORIES:
            payload = fixtures_data.VECTOR_FACTORIES[kind]()
            ref = await session_data_manager.store(sid, payload, prefix="geojson")
        elif kind in fixtures_data.RASTER_FACTORIES:
            if self._tmp_root is None:
                raise AssertionError("raster dataset requires runner tmp_root")
            _, rel = fixtures_data.make_raster(kind, self._tmp_root)
            ref = rel
        else:
            raise AssertionError(f"unknown dataset_kind {kind}")
        refs[name] = ref
        outcome = _StepOutcome(
            call_id=step.call_id, ok=True, is_error=False,
            payload={"ref": ref}, text=ref, layer_ids=[],
        )
        return outcome

    # ── 工具名与参数解析（模板展开）────────────────────────────────────

    def _resolve_call(self, step: AgentStep, turn: TurnSpec,
                      refs: Dict[str, str],
                      step_outcomes: Dict[str, _StepOutcome]):
        args = _expand_templates(step.arguments, refs, step_outcomes)
        if step.kind == StepKind.INTENT:
            return "webgis_map_intent", {"query": step.query or turn.message,
                                         **args}
        if step.kind == StepKind.EXECUTE:
            tool = step.tool
            if not tool:
                raise AssertionError(f"execute step {step.call_id} missing tool")
            return "webgis_execute", {"toolName": tool, "arguments": args}
        if step.kind == StepKind.PRODUCT:
            return "webgis_map_product", {"query": step.query or turn.message,
                                          **args}
        if step.kind == StepKind.COMPONENT_UPDATE:
            return "webgis_component_update", dict(args)
        if step.kind == StepKind.PRODUCT_EDIT:
            return "webgis_product_edit", dict(args)
        if step.kind == StepKind.RAW:
            if not step.tool:
                raise AssertionError(f"raw step {step.call_id} missing tool")
            return step.tool, dict(args)
        raise AssertionError(f"unsupported step kind {step.kind}")


    # ── 工具名与参数解析（模板展开）────────────────────────────────────
_REF_TOKEN = re.compile(r"\{\{ref:([A-Za-z0-9_\-]+)\}\}")
_STEP_TOKEN = re.compile(r"\{\{step:([A-Za-z0-9_\-]+):([A-Za-z0-9_.\[\]]+)\}\}")


def _expand_templates(value: Any, refs: Dict[str, str],
                      step_outcomes: Dict[str, _StepOutcome]) -> Any:
    if isinstance(value, str):
        m = _REF_TOKEN.fullmatch(value)
        if m:
            name = m.group(1)
            if name not in refs:
                raise AssertionError(f"template ref '{name}' not stored yet")
            return refs[name]
        m = _STEP_TOKEN.fullmatch(value)
        if m:
            outcome = step_outcomes.get(m.group(1))
            if outcome is None:
                raise AssertionError(f"template step '{m.group(1)}' not run yet")
            return _dig(outcome.payload, m.group(2))
        # 局部模板（字符串内嵌）—— 整串替换保持简单可控
        out = value
        for name, ref in refs.items():
            out = out.replace("{{ref:%s}}" % name, ref)
        return out
    if isinstance(value, dict):
        return {k: _expand_templates(v, refs, step_outcomes)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_templates(v, refs, step_outcomes) for v in value]
    return value


def _dig(payload: Any, path: str) -> Any:
    cur = payload
    for seg in re.findall(r"[^.@\[\]]+|\[\d+\]", path):
        if seg.startswith("[") and seg.endswith("]"):
            cur = cur[int(seg[1:-1])]
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            cur = getattr(cur, seg, None)
    return cur


def _extract_payload(res) -> Any:
    """PiToolResponse → dict 载荷（content[0].text 为 JSON 时解析）。"""
    import json as _json
    try:
        for block in (res.content or []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                try:
                    return _json.loads(text)
                except (ValueError, TypeError):
                    return {"_text": text}
        return {}
    except Exception:  # noqa: BLE001
        return {}


def _content_text(res) -> str:
    try:
        parts = [str(b.get("text") or "") for b in (res.content or [])
                 if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _extract_layer_ids(payload: Any) -> List[str]:
    """从工具结果提取落地图层 id（product 补层 / layer 工具的输出键）。"""
    ids: List[str] = []
    if not isinstance(payload, dict):
        return ids
    for key in ("layer_id", "layerId"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            ids.append(v)
    for row in payload.get("layers", []) or []:
        if isinstance(row, dict):
            v = row.get("layer_id") or row.get("layerId") or row.get("id")
            if isinstance(v, str) and v and not row.get("role"):
                ids.append(v)
    return ids
