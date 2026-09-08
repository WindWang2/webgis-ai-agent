"""GeoArrow/Arrow 族向量搬运载体（ADR-0101 D8，V4 §16）。

目标：大数据量向量在 Data Plane 内部缝隙（scan → filter → aggregate →
materialize）之间以 Arrow/GeoArrow 语义传输，避免巨型 GeoJSON 序列化；
schema/null/CRS 元数据保留，分块传输，可与 GeoParquet 互操作。

边界（红线）：
- **可选依赖**：pyarrow 未声明为仓库依赖 —— 导入可用才启用；不可用时
  一切入口抛类型化 ``VectorCarrierUnavailable``（诚实降级，绝不假装）；
- **内部载体**：Arrow 载荷绝不进入 tool/LLM 上下文（tool 层永远只看到
  GeoJSON/摘要/ArtifactRef）；大结果仍按既有 feature/byte 上界约束；
- 几何编码采用 GeoArrow 规范的 **WKB 扩展**（schema 级 ``geo`` 元数据
  携带 encoding/CRS）—— 与 GeoParquet 1.1 同族，可零拷贝互转。
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from app.services.data_fabric.errors import DataFabricError

logger = logging.getLogger(__name__)

#: 载体命名空间（schema 元数据版本；编码语义变化时 bump）。
GEOARROW_META_VERSION = "1.0.0"


class VectorCarrierUnavailable(DataFabricError):
    code = "VECTOR_CARRIER_UNAVAILABLE"


class VectorCarrierEncodeError(DataFabricError):
    """几何/属性编码失败（诚实失败：绝不把坏几何静默降级为 null）。"""

    code = "VECTOR_CARRIER_ENCODE_ERROR"


class VectorCarrierSchemaDriftError(VectorCarrierEncodeError):
    """批式输入的 schema 漂移：后续批次与批次 1 冻结的 schema 冲突
    （strict 策略直接拒绝；coerce 策略安全强制失败时同样拒绝 —— 绝不
    静默错位）。"""

    code = "VECTOR_CARRIER_SCHEMA_DRIFT"


def arrow_available() -> bool:
    """pyarrow 可用性探测（每次调用诚实重探；导入开销可忽略）。"""
    try:
        import pyarrow  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - 缺依赖/损坏 = 不可用
        return False


def _require_arrow() -> Any:
    import pyarrow  # noqa: F401

    return pyarrow


def _require_pa() -> Tuple[Any, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq  # noqa: F401

    return pa, pq


def _geo_metadata(
    crs: Optional[str] = None,
    *,
    bbox: Optional[List[float]] = None,
    geometry_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """GeoArrow/GeoParquet 同族的 ``geo`` schema 元数据（WKB 编码）。

    Wave 5：编码时由数据计算 ``bbox``（[minx,miny,maxx,maxy]）与
    ``geometry_types``（排序去重），使写入的文件自描述（此前其自身读取器
    只能诚实报告 bbox=None）。
    """
    # encoding 大写 "WKB" 是 GeoParquet 1.1 规范拼写（评审 MINOR：
    # 小写会破坏严格第三方读取器的互操作）。
    meta: Dict[str, Any] = {
        "version": GEOARROW_META_VERSION,
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "metadata": {},
            }
        },
    }
    geom_col = meta["columns"]["geometry"]
    if crs:
        geom_col["crs"] = crs
    if bbox is not None:
        geom_col["bbox"] = [float(c) for c in bbox]
    if geometry_types:
        geom_col["geometry_types"] = sorted(set(geometry_types))
    return meta


def _iter_geom_coords(node: Any) -> Iterator[Tuple[float, float]]:
    """GeoJSON coordinates 树的 (x, y) 迭代（迭代式；与 stream_bbox_filter
    同一形状判定：数字对 = 坐标点，其余嵌套列表下钻）。"""
    stack: List[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], (int, float)) \
                and len(cur) >= 2 and isinstance(cur[1], (int, float)):
            yield float(cur[0]), float(cur[1])
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)


def _features_geo_stats(
    features: List[Dict[str, Any]],
) -> Tuple[Optional[List[float]], List[str]]:
    """从 feature 批次计算 (bbox, geometry_types)：bbox = 全坐标包围盒
    （无坐标几何 → None，诚实未知）；types = 排序去重几何类型。"""
    minx = miny = math.inf
    maxx = maxy = -math.inf
    types: set = set()
    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict):
            continue
        t = geom.get("type")
        if isinstance(t, str):
            types.add(t)
        for x, y in _iter_geom_coords(geom.get("coordinates")):
            minx = min(minx, x)
            maxx = max(maxx, x)
            miny = min(miny, y)
            maxy = max(maxy, y)
    bbox = [minx, miny, maxx, maxy] if math.isfinite(minx) else None
    return bbox, sorted(types)


def _safe_exc_text(exc: BaseException, limit: int = 200) -> str:
    """异常文本有界化（评审 MINOR：上游 repr 可能含值内容/换行 ——
    日志注入与膨胀面）。"""
    import re as _re

    text = _re.sub(r"[\x00-\x1f\x7f]+", " ", str(exc))
    return text[:limit]


def _features_to_columns(features: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, List[Any]], List[bytes]]:
    """列式拆解：属性列（稀疏 null 保留）+ geometry WKB 列表。"""
    order: List[str] = []
    for feat in features:
        for k in (feat.get("properties") or {}):
            if k not in order:
                order.append(k)
    columns: Dict[str, List[Any]] = {k: [] for k in order}
    wkb_list: List[bytes] = []
    for feat in features:
        props = feat.get("properties") or {}
        for k in order:
            columns[k].append(props.get(k))
        wkb_list.append(_geometry_to_wkb(feat.get("geometry")))
    return order, columns, wkb_list


def _geometry_to_wkb(geom: Optional[Dict[str, Any]]) -> Optional[bytes]:
    """None 几何 → None（合法缺失）；**解析失败 → 抛 typed 错误**（评审
    MAJOR：坏几何静默降级为 null 会与合法缺失不可区分 —— 数据损失）。"""
    if geom is None:
        return None
    if not isinstance(geom, dict) or not geom:
        raise VectorCarrierEncodeError(
            f"geometry is not a GeoJSON geometry object: {type(geom).__name__}")
    try:
        import shapely

        shape = shapely.from_geojson(json.dumps(geom))
        import shapely.io

        if shape is None:
            raise VectorCarrierEncodeError("geometry is not valid GeoJSON")
        return shapely.to_wkb(shape)
    except VectorCarrierEncodeError:
        raise
    except Exception as exc:
        raise VectorCarrierEncodeError(
            f"geometry WKB encode failed: {_safe_exc_text(exc)}") from exc


def _wkb_to_geometry(wkb: Optional[bytes]) -> Optional[Dict[str, Any]]:
    """Arrow → GeoJSON 几何。空几何（POINT EMPTY 等）返回**空 GeoJSON 几何
    字典**（round-1 review MAJOR：空几何是合法数据，静默变 None 是数据
    损失）；WKB 解码失败记 debug 日志后按缺失几何处理（None 保留给不可
    读字节 —— 外来 GeoParquet 的容错方向；编码侧失败是 typed 硬错误）。"""
    if not wkb:
        return None
    try:
        import shapely

        shape = shapely.from_wkb(bytes(wkb))
        if shape is None:
            return None
        # 空几何原样往返（如 {"type": "Point", "coordinates": []}）
        return json.loads(shapely.to_geojson(shape))
    except Exception as exc:
        logger.debug("[vector-carrier] WKB decode failed (%s); geometry=None",
                     _safe_exc_text(exc))
        return None


def features_to_arrow(
    features: List[Dict[str, Any]], *, crs: Optional[str] = None
) -> Any:
    """GeoJSON features → pyarrow.Table（geo WKB 元数据；schema/null 保留）。

    ``geo`` 元数据附编码时计算的 ``bbox`` / ``geometry_types``（文件自描述）。
    """
    if not arrow_available():
        raise VectorCarrierUnavailable(
            "GeoArrow carrier requires the optional 'pyarrow' dependency; "
            "fall back to feature payloads or enable the carrier",
        )
    pa, _ = _require_pa()
    order, columns, wkb_list = _features_to_columns(features)
    arrays = []
    for k in order:
        try:
            arrays.append(pa.array(
                columns[k],
                type=pa.string() if _mostly_str(columns[k]) else None))
        except Exception as exc:
            # 评审 MINOR F7：原始 pyarrow 异常 → typed 错误并指名列。
            raise VectorCarrierEncodeError(
                f"column '{k}' has mixed/unencodable value types: "
                f"{_safe_exc_text(exc)}") from exc
    fields = []
    for k, arr in zip(order, arrays):
        fields.append(pa.field(k, arr.type))
    geometry_array = pa.array(wkb_list, type=pa.binary())
    bbox, geometry_types = _features_geo_stats(features)
    table = pa.Table.from_arrays(
        [*arrays, geometry_array],
        schema=pa.schema(
            [*fields, pa.field("geometry", pa.binary())],
            metadata={"geo": json.dumps(
                _geo_metadata(crs, bbox=bbox, geometry_types=geometry_types))},
        ),
    )
    return table


def _mostly_str(values: List[Any]) -> bool:
    present = [v for v in values if v is not None]
    if not present:
        return True
    return sum(1 for v in present if isinstance(v, str)) >= len(present) / 2


def arrow_to_features(table: Any) -> List[Dict[str, Any]]:
    """pyarrow.Table（本载体形状）→ GeoJSON features（CRS 元数据在 schema）。"""
    pa, _ = _require_pa()
    names = table.column_names
    prop_names = [n for n in names if n != "geometry"]
    out: List[Dict[str, Any]] = []
    geom_col = table.column("geometry").to_pylist() if "geometry" in names else [b""] * table.num_rows
    pydict = {n: table.column(n).to_pylist() for n in prop_names}
    for i in range(table.num_rows):
        # schema 列集合即属性真值：null 保留为显式 None（不丢键）。
        props = {n: pydict[n][i] for n in prop_names}
        out.append({
            "type": "Feature",
            "geometry": _wkb_to_geometry(geom_col[i]),
            "properties": props,
        })
    return out


def iter_arrow_chunks(
    features: List[Dict[str, Any]], *, crs: Optional[str] = None, chunk_size: int = 4096
) -> Iterator[Any]:
    """分块传输：GeoJSON features → RecordBatch 迭代（零整表物化）。"""
    table = features_to_arrow(features, crs=crs)
    for batch in table.to_batches(max_chunksize=max(1, chunk_size)):
        yield batch


def _coercible(src: Any, dst: Any) -> bool:
    """Arrow 类型间是否允许"安全强制"（coerce 策略的白名单）。

    允许：任意 → string（数值/bool 的字符串化）；整型 ↔ 浮点（浮点 → 整型
    会截断，不安全 → 拒绝）；整型/浮点 ↔ bool；string → 数值（逐值可解析性
    由 cast 决定，失败仍报 drift）。
    """
    import pyarrow as pa

    if pa.types.is_null(src):
        return True
    if pa.types.is_string(dst) or pa.types.is_large_string(dst):
        return True
    src_num = pa.types.is_integer(src) or pa.types.is_floating(src) or pa.types.is_boolean(src)
    dst_num = pa.types.is_integer(dst) or pa.types.is_floating(dst) or pa.types.is_boolean(dst)
    if src_num and dst_num:
        if pa.types.is_integer(dst) and pa.types.is_floating(src):
            return False  # 1.5 → 1 是截断：不安全，诚实拒绝
        return True
    if (pa.types.is_string(src) or pa.types.is_large_string(src)) and dst_num:
        return True
    return False


def iter_features_to_arrow_batches(
    feature_batches: Iterable[List[Dict[str, Any]]],
    *,
    chunk_size: int = 4096,
    schema: Optional[Any] = None,
    on_schema_conflict: str = "strict",
    crs: Optional[str] = None,
) -> Iterator[Any]:
    """批式载体输入：``Iterable[List[feature]]`` → RecordBatch 迭代。

    - **批式不整存**：输入按批消费（生成器惰性），绝不为冻结 schema 而
      物化整个输入 —— 修复 ``iter_arrow_chunks`` 必须先持有全量 list 的缺口；
    - **schema 冻结**：批次 1（或显式 ``schema``）冻结列集合与列类型；
      后续批次逐列对齐冻结 schema（``pa.RecordBatch.from_arrays`` 批次级
      构建；空批次被容忍，不产出空批）；
    - **漂移策略** ``on_schema_conflict``：
      ``"strict"``（缺省）类型/结构不一致 → typed
      ``VectorCarrierSchemaDriftError``；
      ``"coerce"`` 先尝试安全强制（数值↔数值、任意→string、可解析 string→
      数值；浮点→整型截断不安全仍拒绝），失败同样 typed 拒绝 ——
      绝不静默错位；
    - 每batch 的 schema 元数据附该批 ``bbox`` / ``geometry_types``；
      汇总视图用 :func:`arrow_batches_geo_metadata` 折叠。
    """
    if on_schema_conflict not in ("strict", "coerce"):
        raise ValueError(
            f"on_schema_conflict must be 'strict' or 'coerce', got {on_schema_conflict!r}")
    if not arrow_available():
        raise VectorCarrierUnavailable(
            "GeoArrow carrier requires the optional 'pyarrow' dependency; "
            "fall back to feature payloads or enable the carrier",
        )
    pa, _ = _require_pa()

    frozen_types: Optional[Dict[str, Any]] = None
    frozen_base_schema: Any = None
    run_bbox: Optional[List[float]] = None
    run_types: set = set()

    for batch_idx, features in enumerate(feature_batches):
        if not features:
            continue  # 空批次容忍（无行 → 无输出批）
        if frozen_types is None:
            if schema is not None:
                pa_schema = schema if isinstance(schema, pa.Schema) else pa.schema(schema)
                frozen_types = {f.name: f.type for f in pa_schema if f.name != "geometry"}
            else:
                order, columns, _ = _features_to_columns(features)
                frozen_types = {}
                for k in order:
                    try:
                        arr = pa.array(
                            columns[k],
                            type=pa.string() if _mostly_str(columns[k]) else None)
                    except Exception as exc:
                        raise VectorCarrierEncodeError(
                            f"column '{k}' has mixed/unencodable value types "
                            f"(batch {batch_idx}): {_safe_exc_text(exc)}") from exc
                    frozen_types[k] = arr.type
            frozen_base_schema = pa.schema(
                [pa.field(k, t) for k, t in frozen_types.items()]
                + [pa.field("geometry", pa.binary())],
                metadata={"geo": json.dumps(_geo_metadata(crs))},
            )

        order, columns, wkb_list = _features_to_columns(features)
        unknown = [k for k in order if k not in frozen_types]
        if unknown:
            raise VectorCarrierSchemaDriftError(
                f"batch {batch_idx} introduces column(s) {unknown} not in the "
                "schema frozen at batch 0; structural drift cannot be coerced")

        arrays: List[Any] = []
        for k, frozen in frozen_types.items():
            if k not in columns:
                # round-1 review MAJOR：后续批次**缺失**冻结列 ≠ 全 null 列。
                # strict 直接 typed 拒绝（静默 null 填充会伪造数据丢失）；
                # coerce 显式 null 填充（声明过的宽松策略）。
                if on_schema_conflict == "strict":
                    raise VectorCarrierSchemaDriftError(
                        f"column '{k}' frozen at batch 0 vanished in batch "
                        f"{batch_idx}; vanishing columns are structural drift "
                        f"(policy=strict)")
                arrays.append(pa.array([None] * len(features), type=frozen))
                continue
            values = columns[k]
            try:
                arr = pa.array(values)
            except Exception as exc:
                raise VectorCarrierEncodeError(
                    f"column '{k}' has mixed/unencodable value types "
                    f"(batch {batch_idx}): {_safe_exc_text(exc)}") from exc
            if pa.types.is_null(arr.type) and not arr.type.equals(frozen):
                arr = arr.cast(frozen)  # 全 null 列适配任意冻结类型（稀疏 null 保留）
            elif not arr.type.equals(frozen):
                if on_schema_conflict == "coerce" and _coercible(arr.type, frozen):
                    try:
                        arr = arr.cast(frozen)
                    except Exception as exc:
                        raise VectorCarrierSchemaDriftError(
                            f"column '{k}' (batch {batch_idx}) drifted to "
                            f"{arr.type}; safe coercion to frozen {frozen} "
                            f"failed: {_safe_exc_text(exc)}") from exc
                else:
                    raise VectorCarrierSchemaDriftError(
                        f"column '{k}' (batch {batch_idx}) drifted: frozen "
                        f"{frozen}, got {arr.type} (policy={on_schema_conflict})")
            arrays.append(arr)
        arrays.append(pa.array(wkb_list, type=pa.binary()))

        batch_bbox, batch_types = _features_geo_stats(features)
        if batch_bbox is not None:
            if run_bbox is None:
                run_bbox = list(batch_bbox)
            else:
                run_bbox[0] = min(run_bbox[0], batch_bbox[0])
                run_bbox[1] = min(run_bbox[1], batch_bbox[1])
                run_bbox[2] = max(run_bbox[2], batch_bbox[2])
                run_bbox[3] = max(run_bbox[3], batch_bbox[3])
        run_types.update(batch_types)

        batch_schema = frozen_base_schema.with_metadata({
            b"geo": json.dumps(_geo_metadata(
                crs, bbox=batch_bbox, geometry_types=batch_types)),
        })
        step = max(1, chunk_size)
        for start in range(0, len(features), step):
            yield pa.RecordBatch.from_arrays(
                [a.slice(start, step) for a in arrays],
                schema=batch_schema,
            )


def arrow_batches_geo_metadata(batches: Iterable[Any]) -> Dict[str, Any]:
    """折叠批序列的 per-batch ``geo`` 元数据 → 汇总 bbox / geometry_types / crs。

    纯批次流没有"表级"挂载点 —— 本助手给调用方一个汇总视图（等价于整表
    编码时附在 schema 上的元数据）。无任何批携带元数据 → bbox 为 None。
    """
    if not arrow_available():
        raise VectorCarrierUnavailable(
            "GeoArrow carrier requires the optional 'pyarrow' dependency; "
            "fall back to feature payloads or enable the carrier",
        )
    combined_bbox: Optional[List[float]] = None
    types: set = set()
    crs: Optional[str] = None
    for batch in batches:
        raw = (batch.schema.metadata or {}).get(b"geo")
        if not raw:
            continue
        try:
            geo = json.loads(raw)
        except Exception:  # noqa: BLE001 - 外来元数据容错：跳过不可解析批
            continue
        col = (geo.get("columns") or {}).get("geometry", {})
        if crs is None and col.get("crs"):
            crs = col.get("crs")
        bbox = col.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            if combined_bbox is None:
                combined_bbox = [float(c) for c in bbox]
            else:
                combined_bbox[0] = min(combined_bbox[0], float(bbox[0]))
                combined_bbox[1] = min(combined_bbox[1], float(bbox[1]))
                combined_bbox[2] = max(combined_bbox[2], float(bbox[2]))
                combined_bbox[3] = max(combined_bbox[3], float(bbox[3]))
        for t in col.get("geometry_types") or []:
            types.add(t)
    return _geo_metadata(crs, bbox=combined_bbox, geometry_types=sorted(types))


def table_to_geoparquet(table: Any, path: str, *, compression: str = "zstd") -> None:
    """Arrow Table → GeoParquet（geo 元数据随 schema 落盘）。"""
    _, pq = _require_pa()
    pq.write_table(table, path, compression=compression)


def geoparquet_to_features(path: str) -> List[Dict[str, Any]]:
    """GeoParquet → GeoJSON features（走 pyarrow；依赖可选）。"""
    _, pq = _require_pa()
    table = pq.read_table(path)
    return arrow_to_features(table)


def table_crs(table: Any) -> Optional[str]:
    """从 schema geo 元数据读回 CRS（round-trip 保留证据）。"""
    import pyarrow  # noqa: F401

    meta = table.schema.metadata or {}
    raw = meta.get(b"geo")
    if not raw:
        return None
    try:
        geo = json.loads(raw)
        return geo.get("columns", {}).get("geometry", {}).get("crs")
    except Exception:  # noqa: BLE001
        return None
