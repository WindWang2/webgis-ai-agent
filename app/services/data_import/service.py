"""G17 导入工作台 —— 领域服务 facade（API 路由与 Pi 工具共用的单一入口）。

浏览器与标准工具必须使用同一服务、同一导入计划、同一提交收据（任务书
专属目标）。本类是唯一编排点：
- staging 生命周期（创建/枚举/读取/过期/删除）
- 计划保存（版本 + hash + 预览失效）
- 预览与提交（同解释器；逐成员幂等）
- 取消（in-flight 成员协作取消；晚到结果不覆盖终态）
- 恢复（重启后 committing 成员复位为 planned —— 无收据即未完成）
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
        # 进程重启/崩溃恢复：本实例没有该批次在飞的提交任务时，残留
        # COMMITTING 不可能是活的 —— 无收据成员复位 planned（无收据即未完成，
        # 场景 17：晚到结果不得显示成功）。
        if (
            state.status == BatchStatus.COMMITTING
            and batch_id not in self._committing_ids
        ):
            task = self._commit_tasks.get(batch_id)
            if task is None or task.done():
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
                staging.save_state(state)
        return state

    async def get_batch(self, batch_id: str, session_id: Optional[str]) -> ImportBatchState:
        return await self._load_owned(batch_id, session_id)

    async def list_batches(self, session_id: str) -> List[Dict[str, Any]]:
        states = await asyncio.to_thread(staging.list_batch_states, session_id)
        return [staging.batch_summary(s) for s in states]

    async def delete_batch(self, batch_id: str, session_id: Optional[str]) -> bool:
        await self._load_owned(batch_id, session_id)  # 归属/过期校验（统一 NOT_FOUND）
        task = self._commit_tasks.get(batch_id)
        if task is not None and not task.done():
            raise DataImportError("COMMIT_IN_PROGRESS", "提交进行中，请先取消", status=409)
        # 删除持锁（评审 P2-9b）：与 start_commit 检查-写入互斥，防竞态逃逸
        # 成未检索任务
        async with self._lock(batch_id):
            task = self._commit_tasks.get(batch_id)
            if task is not None and not task.done():
                raise DataImportError("COMMIT_IN_PROGRESS", "提交进行中，请先取消", status=409)
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
    async def start_commit(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_keys: Optional[List[str]] = None,
        expected_plan_version: Optional[int] = None,
        *,
        background: bool = True,
    ) -> Dict[str, Any]:
        """启动提交。background=True 返回 202 语义（UI 轮询）；False 同步完成
        （Pi 工具路径，小 fixture 即时收据）。"""
        async with self._lock(batch_id):
            state = await self._load_owned(batch_id, session_id)
            if state.status == BatchStatus.COMMITTING:
                existing = self._commit_tasks.get(batch_id)
                if existing is not None and not existing.done():
                    raise DataImportError("COMMIT_IN_PROGRESS", status=409)
            if expected_plan_version is not None and state.plan_version != expected_plan_version:
                raise DataImportError(
                    "PLAN_VERSION_CONFLICT",
                    f"期望 {expected_plan_version} 实际 {state.plan_version}",
                    status=409,
                )
            targets = [m for m in state.members if m.selected or (member_keys and m.member_key in set(member_keys))]
            if member_keys is not None:
                known = {m.member_key for m in state.members}
                missing = [k for k in member_keys if k not in known]
                if missing:
                    raise DataImportError("UNKNOWN_MEMBER", ",".join(missing[:5]), status=404)
            targets = [m for m in targets if m.unsupported_reason is None]
            if not targets:
                raise DataImportError("NO_SELECTED_MEMBERS", "未选择任何可导入成员")
            unplanned = [m.member_key for m in targets if m.member_key not in state.plan_hashes]
            if unplanned:
                raise DataImportError(
                    "PLAN_REQUIRED", f"成员缺少解释计划: {', '.join(unplanned[:5])}"
                )
            # 重启恢复：任何残留 committing 状态复位（无收据即未完成）
            for m in state.members:
                if m.status == MemberStatus.COMMITTING:
                    m.status = MemberStatus.PLANNED
            state.status = BatchStatus.COMMITTING
            state.cancel_requested = False
            state.committing_since = utc_now_iso()
            state.last_commit_started_at = state.committing_since
            for m in targets:
                m.status = MemberStatus.COMMITTING
            self._committing_ids.add(batch_id)
            await asyncio.to_thread(staging.save_state, state)

        cancel_event = asyncio.Event()
        self._cancel_events[batch_id] = cancel_event
        target_keys = [m.member_key for m in targets]

        if not background:
            return await self._run_commit(batch_id, session_id, target_keys, cancel_event)

        existing = self._commit_tasks.get(batch_id)
        if existing is not None and not existing.done():
            raise DataImportError("COMMIT_IN_PROGRESS", status=409)
        self._commit_tasks[batch_id] = asyncio.create_task(
            self._run_commit(batch_id, session_id, target_keys, cancel_event)
        )
        return {
            "batch_id": batch_id,
            "status": "committing",
            "members": target_keys,
            "poll": f"/api/v1/data-import/batches/{batch_id}",
        }

    async def _run_commit(
        self,
        batch_id: str,
        session_id: Optional[str],
        member_keys: List[str],
        cancel_event: asyncio.Event,
    ) -> Dict[str, Any]:
        """逐成员串行提交（默认并发 1；取消在成员间与行循环检查点生效）。"""
        cancelled = False
        try:
            for key in member_keys:
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
                summary = staging.batch_summary(fresh)
                summary["receipts"] = {
                    k: fresh.receipts[k].model_dump()
                    for k in member_keys if k in fresh.receipts
                }
                return summary
        finally:
            self._commit_tasks.pop(batch_id, None)
            self._cancel_events.pop(batch_id, None)
            self._committing_ids.discard(batch_id)

    # ── 取消 / 收据 ───────────────────────────────────────────────────────
    async def cancel_batch(self, batch_id: str, session_id: Optional[str]) -> Dict[str, Any]:
        state = await self._load_owned(batch_id, session_id)
        event = self._cancel_events.get(batch_id)
        if event is None:
            if state.status != BatchStatus.COMMITTING:
                raise DataImportError("NOT_COMMITTING", f"当前状态 {state.status.value}", status=409)
            # 无在飞任务（锁窗口期/重启后）：落取消意图，_run_commit 终态写
            # 之前会读到 cancel_requested 并服从（评审 P2-9 竞态修复）。
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
