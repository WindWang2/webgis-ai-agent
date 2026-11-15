"""G45 WP08/WP09 — 成果发布、显示副本与严格发布门。

布局（G38 data-join 同款纪律；任务独占目录）::

    DATA_DIR/coord-ops/.staging/<run_id>/   # 冻结源 + staging 输出 + manifest
    DATA_DIR/coord-ops/<run_id>/            # 校验通过后同卷原子更名（终态包）

发布链：staging 输出 → 回读核验 → **严格发布门**（见下）→ 原子更名 →
DataObject CAS（kind=coordinate_operation）→ session 台账
（ref:coord-ops/<run_id>）+ fabric-parquet 登记与 display ref → 收据可回读
才允许 run 行落 completed（恢复语义与 G38 recovery 同构）。

严格发布门（C39/C40 —— 任务书明示 runner.ensure_not_cancelled 的
fail-open 不可单独约束发布）：

- 发布前以独立短事务查询 job 持久取消事实 **与** 当前 worker/claim；
- 查询本身失败（DB 不可达）→ ``publish_blocked``（不发布，可恢复）；
- claim/attempt 已被接管（迟到执行体）→ 同上；
- 本门只约束「终态包原子更名 + 台账登记」这类不可逆副作用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.coordinate_operations import contracts
from app.services.coordinate_operations.contracts import CoordinateOperationError

logger = logging.getLogger(__name__)


def domain_root() -> Path:
    from app.core.config import settings

    root = Path(settings.DATA_DIR) / contracts.DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def staging_dir(run_id: str) -> Path:
    d = domain_root() / ".staging" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_dir(run_id: str) -> Path:
    return domain_root() / run_id


def new_run_id() -> str:
    return "co-" + secrets.token_hex(12)


def discard_staging(run_id: str) -> None:
    shutil.rmtree(domain_root() / ".staging" / run_id, ignore_errors=True)


def discard_final(run_id: str) -> None:
    """仅取消收敛/恢复补偿路径调用（不碰源数据/共享 blob）。"""
    shutil.rmtree(final_dir(run_id), ignore_errors=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 严格发布门 ─────────────────────────────────────────────────────────────

def strict_publish_gate(handle: Any, job_id: int) -> None:
    """发布前置校验：持久取消 + 当前 claim/attempt（查询失败 = 阻断）。

    与 runner ``ensure_not_cancelled`` 的差别（任务书 C39/C40）：那里 DB 瞬时
    不可用按未取消处理（fail-open，看门狗兜底）；本域的发布是新增不可逆
    副作用，查询不可达或 claim 无法确认时**必须**停止发布。
    """
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select

    try:
        with SessionLocal() as db:
            row = db.execute(
                select(AnalysisTask).where(AnalysisTask.id == job_id)
            ).scalar_one_or_none()
            if row is None:
                raise CoordinateOperationError(
                    "publish_blocked", f"job {job_id} 行不可读（发布阻断）")
            if str(row.status) in ("cancelling", "cancelled"):
                raise CoordinateOperationError(
                    "publish_blocked", "发布前发现持久取消事实（已停止发布）")
            current_worker = getattr(row, "worker_id", None)
            current_attempt = getattr(row, "attempt", None)
            if current_worker is not None and current_worker != handle.worker_id:
                raise CoordinateOperationError(
                    "publish_blocked",
                    f"job 已被新 worker 认领（{current_worker}）；本执行体"
                    "不得发布（迟到 attempt fencing）")
            if current_attempt is not None and handle.attempt is not None \
                    and int(current_attempt) != int(handle.attempt):
                raise CoordinateOperationError(
                    "publish_blocked",
                    f"job attempt 已漂移（{current_attempt} != "
                    f"{handle.attempt}）；本执行体不得发布")
            db.commit()
    except CoordinateOperationError:
        raise
    except Exception as e:  # noqa: BLE001 — 查询不可达 → 阻断（不 fail-open）
        raise CoordinateOperationError(
            "publish_blocked",
            f"发布前持久状态查询失败（不发布，可恢复）: {type(e).__name__}: "
            f"{e}") from e


# ── manifest / 原子发布 / 收据 ─────────────────────────────────────────────

def write_manifest(
    run_id: str,
    *,
    members: Dict[str, str],
    output_crs: str,
    operation: Dict[str, Any],
    source: Dict[str, Any],
    stats: Dict[str, Any],
) -> Path:
    manifest = {
        "run_id": run_id,
        "members": members,
        "output_crs": output_crs,
        "operation": operation,
        "source": source,
        "stats": stats,
        "schema_version": 1,
    }
    p = staging_dir(run_id) / "manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def atomic_publish(run_id: str) -> Path:
    """staging → 终态目录同卷原子更名（已存在 → typed 失败）。"""
    src = domain_root() / ".staging" / run_id
    dst = final_dir(run_id)
    if not src.is_dir():
        raise FileNotFoundError(f"staging missing for run {run_id}")
    if dst.exists():
        raise CoordinateOperationError(
            "internal_error", f"run {run_id} 终态目录已存在")
    os.replace(src, dst)
    return dst


def write_receipt(run_id: str, receipt: Dict[str, Any]) -> Path:
    p = final_dir(run_id) / "receipt.json"
    p.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def read_receipt(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(
            (final_dir(run_id) / "receipt.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 不可读按无收据处理（诚实）
        return None


def read_manifest(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(
            (final_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def verify_published(run_id: str) -> Dict[str, Any]:
    """终态包核验：成员存在 + sha256 与 manifest 一致 + 可解析（复用前/恢复依据）。"""
    d = final_dir(run_id)
    manifest = read_manifest(run_id)
    if manifest is None or not isinstance(manifest.get("members"), dict):
        raise CoordinateOperationError(
            "result_unavailable", f"run {run_id} 缺 manifest 或成员表",
            run_id=run_id)
    for name, expect_sha in manifest["members"].items():
        p = d / name
        if not p.is_file():
            raise CoordinateOperationError(
                "result_unavailable", f"run {run_id} 成员 {name} 缺失",
                run_id=run_id, member=name)
        if _sha256_file(p) != expect_sha:
            raise CoordinateOperationError(
                "result_unavailable", f"run {run_id} 成员 {name} sha256 不符",
                run_id=run_id, member=name)
    return {"members": manifest["members"],
            "output_crs": manifest.get("output_crs")}


def load_output_fc(run_id: str, output_name: str = "output") -> Dict[str, Any]:
    """权威 GeoJSON 成果读取（结果分页/显示副本/回读 oracle 共用）。"""
    p = final_dir(run_id) / f"{output_name}.geojson"
    if not p.is_file():
        raise CoordinateOperationError(
            "result_unavailable", f"run {run_id} 无权威 GeoJSON 成果",
            run_id=run_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise CoordinateOperationError(
            "result_unavailable", f"run {run_id} 成果不可读: {e}",
            run_id=run_id) from e


def result_page(run_id: str, cursor: int, limit: int,
                output_name: str = "output") -> Dict[str, Any]:
    """权威文件上的分页读（含空几何要素；行序 = 输出序）。"""
    limit = max(1, min(int(limit), contracts.RESULT_PAGE_MAX_ROWS))
    cursor = max(0, int(cursor))
    fc = load_output_fc(run_id, output_name)
    feats = fc.get("features") or []
    total = len(feats)
    window = feats[cursor:cursor + limit]
    rows = [
        {
            "properties": f.get("properties") or {},
            "geometry_summary": (
                {"type": (f.get("geometry") or {}).get("type")}
                if f.get("geometry") else None),
            "first_coordinate": _first_coord(f.get("geometry")),
        }
        for f in window
    ]
    next_cursor = cursor + len(window)
    return {
        "run_id": run_id,
        "rows": rows,
        "total_rows": total,
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < total else None,
        "page_size": limit,
        "crs": fc.get("crs", {}).get("properties", {}).get("name")
        if isinstance(fc.get("crs"), dict) else None,
    }


def _first_coord(geom: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    node = geom
    for _ in range(12):
        if not isinstance(node, dict):
            return None
        coords = node.get("coordinates")
        if coords is None:
            subs = node.get("geometries")
            if subs:
                node = subs[0]
                continue
            return None
        node = coords
        if isinstance(node, list) and node and isinstance(node[0], (int, float)):
            return [float(node[0]), float(node[1])] if len(node) >= 2 else None
        if isinstance(node, list) and node:
            node = node[0]
            continue
        return None
    return None


# ── DataObject / 台账 / fabric-parquet 登记 ────────────────────────────────

def publish_data_object_for_run(
    run_id: str, *, owner_scope: Dict[str, str],
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """终态包 → durable DataObject（CAS；三态诚实降级，本体不受影响）。"""
    from app.services.lakehouse.data_object import (
        DataObjectTooLargeError,
        publish_data_object,
    )

    d = final_dir(run_id)
    files = {
        p.name: p for p in sorted(d.iterdir())
        if p.is_file() and p.name not in ("manifest.json", "receipt.json")
    }
    if not files:
        return {"state": "failed", "error": "no output files"}
    try:
        identity = publish_data_object(
            files,
            kind=contracts.DATA_OBJECT_KIND,
            owner_scope=owner_scope,
            payload={"run_id": run_id},
            producer={"tool": "coordinate_operations", "run_id": run_id},
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
        )
        return {
            "state": "published",
            "data_object_id": identity.data_object_id,
            "manifest_location": identity.manifest_location,
            "content_sha256": identity.content_sha256,
            "byte_size": identity.byte_size,
            "deduped": identity.deduped,
        }
    except DataObjectTooLargeError:
        return {"state": "oversized"}
    except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
        logger.warning("[coord-ops] data object publish failed for %s: %s",
                       run_id, e)
        return {"state": "failed", "error": str(e)[:200]}


async def register_session_artifact(
    session_id: str, run_id: str, descriptor: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Optional[str]:
    """session 台账登记（ref:coord-ops/<run_id>；增值记录，绝不阻断本体）。"""
    try:
        from app.services.artifact_registry import register_artifact

        ref = f"{contracts.ARTIFACT_REF_PREFIX}{run_id}"
        await register_artifact(
            session_id,
            artifact_id=ref,
            artifact_type="coordinate_operation",
            producer_capability="coordinate_operations",
            producer_tool=contracts.TASK_TYPE,
            descriptor=descriptor,
            metadata=metadata,
        )
        return ref
    except Exception as e:  # noqa: BLE001
        logger.warning("[coord-ops] ledger registration skipped for %s: %s",
                       run_id, e)
        return None


def register_fabric_parquet(
    session_id: str, parquet_path: Path, *,
    title: str, crs: str, rows: int, run_id: str,
) -> Dict[str, Any]:
    """GeoParquet 投影 → fabric-parquet 磁盘工件（G18 可直接交付的 ref）。

    复用 materialization_service 同款原语（路径单点 ``fabric_parquet_path``）；
    从已写好的受验文件出发拷贝登记，不重写内容。台账登记（异步
    ``register_artifact``）由调用方 service 完成，本函数返回登记所需证据。
    """
    import secrets as _secrets

    from app.services.artifact_registry import fabric_parquet_path

    artifact_id = _secrets.token_hex(8)
    ref = f"ref:fabric-parquet/{artifact_id}"
    dst = fabric_parquet_path(session_id, ref)
    if dst is None:  # pragma: no cover — 铸造 id 恒合法
        raise CoordinateOperationError("internal_error", "fabric ref 路径不可派生")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(parquet_path, dst)
    sha = _sha256_file(dst)
    return {
        "artifact_ref": ref,
        "path": str(dst),
        "title": title,
        "crs": crs,
        "rows": rows,
        "content_sha256": sha,
        "run_id": run_id,
    }


# ── 显示副本（WGS84 派生物）───────────────────────────────────────────────

async def mint_display_ref(
    session_id: str, fc_4326: Dict[str, Any], *, label: str,
) -> Dict[str, Any]:
    """≤DISPLAY_REF_MAX_ROWS 时铸 session FC ref（地图显示投影）。"""
    from app.services.session_data import session_data_manager

    n = len(fc_4326.get("features") or [])
    if n > contracts.DISPLAY_REF_MAX_ROWS:
        raise CoordinateOperationError(
            "display_unavailable",
            f"显示副本 {n} 行超过上限 {contracts.DISPLAY_REF_MAX_ROWS}"
            "（主成果不受影响；完整数据经导出交付）",
            rows=n, cap=contracts.DISPLAY_REF_MAX_ROWS)
    ref = await session_data_manager.store(
        session_id, fc_4326, prefix=contracts.DISPLAY_REF_PREFIX)
    return {"display_ref": ref, "rows": n, "label": label}
