"""G44 合同持久层 —— 版本化合同行 + 评估收据（SessionLocal，CAS 语义）。

并发纪律：
- 行内容（rules/digest/revision）创建后不可变；修订 = 新行；
- 状态迁移走条件 UPDATE（WHERE status=期望旧值）—— DB 级 CAS，返回受影响行数；
- active 唯一性由部分唯一索引兜底（同绑定第二行 active 直接 IntegrityError）；
- sqlite 开发/测试 lane：评估 job（后台线程）与请求线程并发提交时按
  ``database is locked`` 有界重试（Postgres 行级锁不受影响；重试只在锁冲突
  上生效，绝不吞其它错误）。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app.models.field_contracts import FieldContract, FieldContractAssessment
from app.services.field_contracts.contracts import MAX_CONTRACTS_PER_BINDING

__all__ = [
    "ContractStoreError",
    "FieldContractStore",
    "field_contract_store",
]

_T = TypeVar("_T")

def _sqlite_busy_timeout(db: Any) -> None:
    """sqlite lane：给本连接设 busy_timeout（并发写等待而非立即 locked）。

    Postgres 上 PRAGMA 语句会报语法错误 —— 捕获并忽略（无副作用）。
    """
    try:
        from sqlalchemy import text

        db.execute(text("PRAGMA busy_timeout=8000"))
    except Exception:  # noqa: BLE001 — 非 sqlite 方言：无操作
        pass

_SQLITE_LOCK_RETRIES = 6
_SQLITE_LOCK_BACKOFF_S = 0.25


def _retry_locked(fn: Callable[[], _T]) -> _T:
    """sqlite `database is locked` 有界重试（其它异常原样抛出）。"""
    last: Optional[BaseException] = None
    for attempt in range(_SQLITE_LOCK_RETRIES):
        try:
            return fn()
        except OperationalError as exc:
            msg = str(exc).lower()
            if "database is locked" not in msg and "database table is locked" not in msg:
                raise
            last = exc
            time.sleep(_SQLITE_LOCK_BACKOFF_S * (attempt + 1))
    assert last is not None
    raise last


class ContractStoreError(RuntimeError):
    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FieldContractStore:
    """同步 DB 操作集合（async 包装在 service/enforcement 层）。"""

    # ── 合同行 ──────────────────────────────────────────────────────────

    def create_contract(
        self,
        *,
        contract_key: str,
        session_id: str,
        ref_id: str,
        project_id: Optional[str],
        created_by: Optional[str],
        title: str,
        rules: Dict[str, Any],
        normalized_digest: str,
        revision: int,
        status: str = "draft",
        notes: Optional[str] = None,
        binding: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            row = FieldContract(
                contract_key=contract_key,
                session_id=session_id,
                ref_id=ref_id,
                project_id=project_id,
                created_by=created_by,
                title=title,
                status=status,
                revision=revision,
                rules=rules,
                normalized_digest=normalized_digest,
                binding=binding or {},
                notes=notes,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            self._prune_history_sync(db, session_id, ref_id)
            return _contract_row_to_dict(row)

    def get_contract(self, contract_id: str) -> Optional[Dict[str, Any]]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            row = db.get(FieldContract, contract_id)
            return _contract_row_to_dict(row) if row else None

    def list_contracts(self, session_id: str, ref_id: Optional[str] = None) -> List[Dict[str, Any]]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            stmt = (
                select(FieldContract)
                .where(FieldContract.session_id == session_id)
                .order_by(FieldContract.created_at.desc(), FieldContract.id.desc())
                .limit(200)
            )
            if ref_id:
                stmt = stmt.where(FieldContract.ref_id == ref_id)
            rows = db.execute(stmt).scalars().all()
            return [_contract_row_to_dict(r) for r in rows]

    def get_active_contract(self, session_id: str, ref_id: str) -> Optional[Dict[str, Any]]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            stmt = (
                select(FieldContract)
                .where(
                    FieldContract.session_id == session_id,
                    FieldContract.ref_id == ref_id,
                    FieldContract.status == "active",
                )
                .order_by(FieldContract.revision.desc())
                .limit(1)
            )
            row = db.execute(stmt).scalars().first()
            return _contract_row_to_dict(row) if row else None

    def latest_for_binding(self, session_id: str, ref_id: str) -> Optional[Dict[str, Any]]:
        """绑定上最新一版（任意状态）—— 工作台「当前草稿/活跃」视图。"""
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            stmt = (
                select(FieldContract)
                .where(
                    FieldContract.session_id == session_id,
                    FieldContract.ref_id == ref_id,
                    FieldContract.status != "superseded",
                )
                .order_by(FieldContract.revision.desc())
                .limit(1)
            )
            row = db.execute(stmt).scalars().first()
            return _contract_row_to_dict(row) if row else None

    def transition_status(
        self,
        contract_id: str,
        *,
        from_status: tuple[str, ...],
        to_status: str,
        expected_digest: Optional[str] = None,
        extra_activation: Optional[Dict[str, Any]] = None,
        extra_binding: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """DB 级 CAS 状态迁移；返回迁移后行（None = 条件不满足）。

        R1-P0：expected_digest 非空时同为 CAS 条件 —— 状态与内容摘要都与
        期望一致才迁移（防「选收据 → 修订 → 迁移」窗口内未审计内容上膛）。
        """
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            row = db.get(FieldContract, contract_id)
            if row is None or row.status not in from_status:
                return None
            if expected_digest is not None and str(row.normalized_digest) != str(expected_digest):
                return None
            row.status = to_status
            if extra_activation is not None:
                row.activation = extra_activation
            if extra_binding is not None:
                row.binding = {**(row.binding or {}), **extra_binding}
            row.updated_at = _now()
            db.commit()
            db.refresh(row)
            return _contract_row_to_dict(row)

    def retire_other_active(self, session_id: str, ref_id: str, keep_id: str) -> int:
        """激活新行前，把同绑定其它 active 行置 superseded（历史保留）。"""
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            stmt = (
                update(FieldContract)
                .where(
                    FieldContract.session_id == session_id,
                    FieldContract.ref_id == ref_id,
                    FieldContract.status == "active",
                    FieldContract.id != keep_id,
                )
                .values(status="superseded", updated_at=_now())
            )
            result = db.execute(stmt)
            db.commit()
            return int(result.rowcount or 0)

    def _prune_history_sync(self, db: Any, session_id: str, ref_id: str) -> None:
        """有界历史政策：保留最新 N 行（含 active），更旧的 superseded/retired 删除。"""
        stmt = (
            select(FieldContract)
            .where(FieldContract.session_id == session_id, FieldContract.ref_id == ref_id)
            .order_by(FieldContract.revision.desc())
        )
        rows = list(db.execute(stmt).scalars().all())
        if len(rows) <= MAX_CONTRACTS_PER_BINDING:
            return
        for stale in rows[MAX_CONTRACTS_PER_BINDING:]:
            if stale.status in ("superseded", "retired"):
                db.delete(stale)
        db.commit()

    # ── 评估收据 ────────────────────────────────────────────────────────

    def create_assessment(self, **cols: Any) -> Dict[str, Any]:
        from app.core.database import SessionLocal

        def _op() -> Dict[str, Any]:
            with SessionLocal() as db:
                _sqlite_busy_timeout(db)
                row = FieldContractAssessment(**cols)
                db.add(row)
                db.commit()
                db.refresh(row)
                return _assessment_row_to_dict(row)

        return _retry_locked(_op)

    def get_assessment(self, assessment_id: str) -> Optional[Dict[str, Any]]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            row = db.get(FieldContractAssessment, assessment_id)
            return _assessment_row_to_dict(row) if row else None

    def list_assessments(
        self, contract_id: str, *, limit: int = 20
    ) -> List[Dict[str, Any]]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _sqlite_busy_timeout(db)
            stmt = (
                select(FieldContractAssessment)
                .where(FieldContractAssessment.contract_id == contract_id)
                .order_by(FieldContractAssessment.created_at.desc())
                .limit(limit)
            )
            rows = db.execute(stmt).scalars().all()
            return [_assessment_row_to_dict(r) for r in rows]

    def update_assessment(
        self, assessment_id: str, **cols: Any
    ) -> Optional[Dict[str, Any]]:
        from app.core.database import SessionLocal

        def _op() -> Optional[Dict[str, Any]]:
            with SessionLocal() as db:
                _sqlite_busy_timeout(db)
                row = db.get(FieldContractAssessment, assessment_id)
                if row is None:
                    return None
                for k, v in cols.items():
                    setattr(row, k, v)
                row.updated_at = _now()
                db.commit()
                db.refresh(row)
                return _assessment_row_to_dict(row)

        return _retry_locked(_op)


def _contract_row_to_dict(row: FieldContract) -> Dict[str, Any]:
    return {
        "id": row.id,
        "contract_key": row.contract_key,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "ref_id": row.ref_id,
        "created_by": row.created_by,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "rules": row.rules,
        "normalized_digest": row.normalized_digest,
        "binding": row.binding or {},
        "activation": row.activation,
        "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _assessment_row_to_dict(row: FieldContractAssessment) -> Dict[str, Any]:
    return {
        "id": row.id,
        "contract_id": row.contract_id,
        "contract_revision": row.contract_revision,
        "session_id": row.session_id,
        "ref_id": row.ref_id,
        "status": row.status,
        "job_id": row.job_id,
        "verified": row.verified or {},
        "counts": row.counts or {},
        "findings_path": row.findings_path,
        "findings_digest": row.findings_digest,
        "sampled": bool(row.sampled),
        "error": row.error,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


#: 进程级单例。
field_contract_store = FieldContractStore()
