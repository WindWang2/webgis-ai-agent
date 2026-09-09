"""统一执行器（ADR-0096 D2/D5；ADR-0101 D3/D4/D5）：就绪集调度、预算准入、
复用校验、取消、deadline、分类重试。

协调者而非第二真相：
- 重活穿透既有 durable-job 运行时（``durable_job`` 策略）；
- 取消复用 ``CancellationToken``（lib 叶子原语 + checkpoint 协作点）；
- 就绪集（indegree 驱动）调度取代硬波次屏障 —— 独立分支不再被无关慢波
  阻塞；并发受 ``max_workers`` 与 governor concurrency 槽位双重约束；
- 节点结果按语义指纹复用；复用命中时验证缓存条目的上游输出指纹
  （checkpoint 校验）—— 上游内容变化 → 陈旧拒绝 + 诚实证据；
- 失败分类（errors.FailureClass），只对显式安全类别按 ``RetryPolicy``
  有界退避重试，deadline 感知拒绝对来不及完成的重试；
- 后代失效：上游失败/取消 → 后代 skipped；指纹变化 → invalidation_set。

进程内模式是第一公民：同步执行核心，REST 层用 to_thread 卸载（与
project workflow 路由同一模式），绝不阻塞事件循环。
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)
from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.services.geocompute import graph, ops, tracing
from app.services.geocompute.errors import (
    BudgetExceededError,
    FailureClass,
    GeoComputeError,
    NodeExecutionError,
    RETRYABLE_FAILURE_CLASSES,
    EXTENDED_RETRYABLE_FAILURE_CLASSES,
    classify_failure,
)
from app.services.geocompute.normalization import canonical_dumps
from app.services.geocompute.plan import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionRun,
    ExecutionRunStatus,
    NodeEvidence,
    NodeReusePolicy,
)

#: 计划级最大并行度：独立于工具注册表信号量，小而有界（防线程池饥饿）。
DEFAULT_MAX_WORKERS = 2

#: 错误证据的字符上界（评审 MINOR：error_message 不承载无限文本）。
_MAX_ERROR_MESSAGE_CHARS = 300

#: 绝对路径段（≥2 段，如 /home/kevin/projects/…）→ "<path>"：错误证据不得
#: 泄漏执行主机的目录拓扑；类型化 error_code 不受影响。
_ABSOLUTE_PATH_RE = re.compile(r"(?:/[^/\s]+){2,}")

#: 复用存储条目的元数据键（不与载荷键冲突：载荷键均为合法标识符）。
_SIZE_KEY = "__size__"
_NODE_FP_KEY = "__node_fp__"
_OUT_FP_KEY = "__out_fp__"
_UPSTREAM_KEY = "__upstream_fps__"

#: checkpoint 指纹采样参数（输出指纹：count + 首尾样本；有界且确定性）。
_FP_SAMPLE_HEAD = 16
_FP_SAMPLE_TAIL = 4


# ── Wave 8（audit 07-resource-governance-gaps.md §6.2 steps 1/6）──────────

#: R7（ResourceClass 接线）：并发槽位按资源类别加权 —— memory ≥ 4 或
#: cpu ≥ 5 的重节点占 2 个槽位单位，其余 1。权重经由**祖先作用域**的
#: 并发预算产生可观察的调度效果（session/tenant 槽位被重节点按 2×
#: 消耗 → 跨 run 加权背压：一个原先容纳 4 个并发 run 的 session 只能
#: 容纳 2 个重节点 run）。EXECUTION 作用域自身的并发上界因此以「单位」
#: 计 = ``_HEAVY_SLOT_UNITS × max_workers``（``execute_plan``）—— 这保证
#: 单个重节点永不因自身权重被永久拒绝（max_workers=1 时 2 ≤ 2）。确定性
#: 映射、无随机；同 run 内池上限（max_workers 线程）仍是不变的第一约束。
_HEAVY_SLOT_MEMORY = 4
_HEAVY_SLOT_CPU = 5
_HEAVY_SLOT_UNITS = 2

#: R9（槽位租约看门狗）：节点超过其 deadline + 该宽限仍占着并发槽位
#: （线程不可强杀的非协作节点）→ 强制归还槽位，后续 run 不再被饿死；
#: 节点真正落定时按 ``lease_reclaimed`` 跳过二次释放（钳零语义仍是兜底）。
DEFAULT_SLOT_LEASE_GRACE_S = 60.0

#: R9：看门狗巡检间隔 —— ``wait()`` 以它为上界周期性唤醒。否则长 plan
#: deadline 的 run 会在唯一僵尸节点上一直阻塞到 run deadline 才巡检，
#: 节点级租约过期形同虚设。代价：每 run 至多 1 次/秒的空轮询。
_LEASE_SWEEP_INTERVAL_S = 1.0


class _RunChargeLedger:
    """单 run 的 governor 实际记账累计（线程安全；R1 gauge 归还的依据）。

    节点线程并发完成 → ``_governor_charge`` 沿链 charge 的同时在此累计
    本 run 的贡献（小锁保护的标量三元组 —— 确定性与竞免兼备）；
    ``execute_plan`` 收尾把它与计划级预留估计合并，一次性
    ``governor.release`` 全额归还 —— 长寿命祖先作用域（global/tenant/
    project/session）精确回到 run 前基本线。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows = 0
        self._bytes = 0
        self._nodes = 0

    def add(self, rows: int, bytes_: int, nodes: int) -> None:
        with self._lock:
            self._rows += rows
            self._bytes += bytes_
            self._nodes += nodes

    def snapshot(self) -> tuple[int, int, int]:
        with self._lock:
            return (self._rows, self._bytes, self._nodes)


def _output_fingerprint(payload: dict[str, Any]) -> str:
    """节点输出的有界内容指纹（checkpoint 校验用，非身份真相）。

    确定性：features/rows 取「总数 + 首 16 尾 4 条的 canonical 摘要」，
    其余取 ref_id / raster_path / metadata 标量投影。弱于内容级哈希是
    诚实的取舍 —— 它只为检测「上游已变化，缓存陈旧」，身份仍由
    fingerprint/revision 体系承担（ADR-0101 D4）。
    """
    projection: dict[str, Any] = {}
    for key in ("features", "rows"):
        items = payload.get(key)
        if isinstance(items, list):
            head = [canonical_dumps(v)[:256] for v in items[:_FP_SAMPLE_HEAD]]
            tail = [canonical_dumps(v)[:256] for v in items[-_FP_SAMPLE_TAIL:]] if len(items) > _FP_SAMPLE_HEAD else []
            projection[key] = {"count": len(items), "head": head, "tail": tail}
    for key in ("ref_id", "raster_path"):
        if payload.get(key) is not None:
            projection[key] = str(payload[key])
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        projection["metadata"] = {
            k: v for k, v in meta.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
    return "fp:" + hashlib.sha256(canonical_dumps(projection).encode("utf-8")).hexdigest()[:16]


def owner_scope_for(
    caller: Optional[dict[str, Any]], session_id: Optional[str] = None
) -> str:
    """从调用者身份派生 owner 域（复用键 / run 归属共用）。

    user id 优先（跨 run 稳定），回退 session id，都没有 → "anonymous"。
    哈希而非原文：owner 域出现在复用键/注册表中，不落明文身份。
    匿名哨兵（"anonymous"）经 auth.actor_ids 折叠为 None → 不与真实
    用户共享任何域。
    """
    import hashlib

    uid: Optional[str] = None
    try:
        from app.core.auth import actor_ids

        uid, _ = actor_ids(caller)
    except Exception:  # noqa: BLE001 - 身份解析失败按匿名处理（fail closed）
        uid = None
    if uid:
        return "u:" + hashlib.sha1(uid.encode(), usedforsecurity=False).hexdigest()[:16]
    if session_id:
        return "s:" + hashlib.sha1(
            str(session_id).encode(), usedforsecurity=False
        ).hexdigest()[:16]
    return "anonymous"


def _scrub_error_message(message: Any) -> str:
    """证据 error_message 只保留有界、去本地拓扑的文本。

    截断到 300 字符；绝对路径（≥2 段）替换为 "<path>"。类型化
    ``error_code`` 单独存列，不受清洗影响。
    """
    text = str(message or "")
    scrubbed = _ABSOLUTE_PATH_RE.sub("<path>", text)
    if len(scrubbed) > _MAX_ERROR_MESSAGE_CHARS:
        scrubbed = scrubbed[:_MAX_ERROR_MESSAGE_CHARS]
    return scrubbed


class NodeResultStore:
    """进程内有界节点结果存储（LRU，双重界：条目数 + 字节预算）。"""

    def __init__(self, max_entries: int = 256, max_bytes: int = 128 * 1024 * 1024):
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _measure(payload: dict[str, Any]) -> int:
        """近似字节量：采样 64 条外推（评审 m2 —— 全量 str() 是大节点上的
        瞬时垃圾源；采样估计对字节预算足够）。"""
        total = 0
        for key in ("features", "rows"):
            items = payload.get(key) or []
            if not items:
                continue
            sample = items[:64]
            avg = sum(len(str(f)) for f in sample) / len(sample)
            total += int(avg * len(items))
        return total

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, payload: dict[str, Any]) -> None:
        size = min(self._measure(payload), self._max_bytes + 1)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.get("__size__", 0)
            if size > self._max_bytes:
                return  # 超预算的大结果不入复用存储（仍可作为本 run 内节点输出）
            # dunder 键是存储保留命名空间：载荷侧同名键丢弃（评审 MINOR ——
            # 否则载荷 __size__ 会腐蚀字节记账，evaluation 期 TypeError）。
            clean = {k: v for k, v in payload.items() if not k.startswith("__")}
            self._entries[key] = {"__size__": size, **clean}
            self._bytes += size
            while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.get("__size__", 0)
                if not self._entries:
                    break


class GeoExecutionEngine:
    """执行计划协调器。一个进程一个实例即可（run 之间无共享可变状态，
    结果存储除外）。"""

    def __init__(
        self,
        *,
        result_store: Optional[NodeResultStore] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        run_cache_size: int = 128,
        retain_outputs: bool = False,
        slot_lease_grace_s: float = DEFAULT_SLOT_LEASE_GRACE_S,
    ):
        self._store = result_store or NodeResultStore()
        self._max_workers = max(1, min(int(max_workers), 8))
        # 评审 M3 的逃生门：基准/测试需要在 run 终态后读取载荷做确定性
        # 断言。生产路径保持默认 False（终态即清除，证据/摘要为准）。
        self._retain_outputs = bool(retain_outputs)
        # R9：槽位租约宽限（节点 deadline 之后多久可强制回收其并发槽位）。
        self._slot_lease_grace_s = max(0.0, float(slot_lease_grace_s))
        self._runs: OrderedDict[str, ExecutionRun] = OrderedDict()
        self._run_outputs: dict[str, dict[str, dict[str, Any]]] = {}
        # SEC：run 归属域（owner_scope_for 派生）；REST 读路径按它做
        # 读隔离（他人 run 一律 404，避免存在性预言机）。
        self._run_owners: dict[str, str] = {}
        # Wave-11（audit 08 §6.2.1）：run 终态附加证据（reproducibility 判定
        # + 无载荷 lineage 投影）。有界：随 run 注册表同一容量上界逐出。
        self._run_extras: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._run_tokens: dict[str, CancellationToken] = {}
        self._run_lock = threading.Lock()
        self._run_cache_size = run_cache_size

    # ------------------------------------------------------------- public

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        session_id: Optional[str] = None,
        caller: Optional[dict[str, Any]] = None,
        cancel_token: Optional[CancellationToken] = None,
        governor: Optional[Any] = None,
        governor_parent_path: Optional[str] = None,
        run_id: Optional[str] = None,
        yield_check: Optional[Any] = None,
        owner_scope_override: Optional[str] = None,
        resource_envelope: Optional[dict[str, Any]] = None,
    ) -> ExecutionRun:
        """执行整个计划（同步；调用方负责卸载到线程）。

        ``governor``（可选，ResourceGovernor）：给定后在
        ``governor_parent_path``（默认根）下创建 execution 作用域，准入
        与逐节点记账沿层级链生效（ADR-0096 D6）。

        ``caller``（可选，auth user dict）：贯穿到算子上下文 —— 目录项
        准入、复用键 owner 域、run 归属都以它为准；缺省按匿名隔离。

        V6（cluster runtime，additive）：
        - ``run_id``：外部持久 run 行的 id（cluster coordinator 路径）——
          本地内存注册表与持久控制面共用同一身份；缺省行为不变（自生成）。
        - ``yield_check``：每轮调度循环调用的安全点探针（返回 True = 请求
          在节点边界让出）。触发后在飞节点经 checkpoint 协作收敛，run 终态
          为 ``preempted``。进程内直跑路径不传 → 零行为差异。
        - ``owner_scope_override``：cluster coordinator 路径注入**提交时**
          的 owner 域（执行进程的 caller/session 与提交进程不同 —— 本地
          注册表与证据快照必须延续提交者身份，读隔离才跨进程一致）。
        """
        graph.validate_plan(plan)
        self._admission_check(plan)
        gov_path: Optional[str] = None
        # R1：计划级预留值（收尾与实际记账合并后全额归还 —— gauge 语义）。
        reserved = {"rows": 0, "bytes": 0, "nodes": 0}
        charge_ledger = _RunChargeLedger()
        if governor is not None:
            from app.services.geocompute.budgets import BudgetLimits, ScopeKind

            parent = governor_parent_path or "global:root"
            limits = BudgetLimits(
                max_rows=plan.budget.max_rows,
                max_bytes=plan.budget.max_bytes,
                max_nodes=plan.budget.max_nodes,
                # ADR-0101 D3 + Wave 8 R7：并发上界以「槽位单位」计
                # （轻节点 1 / 重节点 2，见 _HEAVY_SLOT_* 注释）；上层
                # 作用域的并发限额沿链照常生效（跨 run 加权背压）。
                max_concurrency=self._max_workers * _HEAVY_SLOT_UNITS,
            )
            gov_path = governor.create_scope(
                parent, ScopeKind.EXECUTION, f"gexec-{uuid.uuid4().hex[:8]}",
                limits=limits,
            )
            total_rows = sum(
                (n.estimate.rows or 0) for n in plan.nodes if n.estimate
            )
            try:
                # 原子预留（评审 M2：admit→charge TOCTOU 修复）；估计值先行
                # 预配，节点完成后的实际记账叠加 —— 保守方向（宁可多记）。
                governor.reserve(gov_path, rows=total_rows, nodes=1)
                reserved = {"rows": total_rows, "bytes": 0, "nodes": 1}
            except Exception:
                # 评审 MINOR：计划级预留被祖先链拒绝时，必须摘除刚建的
                # execution 作用域（否则每次被拒 run 永久泄漏一个树节点）。
                governor.teardown_scope(gov_path)
                gov_path = None
                raise

        run_id = run_id or f"gexec-{uuid.uuid4().hex[:12]}"
        plan_fp = plan.graph_fingerprint()
        run = ExecutionRun(
            run_id=run_id, plan_id=plan.plan_id, plan_fingerprint=plan_fp,
            status=ExecutionRunStatus.RUNNING,
        )
        run.evidence = {
            n.node_id: NodeEvidence(status="pending", fingerprint=n.semantic_fingerprint(),
                                    policy=n.policy.value)
            for n in plan.nodes
        }
        outputs: dict[str, dict[str, Any]] = {}
        # 本 run 内各节点的输出内容指纹（checkpoint 上游一致性校验用）。
        outputs_fp: dict[str, str] = {}
        # 评审 MINOR：deadline 从**调度开始**计时 —— 身份解析/首次导入等
        # 一次性准备成本（可达数百毫秒）不再侵蚀执行预算。
        owner_scope = owner_scope_override or owner_scope_for(caller, session_id)
        deadline_ts = time.monotonic() + plan.budget.deadline_s
        with self._run_lock:
            self._runs[run_id] = run
            self._run_outputs[run_id] = outputs
            self._run_owners[run_id] = owner_scope
            while len(self._runs) > self._run_cache_size:
                old = next(iter(self._runs))
                self._runs.pop(old)
                self._run_outputs.pop(old, None)
                self._run_owners.pop(old, None)
                self._run_extras.pop(old, None)
        # run 级取消令牌注册（REST/工具凭 run_id 请求取消；M1）
        if cancel_token is None:
            cancel_token = CancellationToken(job_id=run_id)
        self._run_tokens[run_id] = cancel_token

        tracing.emit("run_started", run_id=run_id, plan_fingerprint=plan_fp,
                     nodes=len(plan.nodes), status="running",
                     budget_scope=gov_path)
        started = time.monotonic()
        preempt_requested = {"flag": False}
        try:
            self._run_ready_set(run, plan, outputs, outputs_fp,
                                session_id=session_id,
                                caller=caller, owner_scope=owner_scope,
                                cancel_token=cancel_token, deadline_ts=deadline_ts,
                                governor=governor, gov_path=gov_path,
                                charge_ledger=charge_ledger,
                                yield_check=yield_check,
                                preempt_flag=preempt_requested,
                                resource_envelope=resource_envelope)
        finally:
            if governor is not None and gov_path:
                # Wave 8 R1：本 run 在祖先链上的全部占用（预留估计 + 实际
                # 记账）一次性归还 —— rows/bytes/nodes 由此是**并发在飞
                # 量衡**而非终生计数（审计 07 R1 Critical：原先只增不减，
                # ~25 个估计偏大的 run 即可永久打满 global 预算 → 自我
                # DoS）。钳零语义（budgets.release）是重复归还的兜底。
                try:
                    charged_rows, charged_bytes, charged_nodes = (
                        charge_ledger.snapshot()
                    )
                    governor.release(
                        gov_path,
                        rows=reserved["rows"] + charged_rows,
                        bytes_=reserved["bytes"] + charged_bytes,
                        nodes=reserved["nodes"] + charged_nodes,
                    )
                except Exception:  # noqa: BLE001 - 归还失败不阻断作用域摘除
                    tracing.emit("budget_release_failed", run_id=run_id,
                                 status=run.status.value)
                # 摘除 execution 作用域（归还后祖先链已回到基本线）
                governor.teardown_scope(gov_path)
            run.wall_time_s = round(time.monotonic() - started, 6)

        failed = [nid for nid, ev in run.evidence.items() if ev.status == "failed"]
        if preempt_requested["flag"]:
            # V6：安全点抢占优先于 cancelled 判定 —— 让出是治理行为不是取消。
            run.status = ExecutionRunStatus.PREEMPTED
        elif any(ev.status == "cancelled" for ev in run.evidence.values()):
            run.status = ExecutionRunStatus.CANCELLED
        elif failed:
            run.status = ExecutionRunStatus.FAILED
            first = run.evidence[sorted(
                failed, key=lambda nid: run.evidence[nid].attempts or 0
            )[0]]
            run.error_code = first.error_code
            run.error_message = first.error_message
        else:
            run.status = ExecutionRunStatus.COMPLETED
        # V5（audit 06 §6.1 step 2）：终态证据快照（有界 ≤16KB，owner 域隔离）
        # 尽力落库 —— 进程重启后 get_run 内存未命中时回放，读取不再 404。
        # fail-open：快照失败绝不倒灌执行结果。
        # Wave-11（audit 08 §6.2.1）：built-but-orphaned 的执行包在此接线 ——
        # run 终态即构建有界、无载荷的可复现清单：判定块 + 无载荷 lineage
        # 投影进内存附加层与终态证据快照（folded JSON key，无迁移）。
        # fail-open：清单构建失败绝不阻断执行路径（诚实日志披露）。
        # 注意：run_bundled 必须在 run_finished 之前发射 —— replay 校验器视
        # run_finished 为终态，其后任何 trace 事件都判违规
        # （test_replay_security_v4::test_happy_path_trace_is_valid）。
        run_extras: dict[str, Any] = {}
        try:
            from app.lib.gis.runtime_manifest import get_runtime_manifest
            from app.services.geocompute import reproducibility as _rb

            bundle = _rb.build_execution_bundle(
                plan, run,
                runtime_manifest_fingerprint=get_runtime_manifest().fingerprint,
            )
            run_extras = {
                "reproducibility": _rb.bundle_verdict_block(bundle),
                "lineage": _rb.lineage_projection(plan, run)[:32],
            }
            tracing.emit("run_bundled", run_id=run_id, status=run.status.value,
                         classification=str(bundle.get("reproducibility")))
        except Exception:  # noqa: BLE001 - 附加证据，绝不阻断执行路径
            tracing.emit("run_bundle_skipped", run_id=run_id,
                         status=run.status.value, reason="bundle_unavailable")
        tracing.emit("run_finished", run_id=run_id, plan_fingerprint=plan_fp,
                     status=run.status.value, duration_s=run.wall_time_s,
                     error_code=run.error_code)
        if run_extras:
            with self._run_lock:
                self._run_extras[run_id] = run_extras
                while len(self._run_extras) > self._run_cache_size:
                    self._run_extras.popitem(last=False)
        try:
            from app.services.geocompute import run_evidence

            run_evidence.save_snapshot(run, owner_scope, extras=run_extras or None)
        except Exception:  # noqa: BLE001 - 快照是尽力而为的持久化证据；
            # 终态后不得再发 trace 事件（replay 终态不变量），降级为日志。
            logger.warning(
                "[geocompute] run evidence snapshot unavailable: run_id=%s status=%s",
                run_id, run.status.value,
            )
        # 载荷保留上限（并发评审 M3）：run 终态后立即丢弃原始节点输出 ——
        # 证据/摘要已在 run.evidence；复用走字节预算化的 NodeResultStore。
        if not self._retain_outputs:
            with self._run_lock:
                self._run_outputs.pop(run_id, None)
        self._run_tokens.pop(run_id, None)
        return run

    def cancel_run(self, run_id: str, reason: str = "cancelled by caller") -> bool:
        """请求取消一个 run（幂等；未知 run → False）。

        级联：run token 已在 execute_plan 内传入各节点 → durable 分支会把
        取消请求写入 job 行（request_cancel_sync），worker 侧 checkpoint
        生效。
        """
        token = self._run_tokens.get(run_id)
        if token is None:
            return False
        return token.cancel(reason)

    def get_run(self, run_id: str, *, owner_scope: Optional[str] = None) -> Optional[ExecutionRun]:
        """读取 run；给定 ``owner_scope`` 时做读隔离 —— 归属不符一律返回
        None（调用方 404，不区分「不存在」与「他人 run」，避免存在性预言机）。

        不传 ``owner_scope``（进程内工具/executor 自身路径）保持原有语义。

        V5（audit 06 §6.1 step 2）：内存未命中时回读**终态证据快照**
        （``run_evidence.load_snapshot``，owner 域校验在读取侧）—— 进程重启
        后 REST/工具读取不再 404；快照回放以 ``run.source == "snapshot"``
        诚实标注（只读证据，非活注册表条目）。
        """
        with self._run_lock:
            run = self._runs.get(run_id)
            if run is not None:
                if owner_scope is not None and self._run_owners.get(run_id) != owner_scope:
                    return None
                return run
        # 内存未命中 → 快照回放（owner 域校验在 load 侧：他人/未知一样 None）。
        try:
            from app.services.geocompute import run_evidence

            return run_evidence.load_snapshot(run_id, owner_scope=owner_scope)
        except Exception:  # noqa: BLE001 - 回放失败按未命中处理（诚实 404）
            return None

    def get_node_output(self, run_id: str, node_id: str) -> Optional[dict[str, Any]]:
        with self._run_lock:
            return (self._run_outputs.get(run_id) or {}).get(node_id)

    def get_run_extras(
        self, run_id: str, *, owner_scope: Optional[str] = None
    ) -> dict[str, Any]:
        """读取 run 的 Wave-11 附加证据（reproducibility 判定 + 无载荷
        lineage 投影）。

        内存未命中 → 终态证据快照回读。owner 域隔离与 ``get_run`` 同一纪律：
        归属不符一律 ``{}``（不区分「不存在」与「他人 run」）。返回值绝无
        节点载荷。
        """
        with self._run_lock:
            extras = self._run_extras.get(run_id)
            if extras is not None:
                if owner_scope is not None and self._run_owners.get(run_id) != owner_scope:
                    return {}
                return extras
        try:
            from app.services.geocompute import run_evidence

            return run_evidence.load_snapshot_extras(run_id, owner_scope=owner_scope)
        except Exception:  # noqa: BLE001 - 附加证据读取 fail-open
            return {}

    # ------------------------------------------------------------ internal

    def _admission_check(self, plan: ExecutionPlan) -> None:
        """预算准入：已知估计的总和不得超过计划预算（未知不阻塞，诚实估计）。"""
        total_rows = 0
        total_bytes = 0
        unknown = False
        for node in plan.nodes:
            est = node.estimate
            if est is None or (est.rows is None and est.bytes is None):
                unknown = True
                continue
            if est.confidence == "assumption":
                unknown = True
            if est.rows is not None:
                total_rows += est.rows
            if est.bytes is not None:
                total_bytes += est.bytes
        over: list[str] = []
        if total_rows > plan.budget.max_rows:
            over.append(f"rows {total_rows} > budget {plan.budget.max_rows}")
        if total_bytes > plan.budget.max_bytes:
            over.append(f"bytes {total_bytes} > budget {plan.budget.max_bytes}")
        if over:
            raise BudgetExceededError(
                "plan admission rejected: " + "; ".join(over),
                suggestions=[
                    "push down filters/aggregation to sources",
                    "use statistics-aware planning (data fabric V3 optimizer)",
                    "materialize per-source subsets before joining",
                    "explicitly raise the budget for approved heavy paths",
                ],
                details={"estimated_rows": total_rows, "estimated_bytes": total_bytes,
                         "estimates_unknown_for_some_nodes": unknown},
            )

    def _run_ready_set(
        self,
        run: ExecutionRun,
        plan: ExecutionPlan,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        *,
        session_id: Optional[str],
        caller: Optional[dict[str, Any]],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
        charge_ledger: Optional[Any] = None,
        yield_check: Optional[Any] = None,
        preempt_flag: Optional[dict[str, bool]] = None,
        resource_envelope: Optional[dict[str, Any]] = None,
    ) -> None:
        """就绪集调度（ADR-0101 D3）：indegree 驱动，无硬波次屏障。

        - 就绪节点按字典序稳定派发（与波次序兼容的确定性）；
        - 派发前做 governor weighted admission（concurrency 槽位预留，
          沿层级链生效；Wave 8 R7：单位数按 ResourceClass 加权）；
          拒绝 → 背压（本轮回填队首，等槽位释放）；
        - 启动前检查祖先终态：failed/cancelled/skipped → 后代 skipped；
        - 取消/deadline 对「未启动」节点统一收敛，绝不产生僵尸任务；
        - Wave 8 R9：超过 deadline+宽限仍占槽的非协作在飞节点，其并发
          槽位由租约看门狗强制归还（线程本身不可杀）。
        """
        node_map = plan.node_map()
        indegree = {nid: len(n.inputs) for nid, n in node_map.items()}
        dependents: dict[str, list[str]] = {nid: [] for nid in node_map}
        for node in plan.nodes:
            for src in node.inputs:
                dependents[src].append(node.node_id)
        ready = sorted(nid for nid, deg in indegree.items() if deg == 0)
        inflight: dict[Any, str] = {}
        # R7/R9 的 per-run 账簿（有界：键随 settle 移除，run 收尾清空）。
        inflight_units: dict[Any, int] = {}
        leases: dict[str, dict[str, Any]] = {}
        lease_reclaimed: set[str] = set()
        launched = 0

        def _settle(nid: str) -> None:
            for dep in dependents[nid]:
                indegree[dep] -= 1
                if indegree[dep] == 0 and run.evidence[dep].status == "pending":
                    ready.append(dep)
            if ready:
                ready.sort()

        def _sweep_remaining(reason: str, *, escalate: bool = False) -> None:
            for nid in ready:
                ev = run.evidence[nid]
                if ev.status == "pending":
                    ev.status = "cancelled"
                    ev.error_message = reason
                    tracing.emit("node_marked", run_id=run.run_id, node_id=nid,
                                 status="cancelled", reason=reason)
            ready.clear()
            if escalate and cancel_token is not None:
                # 评审 MAJOR 修正：deadline 触发时升级为 run 级取消 —— 在飞
                # 的协作节点（raster 窗口/时间块循环）经由各自 checkpoint
                # 观察收敛，而不是跑完整个自然生命周期。
                cancel_token.cancel(reason)

        def _reclaim_expired_slot_leases() -> None:
            """R9：租约看门狗 —— 节点超过其 deadline+宽限仍在跑（线程
            不可强杀的非协作节点）→ 强制归还它占用的并发槽位，祖先
            作用域（session/tenant）的槽位预算不再被永久占用。

            正确性：``lease_reclaimed`` 让节点真正落定时的 settle 路径
            跳过二次释放（无重复归还）；线程仍在跑 → 它继续消耗真实
            CPU/内存但不占治理槽位（诚实取舍：治理槽位是调度资源，
            不是线程存活探针）。
            """
            now = time.monotonic()
            for fut, nid in list(inflight.items()):
                lease = leases.get(nid)
                if lease is None or nid in lease_reclaimed:
                    continue
                if not fut.running():
                    continue  # 已落定/尚未开跑 → 交给正常 settle 路径
                if now <= lease["deadline"] + self._slot_lease_grace_s:
                    continue
                lease_reclaimed.add(nid)
                leases.pop(nid, None)
                if governor is not None and gov_path:
                    governor.release(gov_path, concurrency=lease["units"])
                tracing.emit("slot_lease_reclaimed", run_id=run.run_id,
                             node_id=nid, reason="lease_expired",
                             concurrency=lease["units"])

        with ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="geocompute-node",
        ) as pool:
            while ready or inflight:
                # V6 抢占安全点：每轮循环探测 coordinator 的让出请求 ——
                # 只在节点边界生效（在飞节点经 checkpoint 协作收敛，绝不
                # 强杀线程）。触发后行为与取消相同的收敛路径，但 run 终态
                # 由调用方（execute_plan）判为 preempted。
                if (
                    yield_check is not None
                    and preempt_flag is not None
                    and not preempt_flag["flag"]
                    and yield_check()
                ):
                    preempt_flag["flag"] = True
                    _sweep_remaining("preempted at safe point", escalate=True)
                # 取消 / deadline：只收敛「未启动」节点；在飞的由节点自身
                # 的协作 checkpoint 收敛（repo 约束：线程不可强杀）。
                if cancel_token is not None and cancel_token.cancelled:
                    _sweep_remaining(cancel_token.reason or "cancelled by caller")
                elif deadline_ts - time.monotonic() <= 0:
                    _sweep_remaining("deadline exceeded", escalate=True)
                if not ready and not inflight:
                    break
                _reclaim_expired_slot_leases()

                # 填满并行槽位（背压：governor 拒绝时停在本轮回填队首）。
                while ready and len(inflight) < self._max_workers:
                    nid = ready[0]
                    node = node_map[nid]
                    if any(
                        run.evidence[s].status in {"failed", "cancelled", "skipped"}
                        for s in node.inputs if s in run.evidence
                    ):
                        ready.pop(0)
                        run.evidence[nid].status = "skipped"
                        tracing.emit("node_skipped", run_id=run.run_id, node_id=nid,
                                     status="skipped", reason="ancestor_not_completed")
                        _settle(nid)
                        continue
                    # R7：ResourceClass → 槽位单位（重节点 2，其余 1）。
                    units = self.slot_units_for(node)
                    if governor is not None and gov_path:
                        try:
                            governor.reserve(gov_path, concurrency=units)
                        except BudgetExceededError:
                            # weighted admission 背压（ADR-0101 D3）：槽位
                            # 紧张时不再制造新任务；等一个在飞节点落定。
                            break
                    ready.pop(0)
                    tracing.emit("node_admitted", run_id=run.run_id, node_id=nid,
                                 status="running", policy=node.policy.value,
                                 concurrency=units)
                    run.evidence[nid].status = "running"
                    fut = pool.submit(
                        self._execute_one, run, node, outputs, outputs_fp,
                        session_id=session_id, caller=caller,
                        owner_scope=owner_scope,
                        cancel_token=cancel_token,
                        deadline_ts=deadline_ts, budget=plan.budget,
                        governor=governor, gov_path=gov_path,
                        charge_ledger=charge_ledger,
                        resource_envelope=resource_envelope,
                    )
                    inflight[fut] = nid
                    inflight_units[fut] = units
                    # R9 租约：节点自身 deadline（≤ run deadline）为租期基准。
                    lease_deadline = deadline_ts
                    if node.deadline_s is not None:
                        lease_deadline = min(
                            lease_deadline, time.monotonic() + node.deadline_s
                        )
                    leases[nid] = {"units": units, "deadline": lease_deadline}
                    launched += 1

                if not inflight:
                    if ready:
                        # governor 持续拒绝且无在飞槽位可等：短暂让步后重试
                        #（deadline sweep 兜底，绝不无限自旋）。
                        time.sleep(0.01)
                        continue
                    break

                # R9：wait 以巡检间隔为上界周期性返回 —— 看门狗在节点级
                # deadline+宽限（而非 run deadline）就能巡检到僵尸槽位。
                done, _ = wait(
                    set(inflight), return_when=FIRST_COMPLETED,
                    timeout=max(0.05, min(
                        deadline_ts - time.monotonic(), _LEASE_SWEEP_INTERVAL_S,
                    )),
                )
                for fut in done:
                    nid = inflight.pop(fut)
                    units = inflight_units.pop(fut, 1)
                    leases.pop(nid, None)
                    if (
                        governor is not None and gov_path
                        and nid not in lease_reclaimed
                    ):
                        # R9：被看门狗强制回收过的槽位不再二次释放
                        #（clamp-at-zero 本也兜底，这里直接精确配对）。
                        governor.release(gov_path, concurrency=units)
                    exc = fut.exception()
                    if exc is not None:  # noqa: BLE001 - _execute_one 已类型化收编
                        ev = run.evidence[nid]
                        if ev.status in {"running", "pending"}:
                            ev.status = "failed"
                            ev.error_code = "NODE_FAILED"
                            ev.error_message = _scrub_error_message(str(exc))
                    _settle(nid)
        # R7/R9 账簿随 run 收尾清空（有界 per-run dict；异常路径随栈帧丢弃）。
        inflight_units.clear()
        leases.clear()
        lease_reclaimed.clear()

    def _execute_one(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        *,
        session_id: Optional[str],
        caller: Optional[dict[str, Any]],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        deadline_ts: float,
        budget: Any = None,
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
        charge_ledger: Optional[Any] = None,
        resource_envelope: Optional[dict[str, Any]] = None,
    ) -> None:
        ev = run.evidence[node.node_id]
        node_deadline = deadline_ts
        if node.deadline_s is not None:
            node_deadline = min(node_deadline, time.monotonic() + node.deadline_s)

        if node.policy.value == "durable_job":
            self._execute_durable(
                run, node, outputs, outputs_fp, ev, session_id=session_id,
                owner_scope=owner_scope,
                cancel_token=cancel_token, node_deadline=node_deadline,
                governor=governor, gov_path=gov_path,
                charge_ledger=charge_ledger,
                budget=budget,
                resource_envelope=resource_envelope,
            )
            if ev.status in {"completed", "reused"} and node.node_id in outputs:
                outputs_fp[node.node_id] = _output_fingerprint(outputs[node.node_id])
            return

        # 复用（ADR-0101 D4）：owner 域隔离 + 节点语义指纹寻址。
        # 有内部上游输入的节点用跨计划 checkpoint 键（条目带上游输出指纹，
        # 命中前校验一致性）；无输入的外部源节点（QUERY/SOURCE_SCAN）保留
        # 计划域键 —— 外部内容漂移无法用 checkpoint 指纹验证，信任域仍以
        # 计划身份为界（V3 语义）。owner 域隔离不变（SEC）。
        if node.inputs:
            reuse_key = graph.checkpoint_reuse_key(node, owner_scope)
        else:
            reuse_key = graph.node_reuse_key(run.plan_fingerprint, node, owner_scope)
        # 复用：语义指纹命中 + owner 域隔离 + 策略允许 → 校验 checkpoint
        # 上游一致性后跳过执行（ADR-0101 D4）。
        # owner_scope 使不同用户/会话即使指纹相同也绝不共享缓存条目（SEC）。
        if node.reuse == NodeReusePolicy.ALLOW:
            cached = self._store.get(reuse_key)
            if cached is not None and _SIZE_KEY in cached:
                stale = self._checkpoint_stale(node, cached, outputs_fp)
                if stale is None:
                    # 浅拷贝：复用载荷与缓存条目解除别名（评审 MINOR ——
                    # 操作数约定不改写输入；拷贝兜底防缓存腐蚀）。
                    payload = {k: v for k, v in cached.items()
                               if not k.startswith("__")}
                    outputs[node.node_id] = payload
                    out_fp = cached.get(_OUT_FP_KEY) or _output_fingerprint(payload)
                    outputs_fp[node.node_id] = out_fp
                    self._governor_charge(
                    governor, gov_path, node, payload, charge_ledger
                )
                    ev.status = "reused"
                    ev.checkpoint_verified = True
                    ev.rows_emitted = self._count_rows(payload)
                    tracing.emit("node_reused", run_id=run.run_id, node_id=node.node_id,
                                 status="reused", rows=ev.rows_emitted, checkpoint="verified")
                    return
                tracing.emit("node_reused", run_id=run.run_id, node_id=node.node_id,
                             status="miss", checkpoint="stale",
                             reason=f"upstream_changed:{','.join(sorted(stale))}")
                ev.checkpoint_verified = False

        attempts_allowed = node.retry.max_attempts
        started = time.monotonic()
        last_err: Optional[GeoComputeError] = None
        for attempt in range(1, attempts_allowed + 1):
            ev.attempts = attempt
            if cancel_token is not None and cancel_token.cancelled:
                ev.status = "cancelled"
                return
            try:
                ctx = ops.OperatorContext(
                    run_id=run.run_id,
                    node_id=node.node_id,
                    session_id=session_id,
                    caller=caller,
                    budget=budget,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token,
                )
                token = cancel_token if node.cancellable else None
                with use_token(token):
                    payload = ops.execute_node(ctx, node, outputs)
                outputs[node.node_id] = payload
                out_fp = _output_fingerprint(payload)
                outputs_fp[node.node_id] = out_fp
                self._store.put(reuse_key, {
                    _NODE_FP_KEY: node.semantic_fingerprint(),
                    _OUT_FP_KEY: out_fp,
                    _UPSTREAM_KEY: {
                        s: outputs_fp[s] for s in node.inputs if s in outputs_fp
                    },
                    **payload,
                })
                self._governor_charge(
                    governor, gov_path, node, payload, charge_ledger
                )
                ev.status = "completed"
                ev.rows_emitted = self._count_rows(payload)
                ev.duration_s = round(time.monotonic() - started, 6)
                ev.output_ref = payload.get("ref_id")
                ev.output_summary = {
                    k: v for k, v in (payload.get("metadata") or {}).items()
                    if isinstance(v, (str, int, float, bool, type(None)))
                }
                tracing.emit("node_completed", run_id=run.run_id, node_id=node.node_id,
                             status="completed", rows=ev.rows_emitted,
                             duration_s=ev.duration_s, attempts=attempt)
                return
            except OperationCancelled as exc:
                ev.status = "cancelled"
                ev.error_code = "CANCELLED"
                ev.error_message = _scrub_error_message(str(exc))
                ev.duration_s = round(time.monotonic() - started, 6)
                tracing.emit("node_cancelled", run_id=run.run_id, node_id=node.node_id,
                             status="cancelled", duration_s=ev.duration_s)
                return
            except GeoComputeError as exc:
                last_err = exc
                fclass = classify_failure(exc)
                if len(ev.failure_codes) < 4:
                    ev.failure_codes.append(exc.code)
                retryable = self._retry_allowed(node, fclass)
                tracing.emit("node_attempt_failed", run_id=run.run_id,
                             node_id=node.node_id, status="failed",
                             error_code=exc.code, attempts=attempt,
                             failure_class=fclass.value)
                if not retryable or attempt >= attempts_allowed:
                    break
                delay = self._retry_delay_s(node, attempt, node_deadline)
                if delay is None:
                    # deadline 感知拒绝：来不及完成的重试不启动（ADR-0101 D5）。
                    tracing.emit("node_attempt_failed", run_id=run.run_id,
                                 node_id=node.node_id, status="failed",
                                 error_code=exc.code, attempts=attempt,
                                 reason="retry_refused_deadline")
                    break
                time.sleep(delay)
            except Exception as exc:  # noqa: BLE001 - 类型化收编，绝不让线程带异常死掉
                last_err = NodeExecutionError(
                    f"{type(exc).__name__}: {exc}", retry_safe=False, node_id=node.node_id
                )
                tracing.emit("node_attempt_failed", run_id=run.run_id,
                             node_id=node.node_id, status="failed",
                             error_code="NODE_FAILED", attempts=attempt,
                             failure_class=FailureClass.INVALID_DATA.value)
                break

        ev.status = "failed"
        ev.error_code = last_err.code if last_err else "NODE_FAILED"
        ev.error_message = _scrub_error_message(str(last_err) if last_err else "unknown failure")
        if isinstance(last_err, NodeExecutionError):
            ev.retry_safe = last_err.retry_safe
        ev.duration_s = round(time.monotonic() - started, 6)
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     duration_s=ev.duration_s,
                     failure_class=(classify_failure(last_err).value if last_err else "invalid_data"))

    # ------------------------------------------------------- retry helpers

    @staticmethod
    def _retry_allowed(node: ExecutionNode, failure_class: FailureClass) -> bool:
        """重试白名单（ADR-0101 D5）：只对显式安全的类别生效。"""
        if failure_class in RETRYABLE_FAILURE_CLASSES:
            return True
        if not node.retry.retry_transient_only and (
            failure_class in EXTENDED_RETRYABLE_FAILURE_CLASSES
        ):
            return True
        return False

    @staticmethod
    def _retry_delay_s(
        node: ExecutionNode, attempt: int, node_deadline: float
    ) -> Optional[float]:
        """有界指数退避；``None`` = deadline 感知拒绝（来不及完成）。

        jitter 仅在策略允许时叠加（确定性重放应设 ``jitter=false``）。
        """
        delay = min(
            node.retry.backoff_s * (node.retry.backoff_multiplier ** (attempt - 1)),
            node.retry.max_backoff_s,
        )
        if node.retry.jitter and delay > 0:
            delay = random.uniform(0.0, delay)
        if time.monotonic() + delay >= node_deadline:
            return None
        return max(delay, 0.0)

    def _checkpoint_stale(
        self, node: ExecutionNode, cached: dict[str, Any], outputs_fp: dict[str, str]
    ) -> Optional[list[str]]:
        """缓存条目的上游一致性校验；返回已变化的输入列表（None = 新鲜）。

        缓存条目记录执行时的上游输出指纹；本 run 的上游指纹与之不符
        （上游重算且内容变化）→ 陈旧拒绝，诚实重算。
        """
        recorded = cached.get(_UPSTREAM_KEY)
        if not isinstance(recorded, dict):
            return None  # 旧条目（无标记）：按版本一致性视为新鲜
        changed = [
            src for src, fp in recorded.items()
            if src in outputs_fp and outputs_fp[src] != fp
        ]
        return changed or None

    def _execute_durable(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        ev: NodeEvidence,
        *,
        session_id: Optional[str],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        node_deadline: float,
        governor: Optional[Any],
        gov_path: Optional[str],
        charge_ledger: Optional[Any] = None,
        budget: Any = None,
        resource_envelope: Optional[dict[str, Any]] = None,
    ) -> None:
        """durable_job 分支：穿透既有 AnalysisTask 运行时（无第二真相）。

        WORKER_LOSS 类失败按节点 RetryPolicy 有界重派（幂等键保证不产生
        第二 job 行 —— 终态行释放键后重派才建新行，语义即重跑）。

        V5（audit 06 §6.1 step 1/3/5）：
        - 派发按 ``durable.queue_for_node`` 落 profile 队列（重派同节点 →
          同队列，retry affinity 无需新状态机）；
        - **派发前**先查 checkpoint 复用（进程内 store → 跨进程 DB 索引，
          上游指纹一致 + result_ref 存活才命中）—— 最贵的 durable 节点
          终于进入复用；
        - eager（无 Redis）时诚实标注 ``backend_variant="in_process_eager"``。

        V6（P0-3 修复）：plan budget 随派发穿透到 worker 任务体 —— 此前
        worker 侧 ``OperatorContext`` 无 budget，行数红线只剩
        HARD_NODE_ROW_CAP，plan 级预算在 durable 路径形同虚设。budget 走
        task_kwargs（不进 params）—— 不改幂等键（治理元数据≠节点身份）。
        """
        started_dj = time.monotonic()
        if node.reuse == NodeReusePolicy.ALLOW and self._durable_reuse_hit(
            run, node, outputs, outputs_fp, ev, owner_scope,
            governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
        ):
            ev.duration_s = round(time.monotonic() - started_dj, 6)
            return
        attempts_allowed = node.retry.max_attempts
        last_err: Optional[GeoComputeError] = None
        for attempt in range(1, attempts_allowed + 1):
            ev.attempts = attempt
            if cancel_token is not None and cancel_token.cancelled:
                ev.status = "cancelled"
                return
            try:
                from app.services.geocompute import durable

                if not session_id:
                    raise NodeExecutionError(
                        "durable_job policy requires a session context for "
                        "result handoff (session ref)",
                        retry_safe=False, node_id=node.node_id,
                    )
                # V7 input handoff：上游输出已有 session ref 的输入经
                # task_kwargs 交给 worker 解析（全 durable 链首次可执行）；
                # 无 ref 的内存输入（in_process 上游）worker 侧仍不可达 ——
                # 与 V6 行为一致（诚实：混合 DAG 才能全分布式）。
                input_refs = {
                    src: str(outputs[src]["ref_id"])
                    for src in node.inputs
                    if src in outputs and outputs[src].get("ref_id")
                }
                input_keys = {
                    src: outputs_fp[src]
                    for src in node.inputs if src in outputs_fp
                }
                ret = durable.dispatch_node(
                    node,
                    session_id=session_id,
                    plan_fingerprint=run.plan_fingerprint,
                    deadline_s=(node_deadline - time.monotonic())
                    if node.deadline_s is not None else None,
                    budget=budget,
                    run_id=run.run_id,
                    node_attempt=attempt,
                    input_refs=input_refs,
                    input_keys=input_keys,
                    resource_envelope=resource_envelope,
                    owner_scope=owner_scope,
                )
                if ret.get("backend_variant"):
                    # V5 step 5：eager 降级诚实披露（reproducibility honesty）。
                    ev.backend_variant = str(ret["backend_variant"])
                tracing.emit("node_dispatched", run_id=run.run_id,
                             node_id=node.node_id, status="running",
                             job_id=str(ret.get("job_id", "")), policy="durable_job",
                             category=node.category.value,
                             queue=ret.get("queue"),
                             backend=ret.get("backend_variant"))
                done = durable.await_node_job(
                    ret["job_id"],
                    session_id=session_id,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token if node.cancellable else None,
                )
                payload = done["payload"]
                if not payload:
                    raise NodeExecutionError(
                        "durable job produced no resolvable payload",
                        retry_safe=False, node_id=node.node_id,
                    )
                outputs[node.node_id] = payload
                ev.status = "completed"
                ev.rows_emitted = self._count_rows(payload)
                ev.duration_s = round(time.monotonic() - started_dj, 6)
                ev.output_ref = payload.get("ref_id")
                ev.attempts = attempt
                tracing.emit("node_completed", run_id=run.run_id, node_id=node.node_id,
                             status="completed", rows=ev.rows_emitted,
                             duration_s=ev.duration_s, job_id=done["job_id"],
                             policy="durable_job")
                self._governor_charge(
                    governor, gov_path, node, payload, charge_ledger
                )
                # V5 step 3：完成即记录复用事实（进程内 store + 跨进程索引）。
                self._record_durable_result(
                    run, node, outputs, outputs_fp, owner_scope,
                    session_id=session_id, payload=payload,
                )
                return
            except OperationCancelled:
                ev.status = "cancelled"
                ev.error_code = "CANCELLED"
                ev.duration_s = round(time.monotonic() - started_dj, 6)
                tracing.emit("node_cancelled", run_id=run.run_id, node_id=node.node_id,
                             status="cancelled", policy="durable_job")
                return
            except GeoComputeError as exc:
                last_err = exc
                fclass = classify_failure(exc)
                if len(ev.failure_codes) < 4:
                    ev.failure_codes.append(exc.code)
                tracing.emit("node_attempt_failed", run_id=run.run_id,
                             node_id=node.node_id, status="failed",
                             error_code=exc.code, attempts=attempt,
                             failure_class=fclass.value, policy="durable_job")
                if not self._retry_allowed(node, fclass) or attempt >= attempts_allowed:
                    break
                delay = self._retry_delay_s(node, attempt, node_deadline)
                if delay is None:
                    break
                time.sleep(delay)
        ev.status = "failed"
        ev.error_code = last_err.code if last_err else "NODE_FAILED"
        ev.error_message = _scrub_error_message(str(last_err) if last_err else "unknown failure")
        ev.retry_safe = getattr(last_err, "retry_safe", False)
        ev.duration_s = round(time.monotonic() - started_dj, 6)
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     policy="durable_job",
                     failure_class=(classify_failure(last_err).value if last_err else "invalid_data"))

    # ------------------------------------------- V5 durable reuse helpers

    def _durable_reuse_hit(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        ev: NodeEvidence,
        owner_scope: str,
        *,
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
        charge_ledger: Optional[Any] = None,
    ) -> bool:
        """durable 节点派发前的跨进程 checkpoint 复用（audit 06 §6.1 step 3）。

        两级查找，同一套上游一致性校验：
          (a) 进程内 NodeResultStore（与 in_process 节点同键空间）；
          (b) DB 复用索引（geocompute_node_results；fail-open）—— 命中还需
              ``result_ref`` 经会话存储**存活探测**通过才复用。

        复用被拒时记录类型化原因（``ev.reuse_skipped_reason`` + trace）：
        ``upstream_changed:<nodes>`` / ``result_ref_unresolvable``。无条目
        不是「跳过」（没有可复用物），不打证据。
        """
        node_fp = node.semantic_fingerprint()
        reuse_key = graph.checkpoint_reuse_key(node, owner_scope)
        cached = self._store.get(reuse_key)
        if cached is not None and _SIZE_KEY in cached:
            stale = self._checkpoint_stale(node, cached, outputs_fp)
            if stale is None:
                payload = {k: v for k, v in cached.items() if not k.startswith("__")}
                self._accept_durable_reuse(
                    run, node, outputs, outputs_fp, ev, payload,
                    source="in_process", out_fp=cached.get(_OUT_FP_KEY),
                    governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
                )
                return True
            self._note_reuse_skip(
                ev, f"upstream_changed:{','.join(sorted(stale))}", run, node,
            )

        try:
            from app.services.geocompute import reuse_index

            entry = reuse_index.find_result(owner_scope, node_fp)
        except Exception:  # noqa: BLE001 - 索引不可用 → 未命中（诚实重算）
            entry = None
            tracing.emit("node_reuse_skipped", run_id=run.run_id,
                         node_id=node.node_id, reason="index_unavailable")
        if entry is None:
            return False

        recorded = entry.get("upstream_fingerprints") or {}
        changed = [
            src for src, fp in recorded.items()
            if src in outputs_fp and outputs_fp[src] != fp
        ]
        if changed:
            self._note_reuse_skip(
                ev, f"upstream_changed:{','.join(sorted(changed))}", run, node,
            )
            return False

        payload = self._resolve_session_ref(
            entry.get("session_id"), entry.get("result_ref")
        )
        if payload is None:
            # ref 已被会话回收/失效：移除死条目（有界索引保持诚实），重算。
            self._note_reuse_skip(ev, "result_ref_unresolvable", run, node)
            try:
                from app.services.geocompute import reuse_index

                reuse_index.delete_result(owner_scope, node_fp)
            except Exception:  # noqa: BLE001 - 卫生删除是尽力而为
                pass
            return False

        out_fp = _output_fingerprint(payload)
        # 回填进程内 store（后续同进程命中走 fast path，带完整校验元数据）。
        upstream = {
            **{s: fp for s, fp in recorded.items()},
            **{s: outputs_fp[s] for s in node.inputs if s in outputs_fp},
        }
        self._store.put(reuse_key, {
            _NODE_FP_KEY: node_fp,
            _OUT_FP_KEY: out_fp,
            _UPSTREAM_KEY: upstream,
            **payload,
        })
        self._accept_durable_reuse(
            run, node, outputs, outputs_fp, ev, payload,
            source="cross_process_index", out_fp=out_fp,
            governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
        )
        return True

    def _accept_durable_reuse(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        ev: NodeEvidence,
        payload: dict[str, Any],
        *,
        source: str,
        out_fp: Optional[str],
        governor: Optional[Any] = None,
        gov_path: Optional[str] = None,
        charge_ledger: Optional[Any] = None,
    ) -> None:
        """接受复用：写载荷/指纹/证据（浅拷贝防缓存别名腐蚀，同 in_process 路径）。

        DIST（round1）：复用也是资源消费 —— 与执行完成路径同一口径沿层级链
        记账（gauge 语义下漏记会让长寿命作用域的基线被复用流量无偿侵占）。
        """
        outputs[node.node_id] = payload
        outputs_fp[node.node_id] = out_fp or _output_fingerprint(payload)
        ev.status = "reused"
        ev.checkpoint_verified = True
        ev.reuse_source = source
        ev.rows_emitted = self._count_rows(payload)
        ev.output_ref = payload.get("ref_id")
        self._governor_charge(governor, gov_path, node, payload, charge_ledger)
        tracing.emit("node_reused", run_id=run.run_id, node_id=node.node_id,
                     status="reused", rows=ev.rows_emitted, checkpoint="verified",
                     reuse_source=source)

    def _record_durable_result(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        owner_scope: str,
        *,
        session_id: Optional[str],
        payload: dict[str, Any],
    ) -> None:
        """durable 节点完成 → 写两级复用事实（进程内 store + DB 索引）。

        V4 的 durable 分支从不写 NodeResultStore（audit：最贵的节点没有
        复用）—— 现在与 in_process 节点同键空间同校验；DB 索引让复用跨
        worker / 跨 restart 成立。ref 缺失（无会话交接）时只写进程内。
        """
        node_fp = node.semantic_fingerprint()
        out_fp = outputs_fp.get(node.node_id) or _output_fingerprint(payload)
        outputs_fp[node.node_id] = out_fp
        upstream = {s: outputs_fp[s] for s in node.inputs if s in outputs_fp}
        try:
            self._store.put(
                graph.checkpoint_reuse_key(node, owner_scope),
                {
                    _NODE_FP_KEY: node_fp,
                    _OUT_FP_KEY: out_fp,
                    _UPSTREAM_KEY: upstream,
                    **payload,
                },
            )
        except Exception:  # noqa: BLE001 - 复用记录是尽力而为
            pass
        ref = payload.get("ref_id")
        if not ref or not session_id:
            return
        try:
            from app.services.geocompute import reuse_index

            ok = reuse_index.record_result(
                owner_scope=owner_scope,
                node_fingerprint=node_fp,
                result_ref=str(ref),
                session_id=str(session_id),
                upstream_fingerprints=upstream,
            )
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            tracing.emit("node_reuse_record_skipped", run_id=run.run_id,
                         node_id=node.node_id, reason="index_unavailable")

    def _note_reuse_skip(
        self, ev: NodeEvidence, reason: str, run: ExecutionRun, node: ExecutionNode,
    ) -> None:
        """复用被拒的类型化证据（诚实：原因进 evidence + trace）。"""
        ev.reuse_skipped_reason = reason
        ev.checkpoint_verified = False
        tracing.emit("node_reuse_skipped", run_id=run.run_id,
                     node_id=node.node_id, reason=reason)

    @staticmethod
    def _resolve_session_ref(
        session_id: Optional[str], ref: Optional[str]
    ) -> Optional[dict[str, Any]]:
        """把 session ref 解析回节点载荷（与 await_node_job 同一形状）。

        会话存储不可用 / ref 失效 → None（调用方诚实重算）。
        """
        if not session_id or not ref:
            return None
        try:
            from app.services.geocompute._async_bridge import run_coro_sync
            from app.services.session_data import session_data_manager

            stored = run_coro_sync(session_data_manager.get(session_id, ref))
        except Exception:  # noqa: BLE001 - 会话存储故障 → 探测未命中
            return None
        if stored is None:
            return None
        return {"ref_id": ref, "features": stored, "metadata": {"via": "durable_reuse"}}

    @staticmethod
    def slot_units_for(node: ExecutionNode) -> int:
        """ResourceClass → 并发槽位单位（Wave 8 R7 接线；确定性、无随机）。

        memory ≥ 4 或 cpu ≥ 5 → 2 单位，其余 1。效果经由**祖先作用域**的
        并发预算产生（session/tenant 槽位被重节点按 2× 消耗 → 跨 run
        加权背压）；EXECUTION 自身上界以单位计（2×max_workers，见
        ``execute_plan``）保证单重节点绝不自锁。此前该声明维度
        （``plan.ResourceClass``）已解析但从未被任何调度/治理路径消费
        （audit 07 R7）。
        """
        rc = node.resource_class
        if rc.memory >= _HEAVY_SLOT_MEMORY or rc.cpu >= _HEAVY_SLOT_CPU:
            return _HEAVY_SLOT_UNITS
        return 1

    @staticmethod
    def _governor_charge(governor: Any, gov_path: Optional[str],
                         node: ExecutionNode, payload: dict[str, Any],
                         charge_ledger: Optional[Any] = None) -> None:
        """节点完成 → 沿层级链记账（行数 + 字节，ADR-0101 D10）。

        字节用与 NodeResultStore 相同的采样近似 —— 有界 O(1)，只在有
        bytes 限额的作用域上有意义；行数始终记账。
        Wave 8 R1：同步累计到本 run 的 charge ledger，供收尾全额归还
        （gauge 语义：长寿命作用域回到基本线）。
        """
        if governor is None or gov_path is None:
            return
        rows_payload = payload.get("features") or payload.get("rows") or []
        bytes_est = NodeResultStore._measure(payload)
        governor.charge(gov_path, rows=len(rows_payload), bytes=bytes_est, nodes=1)
        if charge_ledger is not None:
            charge_ledger.add(len(rows_payload), bytes_est, 1)

    @staticmethod
    def _count_rows(payload: dict[str, Any]) -> Optional[int]:
        if "features" in payload:
            return len(payload["features"])
        if "rows" in payload:
            return len(payload["rows"])
        return None


#: 进程级默认引擎（与 spatial_catalog_service 同一单例惯例）。
engine = GeoExecutionEngine()
