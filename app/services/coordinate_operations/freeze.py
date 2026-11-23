"""G45 WP02 — 授权源资格裁决与读取期持久冻结。

源资格（最早可证明阶段阻断，不静默假定 4326）：

- 复用 G18 ``resolve_source``（session_ref / fabric_parquet / project_artifact；
  table_rows 无几何 → typed 不合格）；
- CRS 证据必须来自**载体权威元数据**（parquet geo metadata / artifact.crs /
  FC ``properties.schema_info.crs``）—— 缺失 → ``source_crs_missing``，
  绝不按坐标范围猜测（C04/C31）；
- 完整性：``truncated``/非完整选择证据 → ``source_incomplete``；
- 行数/几何预算在 describe 阶段预闸（大表尽早失败，不在内存耗尽后报错）。

冻结（C25/C26 —— 跨批读取一致性）：

- 任何可变载体（session payload / fabric parquet 文件）在执行前统一铸成
  **GeoParquet 冻结件**（run staging 独占目录）：parquet 源 = 受验拷贝
  （拷贝后 sha256 必须等于解析期指纹）；payload 源 = ``features_to_arrow``
  编码（属性列编码失败 = typed 不支持，诚实前置）；
- 冻结件回读行数/CRS/geometry 摘要与解析期快照对账，落 ``freeze.json`` 证据；
- 执行体只读冻结件 —— 源后续变化/ABA 与执行隔离。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

from app.services.coordinate_operations import contracts
from app.services.coordinate_operations.contracts import CoordinateOperationError

logger = logging.getLogger(__name__)


def _actor_kwargs(actor: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.data_delivery.source_resolver import DeliveryActor

    return {"actor": DeliveryActor(
        user_id=actor.get("user_id"), org_id=actor.get("org_id"),
        owner_token=actor.get("owner_token"))}


async def resolve_snapshot(
    session_id: str, source: Dict[str, Any], actor: Dict[str, Any]
) -> Any:
    """源描述 → G18 SourceSnapshot（授权复核；不做资格裁决）。

    受信回退（G38 同款语义）：工具平面的 session_id 由 ToolRegistry 从已授权
    上下文注入 —— 注入 session 本身即授权；匿名 actor + 源在同一受信
    session 内时经同一快照构建器直读；跨 session 源仍然拒绝。
    """
    from app.schemas.data_delivery_schema import DeliverySourceRef
    from app.services.data_delivery.source_resolver import (
        SourceResolutionError,
        resolve_source,
    )

    try:
        ref = DeliverySourceRef(**{
            "kind": source.get("kind"),
            "session_id": source.get("session_id") or session_id,
            "ref": source.get("ref"),
            "project_id": source.get("project_id"),
            "artifact_id": source.get("artifact_id"),
            "revision_no": source.get("revision_no"),
            "table_ref": source.get("table_ref"),
        })
    except Exception as e:
        raise CoordinateOperationError(
            "invalid_spec", f"源描述非法: {e}") from e
    try:
        return await resolve_source(ref, **_actor_kwargs(actor))
    except SourceResolutionError as e:
        code = str(e.error_code or "")
        trusted = actor.get("trusted_session_id")
        if (code.upper().endswith("UNAUTHORIZED") and trusted
                and source.get("kind") in ("session_ref",)
                and (source.get("session_id") or session_id)
                == session_id == trusted):
            from app.services.data_delivery.source_resolver import (
                _snapshot_from_payload,
            )
            from app.services.session_data import session_data_manager

            payload = await session_data_manager.get(
                session_id, source.get("ref") or "")
            if payload is not None:
                try:
                    return _snapshot_from_payload(
                        source.get("ref"), payload, source.get("ref") or "")
                except SourceResolutionError:
                    pass  # 落到 typed 映射
        mapping = {
            "SOURCE_KIND_UNKNOWN": "invalid_spec",
            "UNAUTHORIZED": "unauthorized",
            "SOURCE_EXPIRED": "source_expired",
            "SOURCE_NOT_FOUND": "not_found",
            "SOURCE_SHAPE_UNSUPPORTED": "source_unqualified",
            "SOURCE_REVISION_CHANGED": "source_revision_changed",
        }
        raise CoordinateOperationError(
            mapping.get(e.error_code, "invalid_spec"),
            f"源解析失败: {e.reason}", source_error_code=e.error_code) from e


async def resolve_eligible_source(
    session_id: str, source: Dict[str, Any], actor: Dict[str, Any]
) -> Dict[str, Any]:
    """解析 + 资格裁决 → 源卡（含 CRS 证据与指纹；不读全量）。"""
    from app.services.data_delivery.source_resolver import sample_parquet_geometry

    snap = await resolve_snapshot(session_id, source, actor)

    # ── 资格 1：几何载体（表格/无几何 → 不合格）────────────────────────
    if snap.is_table:
        raise CoordinateOperationError(
            "source_unqualified", "源是纯表格（无几何），不是本域可重投影的矢量载体")
    if snap.geometry is None:
        raise CoordinateOperationError(
            "source_unqualified", "源缺少几何描述（无几何列/几何元数据）")

    # parquet 源先采样补齐几何画像（有界读）
    if snap.parquet_path is not None:
        sample_parquet_geometry(snap, limit=2000)

    geom_types = [t for t in (snap.geometry or {}).get("types") or [] if t]
    total_rows = int(snap.row_count or 0)
    null_or_empty = int((snap.geometry or {}).get("null_or_empty_count") or 0)
    if total_rows <= 0:
        raise CoordinateOperationError(
            "source_unqualified", "源为空（0 行）")
    if not geom_types and null_or_empty != total_rows:
        # 采样未见几何类型且并非全空几何 —— 数据形态存疑，前置阻断
        raise CoordinateOperationError(
            "source_unqualified",
            "源几何形态无法确认（采样无几何类型）")
    if not geom_types and null_or_empty == total_rows:
        raise CoordinateOperationError(
            "source_unqualified", "源全部要素为空/无几何 —— 无可重投影内容")

    # ── 资格 2：完整性（截断/样本不可冒充完整选择）──────────────────────
    comp = snap.completeness or {}
    if comp.get("truncated") is True:
        raise CoordinateOperationError(
            "source_incomplete",
            "源是截断结果（truncated=true）；完整重投影需要完整数据集或"
            "可精确重放的选择句柄")
    if comp.get("is_complete") is False:
        raise CoordinateOperationError(
            "source_incomplete", "源声明非完整（is_complete=false）")

    # ── 资格 3：CRS 证据（载体权威元数据；缺失即阻断，不猜）─────────────
    crs = snap.crs
    crs_evidence = _crs_evidence(snap)
    if not crs:
        raise CoordinateOperationError(
            "source_crs_missing",
            "源缺少可信 CRS 证据（载体元数据无 CRS 声明）；本域不做坐标范围"
            "推断，请先在导入/数据源侧确认源 CRS",
            crs_evidence=crs_evidence)

    # ── 资格 4：预算预闸（行数；字节在冻结时复核）──────────────────────
    if total_rows > contracts.MAX_ROWS:
        raise CoordinateOperationError(
            "budget_exceeded",
            f"源 {total_rows} 行超过本域上限 {contracts.MAX_ROWS}",
            rows=total_rows, cap=contracts.MAX_ROWS)

    return {
        "kind": snap.kind,
        "label": snap.label,
        "row_count": total_rows,
        "content_fingerprint": snap.content_fingerprint,
        "content_revision": snap.content_revision,
        "artifact_revision_no": snap.artifact_revision_no,
        "crs": crs,
        "crs_evidence": crs_evidence,
        "geometry": snap.geometry,
        "fields": snap.fields,
        "completeness": comp,
        # 内部：预览样本读取直接复用本次解析（不二次 resolve）
        "_snapshot": snap,
    }


def _crs_evidence(snap: Any) -> Dict[str, Any]:
    """CRS 证据来源披露（哪里读到的、可信度语义）。"""
    if snap.kind == "fabric_parquet":
        return {"origin": "geoparquet_geo_metadata", "trusted": True}
    if snap.kind == "project_artifact":
        return {"origin": "project_artifact_crs", "trusted": True,
                "artifact_revision": snap.artifact_revision_no}
    if snap.kind == "session_ref":
        return {"origin": "fc_schema_info_crs", "trusted": True}
    return {"origin": "unknown", "trusted": False}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_source(snapshot: Any, run_dir: Path) -> Dict[str, Any]:
    """解析期快照 → run staging 内持久冻结件（同步、有界）。

    双载体（运行时探测、证据披露）：

    - parquet 源（必经 pyarrow，G18 parquet lane 同款约束）：受验拷贝；
    - payload 源：pyarrow 可用 → GeoParquet 冻结（执行期可流式批读）；
      不可用 → canonical JSON 冻结（属性 JSON 保真；执行期整表有界物化）。

    Returns: freeze 证据 {frozen_path(仅证据，不外发), frozen_sha256,
                          carrier("parquet"|"json"), row_count, crs,
                          source_kind, source_fingerprint, geometry}
    Raises:
        source_revision_changed: 拷贝后指纹与解析期不一致（ABA/漂移）。
        unsupported_dimension / budget_exceeded / internal_error。
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    if snapshot.parquet_path is not None:
        frozen = run_dir / (contracts.FROZEN_SOURCE_NAME + ".parquet")
        try:
            import pyarrow  # noqa: F401
        except ImportError as e:
            raise CoordinateOperationError(
                "source_unqualified",
                "GeoParquet 源的冻结/批读需要可选依赖 pyarrow（当前环境未安装）"
            ) from e
        shutil.copyfile(snapshot.parquet_path, frozen)
        copied_sha = _file_sha256(frozen)
        if copied_sha != snapshot.content_fingerprint:
            frozen.unlink(missing_ok=True)
            raise CoordinateOperationError(
                "source_revision_changed",
                "源 GeoParquet 在解析与冻结之间发生变化（sha256 不一致）；"
                "请重新描述源并更新计划")
        carrier = "parquet"
    else:
        features: List[Dict[str, Any]] = (
            (snapshot.payload or {}).get("features") or [])
        if len(features) != int(snapshot.row_count):
            raise CoordinateOperationError(
                "internal_error", "payload 行数与解析期不一致")
        try:
            import pyarrow  # noqa: F401
            from pyarrow import parquet as pq

            from app.services.data_fabric.vector_carrier import (
                VectorCarrierEncodeError,
                features_to_arrow,
            )
            frozen = run_dir / (contracts.FROZEN_SOURCE_NAME + ".parquet")
            try:
                table = features_to_arrow(features, crs=snapshot.crs)
            except VectorCarrierEncodeError as e:
                raise CoordinateOperationError(
                    "unsupported_dimension",
                    f"源属性列无法保真编码为冻结载体（混合类型/超范围值）: {e}",
                ) from e
            pq.write_table(table, str(frozen), compression="zstd")
            carrier = "parquet"
        except ImportError:
            frozen = run_dir / (contracts.FROZEN_SOURCE_NAME + ".json")
            frozen.write_text(
                json.dumps(snapshot.payload, ensure_ascii=False,
                           separators=(",", ":")), encoding="utf-8")
            carrier = "json"

    frozen_sha = _file_sha256(frozen)
    if carrier == "parquet" and snapshot.parquet_path is not None \
            and frozen_sha != snapshot.content_fingerprint:
        frozen.unlink(missing_ok=True)
        raise CoordinateOperationError(
            "internal_error", "冻结件 sha256 对账失败")
    if carrier == "parquet":
        from pyarrow import parquet as pq

        n = int(pq.read_metadata(str(frozen)).num_rows)
    else:
        n = len(json.loads(frozen.read_text(encoding="utf-8"))["features"])
    if n != int(snapshot.row_count):
        frozen.unlink(missing_ok=True)
        raise CoordinateOperationError(
            "internal_error",
            f"冻结件行数 {n} 与解析期 {snapshot.row_count} 不一致")

    evidence = {
        "frozen_path": str(frozen),
        "carrier": carrier,
        "frozen_sha256": frozen_sha,
        "row_count": int(snapshot.row_count),
        "crs": snapshot.crs,
        "source_kind": snapshot.kind,
        "source_fingerprint": snapshot.content_fingerprint,
        "frozen_bytes": frozen.stat().st_size,
        "geometry": snapshot.geometry,
    }
    (run_dir / "freeze.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def verify_frozen(run_dir: Path, expect: Dict[str, Any]) -> Path:
    """执行前复核冻结件（存在 + sha256 + 行数）→ 冻结件路径。

    载体按冻结证据 ``carrier`` 解析（parquet | json）：无 pyarrow 时 payload
    源冻结件是 ``source-frozen.json``（freeze_source 的 json 载体；执行体
    ``_iter_frozen_features`` 同样双载体读取）—— 只认 parquet 会把这类合法
    冻结件误报成"冻结源缺失"。
    """
    carrier = expect.get("carrier") or "parquet"
    if carrier not in ("parquet", "json"):
        raise CoordinateOperationError(
            "internal_error", f"未知冻结载体 {carrier!r}（run staging 不完整）")
    frozen = run_dir / (contracts.FROZEN_SOURCE_NAME + "." + carrier)
    if not frozen.is_file():
        raise CoordinateOperationError(
            "internal_error", "冻结源缺失（run staging 不完整）")
    if _file_sha256(frozen) != expect["frozen_sha256"]:
        raise CoordinateOperationError(
            "internal_error", "冻结源 sha256 漂移（staging 被外部改动）")
    if carrier == "parquet":
        from pyarrow import parquet as pq

        n = int(pq.read_metadata(str(frozen)).num_rows)
    else:
        n = len(json.loads(frozen.read_text(encoding="utf-8"))["features"])
    if n != int(expect["row_count"]):
        raise CoordinateOperationError("internal_error", "冻结源行数漂移")
    return frozen
