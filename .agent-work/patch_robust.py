"""一次性补丁：executor 接入 quarantine + speculative duplicate（执行后删除）。"""
import ast
import pathlib

p = pathlib.Path("app/services/geocompute/executor.py")
t = p.read_text(encoding="utf-8")

# ── 1) 引擎配置：投机阈值（env，默认 0 = 停用）───────────────────────────
old = """    def __init__(
        self,
        *,
        result_store: Optional[NodeResultStore] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        run_cache_size: int = 128,
        retain_outputs: bool = False,
        slot_lease_grace_s: float = DEFAULT_SLOT_LEASE_GRACE_S,
    ):"""
new = """    def __init__(
        self,
        *,
        result_store: Optional[NodeResultStore] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        run_cache_size: int = 128,
        retain_outputs: bool = False,
        slot_lease_grace_s: float = DEFAULT_SLOT_LEASE_GRACE_S,
        speculative_after_s: Optional[float] = None,
    ):"""
assert t.count(old) == 1, "init anchor"
t = t.replace(old, new, 1)

old = """        self._slot_lease_grace_s = max(0.0, float(slot_lease_grace_s))"""
new = """        self._slot_lease_grace_s = max(0.0, float(slot_lease_grace_s))
        # V8 Phase F：节点级 straggler → 投机副本的触发阈值（durable 节点
        # 已运行秒数；0/负 = 停用 —— 副本有真实算力成本，显式 opt-in）。
        if speculative_after_s is None:
            try:
                speculative_after_s = float(
                    os.environ.get("WEBGIS_SPECULATIVE_AFTER_S", "") or 0)
            except ValueError:
                speculative_after_s = 0.0
        self._speculative_after_s = max(0.0, float(speculative_after_s))"""
assert t.count(old) == 1, "grace anchor"
t = t.replace(old, new, 1)

# ── 2) _execute_durable：入口隔离检查 ────────────────────────────────────
old = """        started_dj = time.monotonic()
        if node.reuse == NodeReusePolicy.ALLOW and self._durable_reuse_hit(
            run, node, outputs, outputs_fp, ev, owner_scope,
            governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
            emit_events=emit_events,
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
                from app.services.geocompute import durable"""
new = """        started_dj = time.monotonic()
        if node.reuse == NodeReusePolicy.ALLOW and self._durable_reuse_hit(
            run, node, outputs, outputs_fp, ev, owner_scope,
            governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
            emit_events=emit_events,
        ):
            ev.duration_s = round(time.monotonic() - started_dj, 6)
            return
        # V8 Phase F：毒任务隔离快失败（per-owner × 节点语义指纹；窗口内
        # 直接终局 —— 不再消耗派发/重试预算）。
        node_fp = node.semantic_fingerprint()
        if self._quarantine().is_quarantined(owner_scope, node_fp):
            ev.status = "failed"
            ev.error_code = "POISON_QUARANTINED"
            ev.error_message = (
                "node quarantined after repeated failures "
                "(cooldown active; fails fast to protect cluster capacity)"
            )
            ev.retry_safe = False
            ev.duration_s = round(time.monotonic() - started_dj, 6)
            _emit_run_event(run.run_id, "poison_quarantined",
                            enabled=emit_events, node_id=node.node_id,
                            error_code="POISON_QUARANTINED")
            return
        attempts_allowed = node.retry.max_attempts
        last_err: Optional[GeoComputeError] = None
        for attempt in range(1, attempts_allowed + 1):
            ev.attempts = attempt
            if cancel_token is not None and cancel_token.cancelled:
                ev.status = "cancelled"
                return
            try:
                from app.services.geocompute import durable"""
assert t.count(old) == 1, "durable entry anchor"
t = t.replace(old, new, 1)

# ── 3) 投机等待替换直接 await ────────────────────────────────────────────
old = """                done = durable.await_node_job(
                    ret["job_id"],
                    session_id=session_id,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token if node.cancellable else None,
                )"""
new = """                done = self._await_with_speculative(
                    node, run, ret, session_id=session_id,
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token if node.cancellable else None,
                    owner_scope=owner_scope, budget=budget,
                    input_refs=input_refs, input_keys=input_keys,
                    resource_envelope=resource_envelope,
                    emit_events=emit_events,
                )"""
assert t.count(old) == 1, "await anchor"
t = t.replace(old, new, 1)

# ── 4) 失败尾部：非瞬态终局失败计入毒任务登记 ────────────────────────────
old = """        ev.status = "failed"
        ev.error_code = last_err.code if last_err else "NODE_FAILED"
        ev.error_message = _scrub_error_message(str(last_err) if last_err else "unknown failure")
        ev.retry_safe = getattr(last_err, "retry_safe", False)
        ev.duration_s = round(time.monotonic() - started_dj, 6)
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     policy="durable_job","""
new = """        ev.status = "failed"
        ev.error_code = last_err.code if last_err else "NODE_FAILED"
        ev.error_message = _scrub_error_message(str(last_err) if last_err else "unknown failure")
        ev.retry_safe = getattr(last_err, "retry_safe", False)
        ev.duration_s = round(time.monotonic() - started_dj, 6)
        # V8：非瞬态终局失败 → 毒任务计数（WORKER_LOSS/瞬态是 reclaim 与
        # 重试的职责，不是毒）。
        fclass_at_fail = classify_failure(last_err) if last_err else None
        if last_err is not None and fclass_at_fail not in (
            FailureClass.WORKER_LOSS, FailureClass.TRANSIENT_REMOTE,
            FailureClass.TRANSIENT_DB,
        ):
            try:
                self._quarantine().record_failure(
                    owner_scope, node_fp, run_id=run.run_id,
                    error_code=ev.error_code)
            except Exception:  # noqa: BLE001 - 登记失败不倒灌
                pass
        tracing.emit("node_failed", run_id=run.run_id, node_id=node.node_id,
                     status="failed", error_code=ev.error_code,
                     policy="durable_job","""
assert t.count(old) == 1, "durable fail tail anchor"
t = t.replace(old, new, 1)

# ── 5) 新方法：_quarantine / _await_with_speculative ────────────────────
old = """    # ------------------------------------------- V5 durable reuse helpers"""
new = '''    # --------------------------------- V8 quarantine / speculative

    @staticmethod
    def _quarantine():
        try:
            from app.services.geocompute.cluster.quarantine import (
                get_quarantine,
            )

            return get_quarantine()
        except Exception:  # noqa: BLE001 - 隔离缺席 = 放行
            from app.services.geocompute.cluster.quarantine import TaskQuarantine

            return TaskQuarantine(cooldown_s=0.0)

    def _await_with_speculative(
        self,
        node: ExecutionNode,
        run: ExecutionRun,
        primary_ret: dict[str, Any],
        *,
        session_id: Optional[str],
        deadline_ts: float,
        cancel_token: Optional[CancellationToken],
        owner_scope: str,
        budget: Any = None,
        input_refs: dict[str, str],
        input_keys: dict[str, str],
        resource_envelope: Optional[dict[str, Any]],
        emit_events: bool = False,
    ) -> dict[str, Any]:
        """durable job 等待；straggler 触发时派发**投机副本**（Phase F）。

        门控（全部满足才启用）：``WEBGIS_SPECULATIVE_AFTER_S`` > 0、节点
        deterministic 且 reuse ALLOW（幂等 —— 副本只多花算力，不产生第
        二份可见副作用）。语义：primary 等过阈值未终态 → 派副本（独立
        幂等键）→ **先到先得**（primary 优先），败者请求持久取消（late
        success 由 jobs 状态机丢弃）。事件 speculative_dispatch /
        speculative_resolved 提供诚实可见性。
        """
        from app.services.geocompute import durable
        from app.services.geocompute.errors import DeadlineExceededError

        primary_id = int(primary_ret["job_id"])
        if (
            self._speculative_after_s <= 0
            or not node.deterministic
            or node.reuse is not NodeReusePolicy.ALLOW
        ):
            return durable.await_node_job(
                primary_id, session_id=session_id or "",
                deadline_ts=deadline_ts, cancel_token=cancel_token)

        # 窗口一：primary 独自等待 after_s（窗口到点**不取消** job）
        try:
            return durable.await_node_job(
                primary_id, session_id=session_id or "",
                deadline_ts=min(deadline_ts,
                                time.monotonic() + self._speculative_after_s),
                cancel_token=cancel_token,
            )
        except DeadlineExceededError:
            pass  # primary 是 straggler（或窗口到点）→ 派投机副本
        # 投机副本：参数带 _speculative → 独立幂等键（语义指纹随参数
        # 变化 —— 副本只在等待窗口内存活，绝不被复用记录/消费）。
        spec_node = node.model_copy(update={
            "parameters": {**node.parameters, "_speculative": True},
        })
        spec_ret = durable.dispatch_node(
            spec_node,
            session_id=session_id or "",
            plan_fingerprint=run.plan_fingerprint,
            deadline_s=(deadline_ts - time.monotonic())
            if node.deadline_s is not None else None,
            budget=budget,
            run_id=run.run_id,
            node_attempt=1,
            input_refs=input_refs,
            input_keys=input_keys,
            resource_envelope=resource_envelope,
            owner_scope=owner_scope,
        )
        spec_id = int(spec_ret["job_id"])
        _emit_run_event(run.run_id, "speculative_dispatch",
                        enabled=emit_events, node_id=node.node_id,
                        status=f"after={self._speculative_after_s}s")
        tracing.emit("speculative_dispatch", run_id=run.run_id,
                     node_id=node.node_id, primary_job=primary_id,
                     speculative_job=spec_id)
        states = durable.await_node_jobs(
            [primary_id, spec_id], session_id=session_id or "",
            deadline_ts=deadline_ts, cancel_token=cancel_token,
        )
        primary_state = states.get(primary_id, {})
        spec_state = states.get(spec_id, {})
        if primary_state.get("status") == "completed":
            winner_id, payload = primary_id, primary_state.get("payload") or {}
        elif spec_state.get("status") == "completed":
            winner_id, payload = spec_id, spec_state.get("payload") or {}
        else:
            # 双输（失败/取消）→ 按 primary 的错误语义类型化上抛。
            from app.services.geocompute.errors import NodeExecutionError

            raise NodeExecutionError(
                f"speculative pair failed: primary={primary_state.get('status')} "
                f"speculative={spec_state.get('status')}",
                retry_safe=False, node_id=node.node_id,
                details={"primary": primary_state.get("error"),
                         "speculative": spec_state.get("error")},
            )
        # 败者请求持久取消（late success 由 jobs 状态机丢弃）
        loser = spec_id if winner_id == primary_id else primary_id
        try:
            from app.services.jobs import DurableJobStore

            with durable.session_factory() as db:
                DurableJobStore.request_cancel_sync(db, loser)
        except Exception:  # noqa: BLE001 - 取消失败 = 资源浪费，非正确性问题
            pass
        _emit_run_event(run.run_id, "speculative_resolved",
                        enabled=emit_events, node_id=node.node_id,
                        status="winner=primary" if winner_id == primary_id
                        else "winner=speculative")
        tracing.emit("speculative_resolved", run_id=run.run_id,
                     node_id=node.node_id, winner_job=winner_id)
        return {"payload": payload, "job_id": str(winner_id)}

    # ------------------------------------------- V5 durable reuse helpers'''
assert t.count(old) == 1, "helpers anchor"
t = t.replace(old, new, 1)

p.write_text(t, encoding="utf-8")
ast.parse(t)
print("executor quarantine+speculative wired ok")
