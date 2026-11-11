"""G17 导入工作台 —— 领域服务 facade（API 路由与 Pi 工具共用的单一入口）。

浏览器与标准工具必须使用同一服务、同一导入计划、同一提交收据（任务书
专属目标）。本类是唯一编排点：
- staging 生命周期（创建/枚举/读取/过期/删除）
- 计划保存（版本 + hash + 预览失效）
- 预览与提交（同解释器；逐成员幂等）
- 取消（in-flight 成员协作取消；晚到结果不覆盖终态）
- 恢复（重启后 committing 成员复位为 planned —— 无收据即未完成）

G23（持久任务运行基础）：提交的**在飞证据**从进程内存（`_committing_ids`）
迁至 durable job 行（analysis_tasks，ADR-0052）。进程内 dict 仅保留为本
进程 runner 的生命周期辅助；跨进程查询/取消/恢复判据一律以 DB 活跃 job
为准（G18 数据交付同款范式，见 app/services/data_operations/import_jobs.py）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_import import budgets, staging
from app.services.data_import.commit import commit_member
from app.services.data_import.discovery import DiscoveryError, discover_source
from app.services.data_import.interpretation import InterpretationError
from app.services.data_import.models import (
    BatchStatus,
    CommitReceipt,
    ImportBatchState,
    MemberRecord,
    MemberStatus,
    TableInterpretation,
    VectorInterpretation,
    compute_plan_hash,
    utc_now_iso,
)
from app.services.data_import.preview import build_preview

logger = logging.getLogger(__name__)

# 预算类发现失败：有界拒绝（整个批次拒绝 + staging 清理），不降级为注记
_BOUNDED_DISCOVERY_FAILURES = {
    "TOO_MANY_ZIP_ENTRIES",
    "TOO_MANY_LAYERS",
    "TOO_MANY_SHEETS",
    "ZIP_EXPANSION_TOO_LARGE",
    "ZIP_MEMBER_TOO_LARGE",
}


class DataImportError(Exception):
    """领域错误（code 稳定；API 层映射 HTTP，工具层映射 typed failure）。"""

    def __init__(self, code: str, detail: str = "", status: int = 400) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)[:300]
        self.status = status


def _interpretation_model(kind: str, payload: Dict[str, Any]):
    try:
        if kind == "vector":
            return VectorInterpretation.model_validate(payload)
        return TableInterpretation.model_validate(payload)
    except Exception as e:  # noqa: BLE001 — pydantic 校验错误 → typed
        raise DataImportError("BAD_INTERPRETATION", str(e)[:200])


def _job_active_by_id(job_id: int) -> Optional[Dict[str, Any]]:
    """按 job id 查活跃性（短同步事务；终态/不存在返回 None）。"""
    from app.services.data_operations.runner import _sync_session

    with _sync_session() as db:
        from app.models.db_model import AnalysisTask

        row = db.get(AnalysisTask, int(job_id))
        if row is None:
            db.commit()
            return None
        status = str(row.status)
        db.commit()
    if status in ("completed", "failed", "cancelled", "stale"):
        return None
    return {"job_id": int(job_id), "status": status}


class DataImportService:
    """每 app 单例足够（锁与取消注册表进程内）。"""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}   # batch_id → commit cancel
        self._commit_tasks: Dict[str, asyncio.Task] = {}
        self._committing_ids: set = set()   # 本实例在提交中的批次（重启后为空 → 状态归一化）

    # ── 锁 ────────────────────────────────────────────────────────────────
    def _lock(self, batch_id: str) -> asyncio.Lock:
        return self._locks.setdefault(batch_id, asyncio.Lock())

    # ── 批次创建 / 读取 ───────────────────────────────────────────────────
    async def create_batch(
        self,
        session_id: str,
        user_fingerprint: str,
        files: List[Tuple[str, bytes]],
        *,
        license: str = "",
        attribution: str = "",
    ) -> ImportBatchState:
        """staging + 成员枚举（阻塞部分卸载线程）。"""
        try:
            state = await asyncio.to_thread(
                staging.stage_files, session_id, user_fingerprint, files
            )
        except staging.StagingError as e:
            raise DataImportError(e.code, e.detail, status=413 if "LARGE" in e.code else 400)
        state.license = (license or "")[:200]
        state.attribution = (attribution or "")[:500]
        root = staging.batch_root(state.batch_id)
        extract_root = root / "sources" / "extract"
        members: List[MemberRecord] = []
        expanded_total = 0
        for src in state.sources:
            path = staging.source_path(state, src.source_id)
            try:
                ms, notes = await asyncio.to_thread(
                    discover_source, src, path, extract_root
                )
                # 批次级展开预算（评审 P2-8）：按本源解压后累计，超限整批拒绝
                src_extract = extract_root / src.source_id
                if src_extract.exists():
                    expanded_total += sum(
                        p.stat().st_size for p in src_extract.rglob("*") if p.is_file()
                    )
                    if expanded_total > budgets.MAX_BATCH_EXPANDED_BYTES:
                        raise DiscoveryError(
                            "ZIP_EXPANSION_TOO_LARGE",
                            f"批次展开合计 {expanded_total} 字节超过预算 "
                            f"{budgets.MAX_BATCH_EXPANDED_BYTES}",
                        )
            except DiscoveryError as e:
                if e.code in _BOUNDED_DISCOVERY_FAILURES:
                    # 预算类超限：有界拒绝整个批次并清理（场景 20），不静默降级
                    await asyncio.to_thread(staging.delete_batch, state.batch_id)
                    raise DataImportError(e.code, e.detail, status=413) from e
                state.notes.append(f"{src.original_name}: 发现失败（{e.code}: {e.detail}）")
                continue
            members.extend(ms)
            state.notes.extend(notes[:16])
        state.members = members
        state.status = BatchStatus.INSPECTED
        state.notes = state.notes[-32:]
        await asyncio.to_thread(staging.save_state, state)
        return state

    async def _load_owned(self, batch_id: str, session_id: Optional[str]) -> ImportBatchState:
        state = staging.load_state(batch_id)
        if state is None:
            raise DataImportError("NOT_FOUND", "导入批次不存在", status=404)
        try:
            staging.assert_owner(state, session_id)
        except staging.StagingError as e:
            raise DataImportError(e.code, e.detail, status=404)
        if staging.is_expired(state):
            await asyncio.get_running_loop().run_in_executor(
                None, staging.expire_batch, batch_id
            )
            raise DataImportError("EXPIRED", "staging 已过期；请重新上传", status=410)
        # 进程重启/崩溃恢复（G23 durable 化）：
        # 「COMMITTING 是否残留」的判据是 **durable job 活跃性**，不再是本进程
        # 内存（_committing_ids 只见本副本）。存在活跃 job（pending/queued/
        # running/cancelling，可能在另一 API 副本上推进）→ 保持状态不动；
        # 没有活跃 job → 无收据成员复位 planned（无收据即未完成，场景 17：
        # 晚到结果不得显示成功）。旧格式批次（active_job_id=None 且无 job 行）
        # 走同一归一化 —— 与 G17 行为兼容。
        if state.status == BatchStatus.COMMITTING:
            active = self._active_commit_job(state)
            if active is not None:
                # 另一副本（或本副本 runner 与内存登记之间的窗口）在推进：
                # 绝不复位。顺带把 job_id 补记进状态（老文件/竞态兜底）。
                if state.active_job_id != active["job_id"]:
                    state.active_job_id = active["job_id"]
                    await asyncio.get_running_loop().run_in_executor(
                        None, staging.save_state, state
                    )
                return state
            if batch_id in self._committing_ids:
                # 无 durable job（DB 不可达窗口）但本进程确有在飞 runner：
                # 不复位 —— 宁可保守等待 stale 清扫，也不误杀本进程活任务。
                return state
            for m in state.members:
                if m.status == MemberStatus.COMMITTING:
                    m.status = (
                        MemberStatus.COMPLETED
                        if m.member_key in state.receipts
                        and state.receipts[m.member_key].status == "completed"
                        else MemberStatus.PLANNED
                        if m.member_key in state.plan_hashes
                        else MemberStatus.DISCOVERED
                    )
            planned_keys = set(state.plan_hashes)
            completed_keys = {
                k for k, r in state.receipts.items() if r.status == "completed"
            }
            if state.cancel_requested:
                # 取消意图跨重启保留（复审 P3）：复活为 cancelled 而非
                # configured/partial（无虚假成功，意图也不丢）。
                state.status = BatchStatus.CANCELLED
                for m in state.members:
                    if m.status == MemberStatus.COMMITTING:
                        m.status = MemberStatus.CANCELLED
            elif planned_keys and planned_keys <= completed_keys:
                state.status = BatchStatus.COMPLETED
            elif completed_keys:
                state.status = BatchStatus.PARTIAL
            else:
                state.status = BatchStatus.CONFIGURED
            state.committing_since = None
            state.active_job_id = None
            staging.save_state(state)
        return state

    def _active_commit_job(self, state: ImportBatchState) -> Optional[Dict[str, Any]]:
        """查该批次的活跃 durable job（短同步事务）。

        优先按 active_job_id 精确查；为空或已终态时按 (task_type, session,
        batch_id) 扫描兜底 —— 覆盖状态文件丢失 job_id、跨副本提交等窗口。
        DB 异常按「无活跃 job」处理会误复位，故按「可能有」处理（保守侧）：
        由 stale 清扫（心跳超时）最终收敛。
        """
        from app.services.data_operations.import_jobs import find_active_import_job

        try:
            if state.active_job_id is not None:
                hit = _job_active_by_id(state.active_job_id)
                if hit is not None:
                    return hit
            return find_active_import_job(state.session_id, state.batch_id)
        except Exception:  # noqa: BLE001 — DB 不可达：保守认为在飞（不误杀）
            logger.warning(
                "active-job probe failed for batch %s; assuming active",
                state.batch_id,
                exc_info=True,
            )
            return {"job_id": state.active_job_id or 0, "status": "unknown"}

    async def get_batch(self, batch_id: str, session_id: Optional[str]) -> ImportBatchState:
        return await self._load_owned(batch_id, session_id)

    async def list_batches(self, session_id: str) -> List[Dict[str, Any]]:
        states = await asyncio.to_thread(staging.list_batch_states, session_id)
        return [staging.batch_summary(s) for s in states]

    async def delete_batch(self, batch_id: str, session_id: Optional[str]) -> bool:
        state = await self._load_owned(batch_id, session_id)  # 归属/过期校验（统一 NOT_FOUND）
        task = self._commit_tasks.get(batch_id)
        if task is not None and not task.done():
            raise DataImportError("COMMIT_IN_PROGRESS", "提交进行中，请先取消", status=409)
        # G23：durable 在飞检查 —— 活跃 job 可能在另一副本推进，本地 task
        # 表看不见（A03 同源风险）；rmtree 掉 staging 会让执行副本读不到源。
        if state.status == BatchStatus.COMMITTING:
            active = self._active_commit_job(state)
            if active is not None:
                raise DataImportError(
                    "COMMIT_IN_PROGRESS",
                    f"提交进行中（job {active['job_id']}），请先取消", status=409,
                )
        # 删除持锁（评审 P2-9b）：与 start_commit 检查-写入互斥，防竞态逃逸
        # 成未检索任务
        async with self._lock(batch_id):
            task = self._commit_tasks.get(batch_id)
            if task is not None and not task.done():
                raise DataImportError("COMMIT_IN_PROGRESS", "提交进行中，请先取消", status=409)
            if state.status == BatchStatus.COMMITTING:
                active = self._active_commit_job(state)
                if active is not None:
                    raise DataImportError(
                        "COMMIT_IN_PROGRESS",
                        f"提交进行中（job {active['job_id']}），请先取消", status=409,
                    )
            ok = await asyncio.to_thread(staging.delete_batch, batch_id)
        self._cancel_events.pop(batch_id, None)
        self._commit_tasks.pop(batch_id, None)
        self._locks.pop(batch_id, None)
        return ok

    # ── 计划 ──────────────────────────────────────────────────────────────
    async def save_plan(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_configs: Dict[str, Dict[str, Any]],
        selected: Optional[List[str]] = None,
        expected_plan_version: Optional[int] = None,
    ) -> ImportBatchState:
        async with self._lock(batch_id):
            state = await self._load_owned(batch_id, session_id)
            if state.status == BatchStatus.COMMITTING:
                raise DataImportError("COMMIT_IN_PROGRESS", status=409)
            if expected_plan_version is not None and state.plan_version != expected_plan_version:
                raise DataImportError(
                    "PLAN_VERSION_CONFLICT",
                    f"期望 {expected_plan_version} 实际 {state.plan_version}",
                    status=409,
                )
            by_key = {m.member_key: m for m in state.members}
            for key, payload in member_configs.items():
                member = by_key.get(key)
                if member is None:
                    raise DataImportError("UNKNOWN_MEMBER", key, status=404)
                if member.unsupported_reason:
                    raise DataImportError("MEMBER_UNSUPPORTED", f"{key}: {member.unsupported_reason}")
                kind = "vector" if member.kind.value == "vector" else "table"
                interp = _interpretation_model(kind, {"kind": kind, **(payload or {})})
                # 列引用校验（points/wkt 模式在此 fail-fast）
                _validate_columns(member, interp)
                state.interpretations[key] = interp.model_dump()
                source = next(s for s in state.sources if s.source_id == member.source_id)
                state.plan_hashes[key] = compute_plan_hash(
                    source.sha256, key, state.interpretations[key]
                )
                member.status = MemberStatus.PLANNED
                # 配置变更 → 旧预览失效（合同：改 X/Y/CRS 后旧预览不可用）
                state.previewed_hashes.pop(key, None)
            if selected is not None:
                unknown = [k for k in selected if k not in by_key]
                if unknown:
                    raise DataImportError("UNKNOWN_MEMBER", ",".join(unknown[:5]), status=404)
                for m in state.members:
                    m.selected = m.member_key in set(selected)
                # 勾选即确认默认解释（save_plan 是显式确认点）：未显式配置的
                # 勾选成员落默认解释（vector 默认 / table auto），并铸 plan_hash。
                for m in state.members:
                    if m.selected and not m.unsupported_reason and m.member_key not in state.interpretations:
                        interp = (
                            VectorInterpretation()
                            if m.kind.value == "vector"
                            else TableInterpretation()
                        )
                        state.interpretations[m.member_key] = interp.model_dump()
                        src = next(s for s in state.sources if s.source_id == m.source_id)
                        state.plan_hashes[m.member_key] = compute_plan_hash(
                            src.sha256, m.member_key, state.interpretations[m.member_key]
                        )
                        m.status = MemberStatus.PLANNED
                        state.previewed_hashes.pop(m.member_key, None)
            if state.plan_version == 0 or member_configs:
                state.plan_version += 1
            if state.status in (BatchStatus.STAGED, BatchStatus.INSPECTED, BatchStatus.PREVIEWED):
                state.status = BatchStatus.CONFIGURED
            if state.status in (BatchStatus.PARTIAL, BatchStatus.FAILED, BatchStatus.CANCELLED):
                # 修正失败成员后重试：回到 configured（成功成员收据保留）
                state.status = BatchStatus.CONFIGURED
            await asyncio.to_thread(staging.save_state, state)
            return state

    # ── 预览 ──────────────────────────────────────────────────────────────
    async def preview_members(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_keys: Optional[List[str]] = None,
        row_limit: int = budgets.PREVIEW_DEFAULT_ROWS,
    ) -> Dict[str, Any]:
        async with self._lock(batch_id):
            state = await self._load_owned(batch_id, session_id)
            if state.status == BatchStatus.COMMITTING:
                raise DataImportError("COMMIT_IN_PROGRESS", status=409)
        targets = [
            m for m in state.members
            if (member_keys is None or m.member_key in set(member_keys))
            and m.unsupported_reason is None
        ]
        if member_keys is not None:
            known = {m.member_key for m in state.members}
            missing = [k for k in member_keys if k not in known]
            if missing:
                raise DataImportError("UNKNOWN_MEMBER", ",".join(missing[:5]), status=404)
        limit = max(1, min(int(row_limit), budgets.PREVIEW_MAX_ROWS))
        out: Dict[str, Any] = {"batch_id": batch_id, "plan_version": state.plan_version, "previews": []}
        for member in targets:
            interp_payload = state.interpretations.get(member.member_key)
            interp = (
                _interpretation_model("vector" if member.kind.value == "vector" else "table", interp_payload)
                if interp_payload
                else None
            )
            source = next(s for s in state.sources if s.source_id == member.source_id)
            path = staging.source_path(state, member.source_id)
            extract_root = staging.batch_root(batch_id) / "sources" / "extract"
            try:
                payload = await asyncio.to_thread(
                    build_preview,
                    member=member,
                    source=source,
                    source_path=path,
                    interpretation=interp,
                    row_limit=limit,
                    extract_root=extract_root,
                )
                payload["plan_hash"] = state.plan_hashes.get(member.member_key, "")
                out["previews"].append(payload)
                async with self._lock(batch_id):
                    fresh = await self._load_owned(batch_id, session_id)
                    fm = next((x for x in fresh.members if x.member_key == member.member_key), None)
                    if fm is not None and fm.status not in (MemberStatus.COMPLETED,):
                        fm.status = MemberStatus.PREVIEWED
                        if member.member_key in fresh.plan_hashes:
                            fresh.previewed_hashes[member.member_key] = fresh.plan_hashes[member.member_key]
                        if fresh.status in (BatchStatus.INSPECTED, BatchStatus.CONFIGURED):
                            fresh.status = BatchStatus.PREVIEWED
                        await asyncio.to_thread(staging.save_state, fresh)
            except InterpretationError as e:
                out["previews"].append(
                    {"member_key": member.member_key, "error": {"code": e.code, "message": e.detail}}
                )
        return out

    # ── 提交 ──────────────────────────────────────────────────────────────
    def _conflict(self, batch_id: str) -> DataImportError:
        return DataImportError(
            "COMMIT_IN_PROGRESS", f"批次 {batch_id} 提交进行中（durable job 在飞）",
            status=409,
        )

    async def start_commit(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_keys: Optional[List[str]] = None,
        expected_plan_version: Optional[int] = None,
        *,
        background: bool = True,
        actor: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """启动提交（G23 durable 化）。

        background=True 返回 202 语义（UI 轮询批次/任务中心）；False 同步
        等待完成（小 fixture 即时收据）。两条路径内部都走 durable job ——
        同一 job、同一收据、同一取消语义。重复提交（同 owner、同计划版本、
        同成员计划指纹）幂等复用既有 job。
        """
        from app.services.data_operations.import_jobs import (
            JobActor,
            submit_import_commit,
        )

        job_actor: JobActor = actor or JobActor()
        return await submit_import_commit(
            self, batch_id, session_id, member_keys, expected_plan_version,
            actor=job_actor, background=background,
        )

    async def _run_commit_members(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_keys: List[str],
        cancel_event: asyncio.Event,
        handle: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """逐成员串行提交（默认并发 1；取消在成员间与行循环检查点生效）。

        G23：``handle``（durable job 句柄）在场时，每个成员发布前做强制
        取消复核（ensure_not_cancelled），每成员完成即写 job 进度；返回
        有界批次结果摘要（job result_summary）。
        """
        cancelled = False
        try:
            total = len(member_keys)
            for idx, key in enumerate(member_keys):
                if cancel_event.is_set():
                    cancelled = True
                    break
                async with self._lock(batch_id):
                    state = await self._load_owned(batch_id, session_id)
                    member = next((m for m in state.members if m.member_key == key), None)
                    if member is None:
                        continue
                    if member.status == MemberStatus.COMPLETED:
                        continue
                    source = next(
                        (s for s in state.sources if s.source_id == member.source_id), None
                    )
                    if source is None:
                        continue
                    plan_hash = state.plan_hashes.get(key, "")
                    # 计划一致性防线：提交使用的 hash 必须等于当前状态事实源
                    #（preview 与 commit 同一解释器 + 同一 hash；防外发篡改/陈旧值）
                    if not plan_hash or key not in state.interpretations:
                        state.receipts[key] = CommitReceipt(
                            member_key=key, plan_hash=plan_hash or "", status="failed",
                            error_code="PLAN_REQUIRED", error_message="成员缺少计划",
                        )
                        await asyncio.to_thread(staging.save_state, state)
                        continue
                    # 计划一致性防线：存储 hash 只是缓存 —— 提交时按
                    #（源 sha, 成员, 当前解释）重新推导并比对；不一致即状态
                    #被外发篡改/损坏，拒绝提交（故障注入反验 2）。
                    src = next(
                        (s0 for s0 in state.sources if s0.source_id == member.source_id), None
                    )
                    recomputed = compute_plan_hash(
                        src.sha256, key, state.interpretations[key]
                    ) if src else ""
                    if recomputed != plan_hash:
                        state.receipts[key] = CommitReceipt(
                            member_key=key, plan_hash=plan_hash, status="failed",
                            error_code="PLAN_HASH_MISMATCH",
                            error_message="计划 hash 与当前解释不一致（状态可能被篡改）",
                        )
                        await asyncio.to_thread(staging.save_state, state)
                        continue
                    interp_payload = state.interpretations.get(key)
                    interp = (
                        _interpretation_model(
                            "vector" if member.kind.value == "vector" else "table", interp_payload
                        )
                        if interp_payload
                        else None
                    )
                # 不可逆副作用（摄入/store/收据发布）前的 durable 取消复核：
                # 跨副本取消经 DB 轮询点燃本进程 token/event（A07）。
                if handle is not None:
                    await handle.ensure_not_cancelled()
                try:
                    receipt = await commit_member(
                        state, member, source, interp, plan_hash, cancel_event=cancel_event
                    )
                    status_out = receipt.status
                except Exception as e:  # noqa: BLE001 — 取消/未预期：按成员失败处理
                    from app.lib.cancellation import OperationCancelled

                    if isinstance(e, OperationCancelled) or cancel_event.is_set():
                        cancelled = True
                        receipt = CommitReceipt(
                            member_key=key, plan_hash=plan_hash, status="cancelled",
                            error_code="CANCELLED", error_message="用户取消",
                        )
                        status_out = "cancelled"
                    else:
                        logger.exception("commit member %s crashed", key)
                        receipt = CommitReceipt(
                            member_key=key, plan_hash=plan_hash, status="failed",
                            error_code="COMMIT_CRASHED", error_message=str(e)[:200],
                        )
                        status_out = "failed"
                # 收据落账（重新加载防并发写覆盖）
                async with self._lock(batch_id):
                    fresh = await self._load_owned(batch_id, session_id)
                    fm = next((m for m in fresh.members if m.member_key == key), None)
                    if fm is not None:
                        fm.status = (
                            MemberStatus.COMPLETED if status_out == "completed"
                            else MemberStatus.CANCELLED if status_out == "cancelled"
                            else MemberStatus.FAILED
                        )
                    fresh.receipts[key] = receipt
                    fresh.diagnostics.update(state.diagnostics)
                    await asyncio.to_thread(staging.save_state, fresh)
                # job 进度：已处理成员数 / 总数（真值，不伪造百分比）
                if handle is not None:
                    await handle.progress(
                        phase="commit",
                        message=f"{key}: {status_out}",
                        current_step=idx + 1,
                        total_steps=total,
                    )
            # 批次终态
            async with self._lock(batch_id):
                fresh = await self._load_owned(batch_id, session_id)
                if fresh.cancel_requested or (cancelled and cancel_event.is_set()):
                    fresh.status = BatchStatus.CANCELLED
                    for m in fresh.members:
                        if m.status == MemberStatus.COMMITTING:
                            m.status = MemberStatus.CANCELLED
                else:
                    done = [r for k, r in fresh.receipts.items() if k in set(member_keys)]
                    completed = sum(1 for r in done if r.status == "completed")
                    if completed == len(member_keys):
                        fresh.status = BatchStatus.COMPLETED
                    elif completed > 0:
                        fresh.status = BatchStatus.PARTIAL
                    else:
                        fresh.status = BatchStatus.FAILED
                fresh.committing_since = None
                await asyncio.to_thread(staging.save_state, fresh)
            from app.services.data_operations.import_jobs import batch_outcome_summary

            summary = batch_outcome_summary(fresh, member_keys)
            # 取消语义（A07/A08）：批次终态已如实落盘（cancelled/partial），
            # 再让 job 走取消终态 —— run_async_job 把 OperationCancelled
            # 收敛为 cancelled，绝不允许迟到完成改写。
            if cancelled and cancel_event.is_set() and handle is not None:
                handle.checkpoint()  # raises OperationCancelled
            return summary
        finally:
            self._commit_tasks.pop(batch_id, None)
            self._cancel_events.pop(batch_id, None)
            self._committing_ids.discard(batch_id)

    # ── 取消 / 收据 ───────────────────────────────────────────────────────
    async def cancel_batch(self, batch_id: str, session_id: Optional[str]) -> Dict[str, Any]:
        """取消提交（G23 durable 化）：任何 API 副本发出都生效。

        语义矩阵（任务书 P4 / A07-A09）：
        * 活跃 job 在飞 → DB 落 ``cancel_requested_at``（持久事实）+ 本进程
          event/token 即时联动（本副本时）；执行副本的看门狗 ≤ 轮询周期感知，
          在下一成员边界/行检查点收敛，迟到完成不改写终态。
        * job 已 completed → 完成后取消：状态保持 completed（终态不可迁），
          如实返回 ``already_completed``（产物 retained 披露见收据）。
        * 无 job（旧格式批次/已归一化）→ 沿用 G17 文件路径语义。
        """
        state = await self._load_owned(batch_id, session_id)
        active = self._active_commit_job(state) if state.status == BatchStatus.COMMITTING else None
        if active is not None and state.active_job_id:
            from app.core.database import async_db_session
            from app.services.jobs.store import DurableJobStore

            job_id = int(state.active_job_id)
            async with async_db_session() as db:
                changed, current = await DurableJobStore.request_cancel(db, job_id)
                await db.commit()
            if current.value == "completed":
                return {
                    "batch_id": batch_id,
                    "status": "completed",
                    "cancel_requested": False,
                    "message": "提交已完成，取消不生效（终态不可变；产物见收据）",
                }
            # 取消意图同时落批次文件（本进程无 runner 时仍是跨重启证据）
            async with self._lock(batch_id):
                fresh = await self._load_owned(batch_id, session_id)
                fresh.cancel_requested = True
                await asyncio.to_thread(staging.save_state, fresh)
            # 本副本有 runner：即时联动 + 有界等待确认
            event = self._cancel_events.get(batch_id)
            if event is not None:
                event.set()
            task = self._commit_tasks.get(batch_id)
            final_status = "cancelling"
            if task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=30)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            # 回报真实终态（本副本 runner 已确认时为 cancelled；跨副本执行
            # 时诚实停留在 cancelling —— 状态以任务中心/批次详情为准）
            fresh = await self._load_owned(batch_id, session_id)
            if fresh.status != BatchStatus.COMMITTING:
                final_status = fresh.status.value
            return {
                "batch_id": batch_id,
                "job_id": str(job_id),
                "status": final_status,
                "cancel_requested": changed,
            }
        # ── 无活跃 durable job：G17 兼容路径 ──
        event = self._cancel_events.get(batch_id)
        if event is None:
            if state.status != BatchStatus.COMMITTING:
                raise DataImportError("NOT_COMMITTING", f"当前状态 {state.status.value}", status=409)
            # 无在飞任务（锁窗口期/重启后）：落取消意图，终态写之前会读到
            # cancel_requested 并服从（评审 P2-9 竞态修复）。
            async with self._lock(batch_id):
                fresh = await self._load_owned(batch_id, session_id)
                fresh.cancel_requested = True
                fresh.status = BatchStatus.CANCELLED
                for m in fresh.members:
                    if m.status == MemberStatus.COMMITTING:
                        m.status = MemberStatus.CANCELLED
                await asyncio.to_thread(staging.save_state, fresh)
            return {"batch_id": batch_id, "status": "cancelled"}
        # 在飞任务：先落意图（防晚到终态写覆盖），再协作取消
        async with self._lock(batch_id):
            fresh = await self._load_owned(batch_id, session_id)
            fresh.cancel_requested = True
            await asyncio.to_thread(staging.save_state, fresh)
        event.set()
        task = self._commit_tasks.get(batch_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return {"batch_id": batch_id, "status": "cancelled"}

    async def get_receipts(self, batch_id: str, session_id: Optional[str]) -> Dict[str, Any]:
        state = await self._load_owned(batch_id, session_id)
        return {
            "batch_id": batch_id,
            "status": state.status.value,
            "plan_version": state.plan_version,
            "receipts": {k: r.model_dump() for k, r in state.receipts.items()},
            "notes": state.notes[-16:],
        }


def _validate_columns(member: MemberRecord, interp: Any) -> None:
    """列引用在计划保存时 fail-fast（不等到提交）。"""
    from app.services.data_import.models import TableInterpretation, VectorInterpretation

    if isinstance(interp, VectorInterpretation):
        if interp.keep_fields and member.fields:
            missing = [c for c in interp.keep_fields if c not in set(member.fields)]
            if missing:
                hint = ""
                if member.fields_total and member.fields_total > len(member.fields):
                    hint = f"（发现期清单有界展示 {len(member.fields)}/{member.fields_total}，可能因此误判）"
                raise DataImportError(
                    "COLUMN_NOT_FOUND",
                    f"字段不存在: {', '.join(missing)}{hint}；可用: {', '.join(member.fields[:20])}",
                )
        return
    if not isinstance(interp, TableInterpretation):
        return
    mode = interp.mode
    columns = set(member.fields)
    if mode == "points":
        for c in (interp.x_column, interp.y_column):
            if c and columns and c not in columns:
                raise DataImportError("COLUMN_NOT_FOUND", f"{c} 不在成员字段中")
    elif mode == "wkt":
        if interp.wkt_column and columns and interp.wkt_column not in columns:
            raise DataImportError("COLUMN_NOT_FOUND", f"{interp.wkt_column} 不在成员字段中")


# ── 单例 ───────────────────────────────────────────────────────────────────
_service: Optional[DataImportService] = None


def get_data_import_service() -> DataImportService:
    global _service
    if _service is None:
        _service = DataImportService()
    return _service
