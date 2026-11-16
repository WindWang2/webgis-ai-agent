"""G45 WP07/WP09 — 冻结操作的几何变换内核与全量执行体。

变换内核（vertex-only、确定性）：

- 只用 ``Transformer.from_pipeline(冻结 PROJJSON)`` —— 执行期不重选操作；
- 逐批 vectorized：每几何收集 leaf 顶点对 → 单次数组变换 → 原树重建
  （环结构/几何类型/输入顺序/属性/Z(第三维, 原值保留) 全保真）；
- 不插值测地线、不修复拓扑、不切分反经线（densification 未实现也不隐式）。

失败语义（all-or-nothing，C18/C19）：

- 任一**非空**几何变换抛错 / 输入或输出非有限 → 全任务失败（不发布成果），
  错误定位到行号 + 几何类型 + 批次；
- 空/null 几何独立计数并按政策保留（empty_policy=keep），与失败不混淆；
- 预算闸（行/总顶点/单几何顶点/输出字节）在分配前触发。

执行体（durable）：verify_frozen → 批读冻结 parquet → 变换 → staging 输出
parquet（target CRS）→ 回读核验 → **严格发布门**（C39/C40：持久取消/claim/
attempt 查询不可达或漂移 → 不发布）→ 成果发布与显示副本（见 outputs.py）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.coordinate_operations import contracts
from app.services.coordinate_operations.contracts import CoordinateOperationError

logger = logging.getLogger(__name__)


def _leaf_pairs(coords: Any, out: List[List[Any]]) -> None:
    """GeoJSON 坐标树 → leaf 顶点列表（复用 app.utils.coord_transform 同构遍历，
    保持全仓单一遍历语义；此处直接 import 以避免第二实现漂移）。"""
    from app.utils.coord_transform import _collect_leaf_pairs

    _collect_leaf_pairs(coords, out)


def _rebuild(coords: Any, new_pairs: np.ndarray, idx: List[int]) -> Any:
    from app.utils.coord_transform import _rebuild_coords

    return _rebuild_coords(coords, new_pairs, idx)


class FrozenOperation:
    """冻结坐标操作的执行视图（from_pipeline 精确重建 + 环境复核）。"""

    def __init__(self, operation_projjson: str, definition_sha256: str,
                 frozen_env: Dict[str, Any]):
        from pyproj.transformer import Transformer

        self.definition_sha256 = definition_sha256
        self.frozen_env = frozen_env
        try:
            self._transformer = Transformer.from_pipeline(operation_projjson)
        except Exception as e:
            raise CoordinateOperationError(
                "operation_env_changed",
                f"冻结操作定义无法在当前环境重建（PROJ 兼容性漂移）: {e}") from e
        self.verify_environment()

    def verify_environment(self) -> None:
        """执行前环境复核：pyproj/PROJ/network/格网清单与冻结证据不一致即阻断。"""
        from app.services.coordinate_operations.candidates import (
            environment_fingerprint,
        )

        current = environment_fingerprint()
        for k in ("pyproj_version", "proj_version", "network_enabled",
                  "trusted_grid_inventory"):
            if current.get(k) != self.frozen_env.get(k):
                raise CoordinateOperationError(
                    "operation_env_changed",
                    f"执行环境与计划冻结时不一致（{k}: "
                    f"{self.frozen_env.get(k)!r} → {current.get(k)!r}）；"
                    "原计划失效，请重新列出候选并锁定",)
        if self._transformer is None:  # pragma: no cover — 构造期已挡
            raise CoordinateOperationError("operation_env_changed", "操作未初始化")

    def transform_array(self, xs: np.ndarray, ys: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """数组变换（errcheck 开：PROJ 错误如实抛出，不吞错）。"""
        try:
            return self._transformer.transform(xs, ys, errcheck=True)
        except CoordinateOperationError:
            raise
        except Exception as e:
            raise CoordinateOperationError(
                "execute_failed", f"坐标操作执行错误: {e}") from e


@dataclass
class BatchStats:
    rows: int = 0
    vertices: int = 0
    null_geometry: int = 0
    empty_geometry: int = 0
    max_single_geometry_vertices: int = 0
    geom_types: Dict[str, int] = field(default_factory=dict)


def transform_feature(
    feature: Dict[str, Any],
    op: FrozenOperation,
    stats: BatchStats,
    *,
    row_index: int,
) -> Dict[str, Any]:
    """单要素变换（GeoJSON dict → 新 dict；Z 原值保留；属性原样）。

    空/null 几何：保留 + 计数（empty_policy=keep），不参与变换与失败语义。
    """
    geom = feature.get("geometry")
    if geom is None:
        stats.null_geometry += 1
        return feature
    if not isinstance(geom, dict) or not geom.get("type"):
        stats.null_geometry += 1
        return feature

    coords = geom.get("coordinates")
    if coords is None or coords == [] or coords == {}:
        if "geometries" not in geom:
            # 纯坐标几何且坐标为空 —— 空几何政策（keep + 独立计数）。
            # GeometryCollection 无顶层 coordinates，不能落此分支（否则整类
            # 几何被误判为空 —— 冒烟测试抓到的真实缺陷）。
            stats.empty_geometry += 1
            stats.geom_types[geom.get("type", "?")] = (
                stats.geom_types.get(geom.get("type", "?"), 0) + 1)
            return feature

    leaves: List[List[Any]] = []
    _collect_leaves_deep(geom, leaves)
    if not leaves:
        stats.empty_geometry += 1
        stats.geom_types[geom.get("type", "?")] = (
            stats.geom_types.get(geom.get("type", "?"), 0) + 1)
        return feature

    if len(leaves) > contracts.MAX_SINGLE_GEOM_VERTICES:
        raise CoordinateOperationError(
            "budget_exceeded",
            f"行 {row_index} 单几何顶点数 {len(leaves)} 超过上限 "
            f"{contracts.MAX_SINGLE_GEOM_VERTICES}（分配前阻断）",
            row_index=row_index, vertices=len(leaves),
            cap=contracts.MAX_SINGLE_GEOM_VERTICES)

    arr = np.array([[float(c[0]), float(c[1])] for c in leaves], dtype=np.float64)
    if not np.isfinite(arr).all():
        bad = int(np.where(~np.isfinite(arr).all(axis=1))[0][0])
        raise CoordinateOperationError(
            "execute_failed",
            f"行 {row_index} 输入坐标含非有限值（顶点 #{bad}）",
            row_index=row_index, phase="non_finite_input")

    tx, ty = op.transform_array(arr[:, 0], arr[:, 1])
    out = np.column_stack([tx, ty])
    if not np.isfinite(out).all():
        bad = int(np.where(~np.isfinite(out).all(axis=1))[0][0])
        raise CoordinateOperationError(
            "execute_failed",
            f"行 {row_index} 变换输出含非有限值（顶点 #{bad}；操作适用范围外"
            "或输入超域）", row_index=row_index, phase="non_finite_output")

    new_geom = dict(geom)
    if "coordinates" in new_geom:
        new_geom["coordinates"] = _rebuild_tree(
            new_geom["coordinates"], out, [0])
    if "geometries" in new_geom:
        # GeometryCollection：leaves 已深度收集，重建需逐子几何分配 —— 简单
        # 起见按同一游标重建每个子树。
        cursor = [0]
        new_geom["geometries"] = [
            _rebuild_subtree(g, out, cursor) for g in new_geom["geometries"]]

    stats.rows += 1
    stats.vertices += len(leaves)
    stats.max_single_geometry_vertices = max(
        stats.max_single_geometry_vertices, len(leaves))
    gtype = geom.get("type", "?")
    stats.geom_types[gtype] = stats.geom_types.get(gtype, 0) + 1

    new_feat = dict(feature)
    new_feat["geometry"] = new_geom
    return new_feat


def _collect_leaves_deep(geom: Dict[str, Any], out: List[List[Any]]) -> None:
    """深度收集（含 GeometryCollection 嵌套 geometries）。"""
    if "coordinates" in geom and geom["coordinates"] is not None:
        _leaf_pairs(geom["coordinates"], out)
    for sub in geom.get("geometries") or []:
        if isinstance(sub, dict):
            _collect_leaves_deep(sub, out)


def _rebuild_tree(coords: Any, pairs: np.ndarray, idx: List[int]) -> Any:
    return _rebuild(coords, pairs, idx)


def _rebuild_subtree(geom: Dict[str, Any], pairs: np.ndarray,
                     cursor: List[int]) -> Dict[str, Any]:
    new_sub = dict(geom)
    if "coordinates" in new_sub and new_sub["coordinates"] is not None:
        n_leaves = 0
        tmp: List[List[Any]] = []
        _leaf_pairs(new_sub["coordinates"], tmp)
        n_leaves = len(tmp)
        slice_ = pairs[cursor[0]: cursor[0] + n_leaves]
        cursor[0] += n_leaves
        new_sub["coordinates"] = _rebuild(new_sub["coordinates"], slice_, [0])
    if "geometries" in new_sub:
        new_sub["geometries"] = [
            _rebuild_subtree(g, pairs, cursor) for g in new_sub["geometries"]]
    return new_sub


# ── 全量执行体 ─────────────────────────────────────────────────────────────

def _iter_frozen_features(
    frozen_evidence: Dict[str, Any], batch_rows: int,
):
    """冻结件 → Feature 批次迭代（parquet 流式 / json 整读后批切）。"""
    path = Path(frozen_evidence["frozen_path"])
    if frozen_evidence["carrier"] == "parquet":
        import pyarrow.parquet as pq

        from app.services.data_fabric.vector_carrier import arrow_to_features

        pf = pq.ParquetFile(str(path))
        for batch in pf.iter_batches(batch_size=batch_rows):
            yield arrow_to_features(batch)
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    features = doc.get("features") or []
    for start in range(0, len(features), batch_rows):
        yield features[start:start + batch_rows]


def run_streaming_transform(
    frozen_evidence: Dict[str, Any],
    staging_dir: Path,
    op: FrozenOperation,
    target_crs: str,
    *,
    output_name: str = "output",
    handle: Any = None,
    batch_rows: int = contracts.EXECUTE_BATCH_ROWS,
    loop: Any = None,
) -> Dict[str, Any]:
    """冻结件 → 批变换 → staging 双载体输出（同步；建议线程池执行）。

    输出（staging_dir 内）：

    - ``<output_name>.geojson`` —— **权威成果**：目标 CRS 成员 +
      ``properties.schema_info.crs``（G18 材料化证据通道同款）；逐批增量写
      （内存 = 单批）；
    - ``<output_name>.parquet`` —— GeoParquet 投影（pyarrow 可用时；native
      CRS 载体，供 fabric-parquet 登记）。

    all-or-nothing：任何失败抛 typed 错误并删除半成品（无发布资格）。
    ``handle`` 提供 checkpoint()/progress()（每批 + 批内预算闸前）。
    """
    import json as _json

    stats = BatchStats()
    started = time.monotonic()
    total = int(frozen_evidence["row_count"])
    if total > contracts.MAX_ROWS:
        raise CoordinateOperationError(
            "budget_exceeded", f"冻结源 {total} 行超上限 {contracts.MAX_ROWS}")

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        from app.services.data_fabric.vector_carrier import features_to_arrow

        have_arrow = True
    except ImportError:
        have_arrow = False
        pa = pq = None  # type: ignore[assignment]
        features_to_arrow = None  # type: ignore[assignment]

    geojson_path = staging_dir / f"{output_name}.geojson"
    parquet_path = staging_dir / f"{output_name}.parquet"
    writer: Any = None
    out_schema: Any = None
    parquet_written = False

    def _checkpoint() -> None:
        if handle is not None:
            handle.checkpoint()

    out_f = geojson_path.open("w", encoding="utf-8")
    try:
        out_f.write('{"type":"FeatureCollection","features":[')
        first_batch = True
        for feats in _iter_frozen_features(frozen_evidence, batch_rows):
            _checkpoint()
            out_feats: List[Dict[str, Any]] = []
            for i, f in enumerate(feats):
                out_feats.append(transform_feature(
                    f, op, stats,
                    row_index=stats.rows + stats.null_geometry
                    + stats.empty_geometry + i + 1))
            if stats.vertices > contracts.MAX_TOTAL_VERTICES:
                raise CoordinateOperationError(
                    "budget_exceeded",
                    f"总顶点数 {stats.vertices} 超上限 "
                    f"{contracts.MAX_TOTAL_VERTICES}",
                    vertices=stats.vertices,
                    cap=contracts.MAX_TOTAL_VERTICES)
            # 权威 GeoJSON 增量写
            for j, feat in enumerate(out_feats):
                if not first_batch or j > 0:
                    out_f.write(",")
                out_f.write(_json.dumps(feat, ensure_ascii=False))
                first_batch = False
            # GeoParquet 投影（有 pyarrow 时）
            if have_arrow and out_feats:
                table = features_to_arrow(out_feats, crs=target_crs)
                if writer is None:
                    out_schema = table.schema
                    writer = pq.ParquetWriter(
                        str(parquet_path), out_schema, compression="zstd")
                else:
                    if not table.schema.equals(out_schema):
                        diff = sorted(
                            set(out_schema.names) ^ set(table.schema.names))[:8]
                        raise CoordinateOperationError(
                            "execute_failed",
                            f"批间属性 schema 漂移（列差异: {diff}）；"
                            "源属性集不一致（all-or-nothing 拒绝）")
                    try:
                        table = table.cast(out_schema)
                    except Exception as e:  # noqa: BLE001 — cast 失败 typed
                        raise CoordinateOperationError(
                            "execute_failed",
                            f"批间属性类型不可安全对齐（cast 失败）: {e}") from e
                writer.write_table(table)
                parquet_written = True
            if handle is not None:
                done = stats.rows + stats.null_geometry + stats.empty_geometry
                _report_progress(
                    handle,
                    value=int(done * 100 / max(total, 1)),
                    message=f"已变换 {done}/{total} 行",
                    loop=loop)
        out_f.write(
            f'],"crs":{{"type":"name","properties":{{"name":'
            f'"{target_crs}"}}}},'
            f'"properties":{{"schema_info":{{"crs":"{target_crs}"}}}}}}')
        if stats.rows <= 0:
            raise CoordinateOperationError(
                "source_unqualified",
                "冻结源没有任何非空可变换要素 —— 拒绝发布空成果")
    except BaseException:
        out_f.close()
        geojson_path.unlink(missing_ok=True)
        parquet_path.unlink(missing_ok=True)
        raise
    finally:
        out_f.close()
        if writer is not None:
            writer.close()

    geojson_bytes = int(geojson_path.stat().st_size)
    parquet_bytes = (int(parquet_path.stat().st_size)
                     if parquet_written else 0)
    if geojson_bytes + parquet_bytes > contracts.MAX_OUTPUT_BYTES:
        geojson_path.unlink(missing_ok=True)
        parquet_path.unlink(missing_ok=True)
        raise CoordinateOperationError(
            "budget_exceeded",
            f"输出 {geojson_bytes + parquet_bytes} 字节超上限 "
            f"{contracts.MAX_OUTPUT_BYTES}")

    return {
        "rows_transformed": stats.rows,
        "null_geometry": stats.null_geometry,
        "empty_geometry": stats.empty_geometry,
        "total_vertices": stats.vertices,
        "max_single_geometry_vertices": stats.max_single_geometry_vertices,
        "geometry_types": dict(sorted(stats.geom_types.items())),
        "geojson_bytes": geojson_bytes,
        "parquet_projection": parquet_written,
        "parquet_bytes": parquet_bytes,
        "duration_s": round(time.monotonic() - started, 3),
        "batch_rows": batch_rows,
    }


def verify_staged_output(
    staging_dir: Path, output_name: str, target_crs: str, expect_rows: int,
) -> Dict[str, Any]:
    """staging 权威输出核验：可解析/行数/CRS 声明一致（发布前置条件）。"""
    import json as _json

    geojson_path = staging_dir / f"{output_name}.geojson"
    if not geojson_path.is_file():
        raise CoordinateOperationError(
            "internal_error", "staging 权威输出缺失")
    doc = _json.loads(geojson_path.read_text(encoding="utf-8"))
    n = len(doc.get("features") or [])
    if n != expect_rows:
        raise CoordinateOperationError(
            "internal_error",
            f"staging 输出行数 {n} != 期望 {expect_rows}（含空几何要素）")
    declared = ((doc.get("properties") or {}).get("schema_info") or {}).get("crs")
    crs_member = ((doc.get("crs") or {}).get("properties") or {}).get("name")
    if not declared or declared != crs_member:
        raise CoordinateOperationError(
            "internal_error", "staging 输出 CRS 声明缺失或不一致")
    from pyproj import CRS

    try:
        same = CRS.from_user_input(declared).equals(
            CRS.from_user_input(target_crs))
    except Exception:  # noqa: BLE001 — 目标不可解析时字符串比较兜底
        same = str(declared) == str(target_crs)
    if not same:
        raise CoordinateOperationError(
            "internal_error",
            f"staging 输出 CRS {declared!r} 与计划目标 {target_crs!r} 不一致")
    return {
        "rows": n,
        "crs": declared,
        "geojson_bytes": int(geojson_path.stat().st_size),
        "geojson_sha256": _sha256_file(geojson_path),
    }


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _report_progress(handle: Any, *, loop: Any = None, **fields: Any) -> None:
    """线程内进度上报（评审 P1-2 修复）。

    AsyncJobHandle.progress 是协程：worker 线程里没有 running loop ——
    ``ensure_future`` 不可用（原实现 100% 走 close() 静默丢弃）。正确通道是
    ``run_coroutine_threadsafe(coro, owner_loop)``（fire-and-forget，异常在
    future 里消化）；owner loop 由提交方（service，事件循环线程）在提交
    executor 前捕获传入。同步 handle（测试桩）直接调用。
    """
    if handle is None:
        return
    try:
        import inspect

        result = handle.progress(**fields)
        if inspect.iscoroutine(result):
            if loop is not None and loop.is_running():
                import contextlib

                fut = asyncio.run_coroutine_threadsafe(result, loop)
                with contextlib.suppress(Exception):
                    fut.result(timeout=5.0)  # 节流下低频；超时不再等
            else:
                result.close()  # 无可用 loop（纯同步测试）—— 关闭不留警告
    except Exception:  # noqa: BLE001 — 进度失败不阻断执行
        pass
