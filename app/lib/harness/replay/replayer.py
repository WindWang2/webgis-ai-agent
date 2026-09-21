"""离线确定性重放器（B3，ADR-0183 决策三 / D6）。

**决策重放，而非 LLM 重生成**：scenario/录制 trace 提供冻结的工具调用
与收据（canned dispatch-shaped results），重放器在进程内重执行确定性
下游，分层比对语义输出：

- **T1 证据级（全场景）**：冻结 ops → 真实 ``PiAgentHarness`` 记录 →
  真实 ``evaluate_with_evidence`` + ``HarnessEvaluator.evaluate_evidence``
  → L5 ``derive_goal_satisfaction``；``expect`` 白名单 exact 比对；
- **T2 变异级（带 mutations 的场景）**：op 序列经真实 ``mapspec_store``
  门面 → ``MapSpecLifecycleEngine.apply_mutation``，比对结果 spec 的
  内容指纹（确定性核心的回归牙齿）；
- **T3 bind-gate 级（dispatch_backed + tool_registry 的场景，ADR-0212
  决策五）**：逐 op 过生产同函数 ``check_tool_capability_at_dispatch``，
  比对 allow/deny + alternatives；receipt 级经 ToolDispatchService 重发
  在离线约束下不做（``deferred_levels`` 诚实披露）。

比对三分类（B3）：``exact``（白名单字段相等）/ ``tolerant``（数值走
ratchet 行）/ ``nondeterministic_text``（LLM 文本只验存在性+长度带）。
无浏览器 / 无 LLM / 无网络（offline guard 直接生效）。
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.harness.evidence import (
    CartographicReviewEvidence,
    RefResolution,
    RefResolutionStatus,
)
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.replay.determinism import seeded_id, sha256_of
from app.lib.harness.replay.metrics import project_metrics
from app.lib.harness.visual_evaluator import derive_goal_satisfaction


# ── 场景数据模型（语料 JSON 的直接映射，versioned additive-only）──────────────

SCENARIO_SCHEMA_VERSION = 1


@dataclass
class ScenarioOp:
    """一个冻结工具步骤：编排的调用 + canned 收据。"""

    call_id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_msg: str = ""
    duration_ms: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioOp":
        return cls(
            call_id=str(data.get("call_id") or ""),
            tool=str(data.get("tool") or ""),
            arguments=dict(data.get("arguments") or {}),
            result=dict(data.get("result") or {}),
            is_error=bool(data.get("is_error")),
            error_msg=str(data.get("error_msg") or ""),
            duration_ms=float(data.get("duration_ms") or 0.0),
        )


@dataclass
class TurnSpec:
    """一个 turn（multi-turn 场景的步骤）：同 session 顺序执行。"""

    user_input: str = ""
    ops: List[ScenarioOp] = field(default_factory=list)
    mutations: List[Dict[str, Any]] = field(default_factory=list)  # T2 op 序列
    visual_report: Optional[Dict[str, Any]] = None   # L5 视觉摘要 fixture
    cartography: Optional[Dict[str, Any]] = None     # L4 审查证据 fixture
    refs: Dict[str, Any] = field(default_factory=dict)  # ref → fixture payload
    expect: Dict[str, Any] = field(default_factory=dict)  # exact 比对白名单

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TurnSpec":
        return cls(
            user_input=str(data.get("user_input") or ""),
            ops=[ScenarioOp.from_dict(op) for op in data.get("ops") or []],
            mutations=list(data.get("mutations") or []),
            visual_report=data.get("visual_report"),
            cartography=data.get("cartography"),
            refs=dict(data.get("refs") or {}),
            expect=dict(data.get("expect") or {}),
        )


@dataclass
class Scenario:
    scenario_id: str
    category: str
    turns: List[TurnSpec] = field(default_factory=list)
    description: str = ""
    schema_version: int = SCENARIO_SCHEMA_VERSION
    dispatch_backed: bool = False    # T3：bind-gate 重放（见 replay_scenario）
    faults: List[Dict[str, Any]] = field(default_factory=list)  # M5 编译进环境
    tags: List[str] = field(default_factory=list)
    # ── ADR-0212 additive ──────────────────────────────────────────────
    # 录制 roundtrip 场景携带的决策索引 + 录制时 registry 指纹
    # （bench 重放期做 registry drift 检测 + 决策重推导比对）。
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    registry_digest: str = ""
    # T3 bind-gate fixture：tool → capability 声明（缺席 + dispatch_backed
    # → not_run 诚实标注）。
    tool_registry: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scenario":
        return cls(
            scenario_id=str(data.get("scenario_id") or ""),
            category=str(data.get("category") or ""),
            description=str(data.get("description") or ""),
            turns=[TurnSpec.from_dict(t) for t in data.get("turns") or []],
            schema_version=int(data.get("schema_version") or SCENARIO_SCHEMA_VERSION),
            dispatch_backed=bool(data.get("dispatch_backed")),
            faults=list(data.get("faults") or []),
            tags=[str(t) for t in data.get("tags") or []],
            decisions=[
                d for d in (data.get("decisions") or [])
                if isinstance(d, dict)
            ],
            registry_digest=str(data.get("registry_digest") or ""),
            tool_registry={
                str(tool): [str(c) for c in caps]
                for tool, caps in (data.get("tool_registry") or {}).items()
                if isinstance(caps, list)
            } if isinstance(data.get("tool_registry"), dict) else {},
        )


# ── fixture 注入缝（与生产 ref_resolver / cartography_state_reader 同签名）────


def make_ref_resolver(refs: Dict[str, Any]):
    """场景级 ref 解析器：fixture 命中 → RESOLVED，未命中 → NOT_FOUND。"""

    async def _resolve(session_id: str, ref_cursor: str):
        if refs.get(ref_cursor) is None:
            return RefResolution(
                ref=ref_cursor, session_id=session_id,
                status=RefResolutionStatus.NOT_FOUND,
                detail="fixture ref miss",
            )
        ref_type = ref_cursor.split(":")[1] if ref_cursor.count(":") >= 1 else None
        return RefResolution(
            ref=ref_cursor, session_id=session_id,
            status=RefResolutionStatus.RESOLVED,
            expected_type=ref_type, actual_type=ref_type,
        )

    return _resolve


def make_cartography_reader(turn: TurnSpec):
    """生产 reader 契约的 fixture 版：返回 **session 状态 dict**
    （``{session_id, mapspec, map_state}``），harness 自行重算确定性评审
    （``evaluate_cartography_semantics``）与指纹收敛。fixture 缺席 →
    无 mapspec 状态（CartographicQuality 走 fail-closed 诚实语义）。
    """
    spec_fixture = turn.cartography if isinstance(turn.cartography, dict) else {}
    fixture_mapspec = spec_fixture.get("mapspec")

    async def _reader(session_id: str):
        return {
            "session_id": session_id,
            "mapspec": fixture_mapspec,
            "map_state": dict(spec_fixture.get("map_state") or {}),
        }

    return _reader


# ── 比对（exact / text 两类；tolerant 走 metrics+ratchet）────────────────────


def compare_exact(expected: Any, actual: Any, path: str = "") -> List[Dict[str, Any]]:
    """白名单精确比对：以 expect 树为骨架，实际值逐叶比对。返回 diff 列表。"""
    diffs: List[Dict[str, Any]] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"path": path or "$", "expected": "<dict>",
                     "actual": type(actual).__name__, "diff_class": "exact"}]
        for key, sub in expected.items():
            diffs.extend(compare_exact(sub, actual.get(key),
                                       f"{path}.{key}" if path else key))
        return diffs
    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append({"path": path or "$", "expected": "<list>",
                          "actual": type(actual).__name__, "diff_class": "exact"})
            return diffs
        for i, sub in enumerate(expected):
            diffs.extend(compare_exact(
                sub, actual[i] if i < len(actual) else None, f"{path}[{i}]"))
        if len(actual) > len(expected):
            # 实际多出的条目不可见即假绿 —— 必须显式暴露。
            diffs.append({"path": path or "$", "expected": len(expected),
                          "actual": len(actual), "diff_class": "exact",
                          "reason": "extra_actual_items"})
        return diffs
    if expected != actual:
        diffs.append({"path": path or "$", "expected": expected,
                      "actual": actual, "diff_class": "exact"})
    return diffs


def len_bucket(text: str) -> str:
    length = len(text or "")
    if length == 0:
        return "0"
    if length <= 64:
        return "xs"
    if length <= 512:
        return "s"
    if length <= 4096:
        return "m"
    return "l"


# ── T2：MapSpec 变异重放（真实 lifecycle engine）─────────────────────────────

_MUTATION_OPS = (
    "init_project", "set_view", "source_profile", "layer_upsert",
    "layer_remove", "set_basemap", "layout_set", "patch_component",
    "remove_component", "duplicate_component", "rebind_component",
    "checkpoint", "rollback",
)


def fingerprint_of(mapspec: Optional[Dict[str, Any]]) -> Optional[str]:
    """生产同源指纹（``cartographic_fingerprint``）—— 占位符解析专用。

    与 harness 收敛检查用的必须是同一函数，否则占位符指纹永远不收敛。
    """
    if not isinstance(mapspec, dict) or not mapspec:
        return None
    from app.lib.cartography.quality_loop import cartographic_fingerprint

    return cartographic_fingerprint(mapspec)


#: session 限定别名（source_profile 按 session 哈希铸造 ref 别名）——
#: 重放级归一化指纹剥除它们，使跨 run / 跨 session 的 T2 比对成立。
_SESSION_SCOPED_SOURCE_KEYS = ("ref", "ref_id", "data_fingerprint")


def normalized_fingerprint(mapspec: Optional[Dict[str, Any]]) -> Optional[str]:
    """重放归一化指纹：剥 session 限定别名后的 sha256（session 无关）。"""
    if not isinstance(mapspec, dict) or not mapspec:
        return None

    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: ("<alias>" if k in _SESSION_SCOPED_SOURCE_KEYS else _strip(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_strip(v) for v in value]
        return value

    return sha256_of(_strip(mapspec))


class _sandboxed_mutation_store:
    """把 lifecycle 存储指到一次性目录（重放期间），退出恢复。

    MapSpecStore 的路径在每次调用时读模块级 ``BASE_STORAGE_DIR``，
    属性交换即全局生效；单进程顺序重放下无竞争。
    """

    def __enter__(self):
        import tempfile
        from pathlib import Path

        from app.services.mapspec import store as store_module

        self._module = store_module
        self._saved = store_module.BASE_STORAGE_DIR
        self._tmp = tempfile.mkdtemp(prefix="r10-replay-muts-")
        store_module.BASE_STORAGE_DIR = Path(self._tmp)
        return self

    def __exit__(self, *exc):
        self._module.BASE_STORAGE_DIR = self._saved
        # 沙箱目录用完即清：只还原不删除会让每次 replay_mutations 泄漏一个
        # 含全部写入 mutation 的 /tmp 目录（CI/长驻进程无界累积）。
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)
        return False


async def replay_mutations(
    session_id: str, mutations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """op 序列 → 真实 lifecycle engine（沙箱存储）；产出确定性证据。"""
    from app.services.mapspec_store import mapspec_store

    outcomes: List[Dict[str, Any]] = []
    _ = mapspec_store  # 实际调用统一发生在下方沙箱 CM 内
    with _sandboxed_mutation_store():
      for mut in mutations:
        op = str(mut.get("op") or "")
        args = dict(mut.get("args") or {})
        if op not in _MUTATION_OPS:
            outcomes.append({"op": op, "unknown_op": True})
            continue
        method = getattr(mapspec_store, op)
        result = await method(session_id, **args)
        mapspec = result.get("mapspec")
        # facade 返回形态不一（mutation 结果带 success；profile 类返回
        # 载荷本身）—— 缺省按「有载荷且无 is_error」诚实归为成功。
        success = result.get("success")
        if success is None:
            success = bool(result) and not result.get("is_error")
        outcomes.append({
            "op": op,
            "success": bool(success),
            "is_error": bool(result.get("is_error")),
            "error_code": result.get("error_code"),
            "is_compiled": bool(result.get("is_compiled")),
            "spec_fingerprint": normalized_fingerprint(mapspec),
            "layer_count": (
                len(mapspec.get("layers") or [])
                if isinstance(mapspec, dict) else None
            ),
            "finding_count": len(result.get("cartography_findings") or []),
            "superseded": bool(result.get("superseded")),
        })
    return outcomes


# ── 重放器 ───────────────────────────────────────────────────────────────────


#: canned 结果中的指纹占位符：重放时替换为 fixture mapspec 的规范指纹
#: （场景作者无需硬编码哈希，指纹收敛自动成立）。
FINGERPRINT_PLACEHOLDER = "$cartographic_fingerprint"
#: cartography fixture 中的 session 占位符（observation 归属断言用）。
SESSION_PLACEHOLDER = "$session_id"


def _resolve_fixture_placeholders(turn: TurnSpec, session_id: str) -> None:
    """深度替换 cartography fixture（mapspec + map_state）与 op 结果中的
    占位符 —— 场景作者不需要预知指纹哈希。"""
    fixture = turn.cartography if isinstance(turn.cartography, dict) else {}
    fixture_mapspec = fixture.get("mapspec")
    target = fingerprint_of(fixture_mapspec) if isinstance(fixture_mapspec, dict) else None

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if value == FINGERPRINT_PLACEHOLDER and target:
            return target
        if value == SESSION_PLACEHOLDER:
            return session_id
        return value

    for key in ("mapspec", "map_state"):
        if key in fixture:
            fixture[key] = _walk(fixture[key])
    for op in turn.ops:
        if target and op.result.get("mapspec_fingerprint") == FINGERPRINT_PLACEHOLDER:
            op.result["mapspec_fingerprint"] = target


#: 离线 judge 注入（生产 ``CARTO_VISUAL_JUDGE`` env 缝的 fixture 侧）：
#: 确定性 judge + 注入截图 —— 语料声明 ``visual_judge: true`` 的 turn 生效。
_OFFLINE_JUDGE_SPEC = "tests.harness_replay.replay_judge:deterministic_judge"
_OFFLINE_SCREENSHOT_ENV = "CARTO_VISUAL_JUDGE_SCREENSHOT"
_SCREENSHOT_FIXTURE_DEFAULT = "tests/fixtures/replay/map.png"


class _offline_judge_env:
    """进程级 env 注入的作用域包装（bench 顺序执行，无并发 env 竞争）。"""

    def __enter__(self):
        import os

        self._saved = (
            os.environ.get("CARTO_VISUAL_JUDGE"),
            os.environ.get(_OFFLINE_SCREENSHOT_ENV),
        )
        os.environ["CARTO_VISUAL_JUDGE"] = _OFFLINE_JUDGE_SPEC
        os.environ[_OFFLINE_SCREENSHOT_ENV] = _SCREENSHOT_FIXTURE_DEFAULT
        return self

    def __exit__(self, *exc):
        import os

        judge, screenshot = self._saved
        if judge is None:
            os.environ.pop("CARTO_VISUAL_JUDGE", None)
        else:
            os.environ["CARTO_VISUAL_JUDGE"] = judge
        if screenshot is None:
            os.environ.pop(_OFFLINE_SCREENSHOT_ENV, None)
        else:
            os.environ[_OFFLINE_SCREENSHOT_ENV] = screenshot
        return False


@dataclass
class TurnReplayResult:
    turn_index: int
    gate_result: Dict[str, Any] = field(default_factory=dict)
    goal_satisfaction: Dict[str, Any] = field(default_factory=dict)
    mutation_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    exact_diffs: List[Dict[str, Any]] = field(default_factory=list)
    text_diffs: List[Dict[str, Any]] = field(default_factory=list)
    evidence_count: int = 0
    #: T3 bind-gate 重放条目（dispatch_backed + tool_registry 提供时）。
    dispatch_decisions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # nondeterministic_text 永不作为语义失败（B3/B11）——只进报告。
        return not self.exact_diffs


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    ok: bool
    turns: List[TurnReplayResult] = field(default_factory=list)
    replay_digest: str = ""
    levels_run: List[str] = field(default_factory=list)
    not_run: List[str] = field(default_factory=list)
    metrics_rows: List[Dict[str, Any]] = field(default_factory=list)
    #: ADR-0212：声明面里**系统级未实现**的层（不毒化 ok —— 与作者声明
    #: 了却跑不了的 not_run 区分；receipt 级经 ToolDispatchService 重发
    #: 在离线约束下不做，诚实披露）。
    deferred_levels: List[str] = field(default_factory=list)


class OfflineReplayer:
    """进程内离线重放器（无 Node / 无 LLM / 无网络）。

    T2 变异重放隔离：``run_token``（缺省每实例随机）参与 mutation session
    id 派生，且 lifecycle 存储在重放期间被交换到一次性沙箱目录 ——
    同 seed 跨 run 指纹稳定、不污染生产 MAPSPEC_STORAGE_DIR。
    """

    def __init__(self, *, seed: int = 0,
                 evaluator: Optional[HarnessEvaluator] = None,
                 run_token: Optional[str] = None):
        import uuid

        self.seed = seed
        self.run_token = run_token or uuid.uuid4().hex[:12]
        self.evaluator = evaluator or HarnessEvaluator()

    def session_for(self, scenario_id: str) -> str:
        """同场景多 turn 共享 session（情境持续性验证的载体）。"""
        return seeded_id("rsess", self.seed, scenario_id)

    def mutation_session_for(self, scenario_id: str) -> str:
        """T2 的隔离 session（run_token 参与 → 跨 run 不撞会话状态）。"""
        return seeded_id("rmut", self.seed, self.run_token, scenario_id)

    async def replay_scenario(self, scenario: Scenario) -> ScenarioResult:
        session_id = self.session_for(scenario.scenario_id)
        # T1：全 turn 共享一个 harness —— 证据跨 turn 累积（会话语义）。
        harness = PiAgentHarness(session_id=session_id)
        turn_results: List[TurnReplayResult] = []
        t2_used = False
        t3_used = False

        for index, turn in enumerate(scenario.turns):
            result = await self._replay_turn(
                scenario, turn, index=index, session_id=session_id,
                harness=harness,
            )
            if turn.mutations:
                t2_used = True
            if result.dispatch_decisions:
                t3_used = True
            turn_results.append(result)

        # ADR-0212 决策五：T3 = capability bind gate 重放（生产同函数
        # check_tool_capability_at_dispatch）。receipt 级经
        # ToolDispatchService 重发在离线约束下不做 → deferred 诚实披露。
        not_run = ["t3_bind"] if (scenario.dispatch_backed and not t3_used) else []
        deferred = ["receipt_redispatch"] if scenario.dispatch_backed else []
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            ok=all(r.ok for r in turn_results) and not not_run,
            turns=turn_results,
            replay_digest=self._digest(turn_results),
            levels_run=["t1"] + (["t2"] if t2_used else [])
            + (["t3_bind"] if t3_used else []),
            not_run=not_run,
            deferred_levels=deferred,
            metrics_rows=self._metric_rows(scenario, turn_results),
        )

    @staticmethod
    def _fixture_tool_registry(tool_registry: Dict[str, List[str]]):
        """bind gate 的最小 registry stub（metadata(name) → capabilities）。"""

        class _StubRegistry:
            def __init__(self, mapping: Dict[str, List[str]]):
                self._mapping = mapping

            def metadata(self, name: str) -> Dict[str, Any]:
                return {"capabilities": list(self._mapping.get(name) or [])}

        return _StubRegistry(tool_registry)

    def _dispatch_gate_entries(
        self, scenario: Scenario, turn: TurnSpec, session_id: str,
    ) -> List[Dict[str, Any]]:
        """turn 内逐 op 重放 dispatch bind gate（生产同函数，离线纯查询）。"""
        if not scenario.tool_registry:
            return []
        try:
            from app.services.gis_harness.hotpath_convergence.capability_bind import (
                check_tool_capability_at_dispatch,
            )
        except Exception:  # noqa: BLE001 — bind 面缺席 → 不伪造决策
            return []
        registry = self._fixture_tool_registry(scenario.tool_registry)
        entries: List[Dict[str, Any]] = []
        for op in turn.ops:
            if not op.tool:
                continue
            decision = check_tool_capability_at_dispatch(
                op.tool, registry=registry, session_id=session_id,
            )
            if decision is None:
                entries.append({"call_id": op.call_id, "tool": op.tool,
                                "allowed": True})
            else:
                entries.append({
                    "call_id": op.call_id,
                    "tool": op.tool,
                    "allowed": bool(decision.allowed),
                    "capability": str(decision.capability_id)[:96],
                    "reason": str(decision.reason)[:96],
                    "alternatives": [
                        str(a.get("id") or "")[:96]
                        for a in (decision.alternatives or [])[:4]
                        if isinstance(a, dict)
                    ],
                })
        return entries

    async def _replay_turn(
        self, scenario: Scenario, turn: TurnSpec, *, index: int,
        session_id: str, harness: PiAgentHarness,
    ) -> TurnReplayResult:
        run_id = seeded_id("rrun", self.seed, scenario.scenario_id, index)
        turn_id = seeded_id("rturn", self.seed, scenario.scenario_id, index)
        harness.set_correlation(run_id=run_id, turn_id=turn_id)
        # 生产缝是构造器注入；重放按 turn 重绑（multi-turn fixture 独立）。
        harness.ref_resolver = make_ref_resolver(turn.refs)
        _resolve_fixture_placeholders(turn, session_id)
        harness.cartography_state_reader = make_cartography_reader(turn)

        for op in turn.ops:
            harness.record_tool_call(op.call_id, op.tool, op.arguments)
            harness.record_tool_result(
                op.call_id, op.tool, op.result,
                is_error=op.is_error, error_msg=op.error_msg or None,
            )

        # 语料声明 visual_judge 的 turn：经生产 env 缝注入确定性 judge +
        # fixture 截图（未声明 → judge 缺席，L5 诚实 not_evaluated）。
        use_visual_judge = bool(
            isinstance(turn.cartography, dict) and turn.cartography.get("visual_judge")
        )
        with (_offline_judge_env() if use_visual_judge else contextlib.nullcontext()):
            evidence_result = await harness.evaluate_with_evidence()
        gate_result = self.evaluator.evaluate_evidence(
            evidence_result, require_evaluated=True,
        )
        # L5 目标满足推导：attach_visual_judgement 在 evaluate 内已按 env
        # 缝运行（fixture 未注入 → visual_judge_disabled 的诚实缺席）。
        projection = evidence_result.get("cartography") or {}
        cartography_obj = CartographicReviewEvidence(
            session_id=str(projection.get("session_id") or session_id),
            status=str(projection.get("status") or "not_evaluated"),
            desired_status=str(projection.get("desired_status") or "not_evaluated"),
            trusted=bool(projection.get("trusted")),
            checks=[c for c in projection.get("checks") or [] if isinstance(c, dict)],
            visual_evidence=[
                v for v in projection.get("visual_evidence") or []
                if isinstance(v, dict)
            ],
            termination_reason=str(projection.get("termination_reason") or ""),
        )
        goal = derive_goal_satisfaction(cartography_obj)

        mutation_outcomes: List[Dict[str, Any]] = []
        if turn.mutations:
            mutation_outcomes = await replay_mutations(
                self.mutation_session_for(scenario.scenario_id), turn.mutations,
            )

        # T3 bind-gate 重放（ADR-0212 决策五）：dispatch_backed 场景逐 op
        # 过生产 check_tool_capability_at_dispatch，allow/deny + alternatives
        # 可被 expect["dispatch"] 白名单钉住。
        dispatch_entries: List[Dict[str, Any]] = []
        if scenario.dispatch_backed:
            dispatch_entries = self._dispatch_gate_entries(
                scenario, turn, session_id,
            )

        # exact 比对：expect 白名单（gate / goal / mutations / dispatch）。
        # user_text 由 nondeterministic_text 专项处理（不进 exact 树）。
        actual = {
            "gate": {
                "overall_passed": gate_result.get("overall_passed"),
                "checks": {
                    name: {
                        "passed": check.get("passed"),
                        "evaluated": check.get("evaluated"),
                        "reason": check.get("reason"),
                    }
                    for name, check in (gate_result.get("checks") or {}).items()
                },
            },
            "goal": goal,
            "mutations": mutation_outcomes,
        }
        if scenario.dispatch_backed:
            actual["dispatch"] = {
                entry["call_id"]: {
                    "allowed": entry["allowed"],
                    **({"capability": entry["capability"]}
                       if entry.get("capability") else {}),
                }
                for entry in dispatch_entries
            }
        semantic_expect = {k: v for k, v in turn.expect.items()
                           if k != "user_text"}
        exact_diffs = compare_exact(semantic_expect, actual) if semantic_expect else []

        # nondeterministic_text：LLM 文本只验存在性 + 长度带。
        text_expect = turn.expect.get("user_text")
        text_diffs: List[Dict[str, Any]] = []
        if isinstance(text_expect, dict):
            actual_bucket = len_bucket(turn.user_input)
            expect_present = bool(text_expect.get("present", True))
            actual_present = bool(turn.user_input)
            if expect_present != actual_present:
                text_diffs.append({"path": "text:user_input",
                                   "expected": expect_present,
                                   "actual": actual_present,
                                   "diff_class": "nondeterministic_text"})
            elif expect_present and text_expect.get("len_bucket") \
                    and text_expect["len_bucket"] != actual_bucket:
                text_diffs.append({"path": "text:user_input",
                                   "expected": text_expect["len_bucket"],
                                   "actual": actual_bucket,
                                   "diff_class": "nondeterministic_text"})

        return TurnReplayResult(
            turn_index=index,
            gate_result=gate_result,
            goal_satisfaction=goal,
            mutation_outcomes=mutation_outcomes,
            exact_diffs=exact_diffs,
            text_diffs=text_diffs,
            evidence_count=len(evidence_result.get("evidence") or []),
            dispatch_decisions=dispatch_entries,
        )

    def _digest(self, turn_results: List[TurnReplayResult]) -> str:
        """重放行为摘要：gate 裁决 + score + 指纹 + 目标态。

        score 是 canned 数据的纯函数（无计时抖动），是确定性回归的主信号；
        计时/token 用量不进 digest（走 tolerant ratchet 行）。
        """
        projection = [
            {
                "gate_passed": r.gate_result.get("overall_passed"),
                "checks": {
                    name: {
                        "score": c.get("score"),
                        "passed": c.get("passed"),
                        "evaluated": c.get("evaluated"),
                    }
                    for name, c in (r.gate_result.get("checks") or {}).items()
                },
                "goal": r.goal_satisfaction,
                "mutations": [
                    {k: v for k, v in m.items() if k != "error_msg"}
                    for m in r.mutation_outcomes
                ],
                # T3 bind-gate 裁决入 digest（决策面漂移进基线比对信号）。
                "dispatch": [
                    {k: entry.get(k) for k in ("call_id", "allowed", "capability")
                     if entry.get(k) is not None}
                    for entry in r.dispatch_decisions
                ],
            }
            for r in turn_results
        ]
        return sha256_of(projection)

    def _metric_rows(
        self, scenario: Scenario, turn_results: List[TurnReplayResult],
    ) -> List[Dict[str, Any]]:
        """重放结论 → tolerant ratchet 观测行（turn 粒度）。"""
        rows: List[Dict[str, Any]] = []
        for r in turn_results:
            trace_like: Dict[str, Any] = {
                "verdict": {"map_product": {}},
                "tool_calls": [
                    {"is_error": bool(op.is_error), "arg_bytes": 0}
                    for op in scenario.turns[r.turn_index].ops
                ],
            }
            rows.extend(project_metrics(
                trace_like,
                scene_id=f"{scenario.scenario_id}#t{r.turn_index}",
                gate_result=r.gate_result,
                goal_satisfaction=r.goal_satisfaction,
            ))
        return rows
