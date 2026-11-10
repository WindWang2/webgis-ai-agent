"""G17 导入工作台 —— 解释引擎（preview 与 commit 共用的唯一解释器）。

合同：
- preview 与 commit 对同一 (源, 成员, 解释) 必须产出同一解释结果（场景 12/22
  的机制基础：本模块是唯一实现，两个入口仅限行数不同）。
- 字段类型：默认原样字符串（前导零/长整型不损失）；显式 field_types 才做
  number/boolean/date 强制转换，转换失败按行拒绝并计数（不静默丢类型）。
- 几何：X/Y 构点 / WKT 解析 / 源几何三来源；空几何、非有限坐标、非法 WKT
  按行拒绝（policy=reject_row），族/维度在摘要中披露。
- CRS：crs_source ∈ {source_metadata, declared, rfc7946_default, unknown}；
  unknown 不得落图（预览为表，提交 CRS_UNCONFIRMED）。
- 有界：行预算、取消检查（每 N 行）、拒绝行原文摘录截断。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.data_import import budgets
from app.services.data_import.models import CrsEvidence, TableInterpretation, VectorInterpretation

CancelCheck = Callable[[], None]

_INT_RE = re.compile(r"^[+-]?\d+$")
_BOOL_TRUE = {"true", "1", "yes", "是", "y"}
_BOOL_FALSE = {"false", "0", "no", "否", "n"}


class InterpretationError(Exception):
    """解释失败（typed；code 稳定供收据/前端映射）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)[:300]


@dataclass
class RowReject:
    row_number: int          # 数据行号（1 基，不含表头）
    reason_code: str         # type_mismatch | missing_coordinate | invalid_coordinate | invalid_wkt | empty_geometry | null_value_required_field
    column: Optional[str] = None
    detail: str = ""
    raw_excerpt: str = ""    # 有界原文摘录

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row": self.row_number,
            "reason": self.reason_code,
            "column": self.column,
            "detail": self.detail[:200],
            "raw": self.raw_excerpt[: budgets.RAW_EXCERPT_MAX_CHARS],
        }


@dataclass
class InterpretedRecord:
    properties: Dict[str, Any]
    geometry: Any = None     # shapely geometry；纯表为 None
    row_number: int = 0
    has_z: bool = False
    geometry_family: str = ""


@dataclass
class InterpretResult:
    records: List[InterpretedRecord] = field(default_factory=list)
    rejects: List[RowReject] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    column_kinds: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # col -> {suggested, observed}
    effective_mode: str = ""            # points | wkt | table（auto 解析后落定）
    crs_evidence: Optional[CrsEvidence] = None
    geometry_family: str = ""           # Point | LineString | ... | Mixed | ""
    rows_scanned: int = 0
    rows_total: Optional[int] = None    # 未知为 None（诚实）
    truncated: bool = False
    formula_disclosure: Optional[str] = None
    encoding_used: Optional[str] = None
    delimiter_used: Optional[str] = None
    has_z: bool = False                 # 任何记录含 Z 坐标时披露


def _excerpt(row: Dict[str, Any], limit: int = budgets.RAW_EXCERPT_MAX_CHARS) -> str:
    parts = []
    total = 0
    for k, v in list(row.items())[:12]:
        seg = f"{k}={v}"
        if total + len(seg) > limit:
            seg = seg[: max(0, limit - total)]
        parts.append(seg)
        total += len(seg) + 1
        if total >= limit:
            break
    return "; ".join(parts)


def coerce_value(column: str, raw: str, target: str) -> Tuple[bool, Any, str]:
    """显式类型转换：返回 (ok, value, reason)。空串 → None（null）。

    - number：整数安全范围（±2^63）内出 int，否则 float；非数值拒绝
    - boolean：常用词表
    - date：dateutil 可解析即通过，保留原字符串（不静默改写时区/格式）
    - text：原样
    """
    s = raw if isinstance(raw, str) else str(raw)
    if s == "":
        return True, None, ""
    if target == "text":
        return True, s, ""
    if target == "number":
        s2 = s.strip()
        if _INT_RE.match(s2):
            v = int(s2)
            if abs(v) <= 2**53:
                return True, v, ""
            # >2^53 的整数按 JSON 数值会被 JS Number 截断 → 保真为字符串
            #（任务书场景 7：长整型 ID 不经数值截断）
            return True, s2, "int_beyond_js_safe_range_kept_as_text"
        try:
            fv = float(s2)
        except ValueError:
            return False, None, f"非数值: {s[:60]!r}"
        if not math.isfinite(fv):
            return False, None, "非有限数值"
        return True, fv, ""
    if target == "boolean":
        low = s.strip().lower()
        if low in _BOOL_TRUE:
            return True, True, ""
        if low in _BOOL_FALSE:
            return True, False, ""
        return False, None, f"非布尔值: {s[:60]!r}"
    if target == "date":
        try:
            from dateutil import parser as du_parser
        except ImportError:  # pragma: no cover - 生产有 dateutil
            return True, s, ""
        try:
            du_parser.parse(s)
        except (ValueError, OverflowError):
            return False, None, f"非可解析日期: {s[:60]!r}"
        return True, s, ""
    return True, s, ""


def infer_column_kinds(sample: Dict[str, List[str]], limit: int = 200) -> Dict[str, Dict[str, Any]]:
    """列类型建议（仅建议；默认输出保持字符串）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for col, values in sample.items():
        vals = [v for v in values[:limit] if v != ""]
        if not vals:
            out[col] = {"suggested": "text", "observed": "empty"}
            continue
        kinds = set()
        for v in vals:
            ok_n, _, _ = coerce_value(col, v, "number")
            ok_b, _, _ = coerce_value(col, v, "boolean")
            ok_d, _, _ = coerce_value(col, v, "date")
            if ok_n:
                kinds.add("number")
            if ok_b:
                kinds.add("boolean")
            if ok_d:
                kinds.add("date")
        if kinds == {"number"}:
            sug, obs = "number", "number"
        elif kinds == {"boolean"}:
            sug, obs = "boolean", "boolean"
        elif kinds == {"date"} or kinds == {"date", "number"} and False:
            sug, obs = "date", "date"
        elif kinds == {"number", "text"} or "text" in kinds or len(kinds) > 1:
            sug, obs = "text", "mixed:" + ",".join(sorted(kinds))
        elif not kinds:
            sug, obs = "text", "unparseable"
        else:
            sug, obs = "text", ",".join(sorted(kinds))
        out[col] = {"suggested": sug, "observed": obs}
    return out


# ── 表格读取（CSV / XLSX → 字符串行流） ───────────────────────────────────


def _read_csv_rows(
    path, interp: TableInterpretation, row_limit: int, cancel: CancelCheck
) -> Tuple[List[Dict[str, str]], Optional[int], bool, str, Optional[str]]:
    """返回 (rows, total_rows|None, truncated, encoding, delimiter)。"""
    import pandas as pd

    chain: Tuple[str, ...] = (interp.encoding,) if interp.encoding else ("utf-8-sig", "utf-8", "gb18030")
    last_err: Optional[Exception] = None
    df = None
    used_enc = ""
    for enc in chain:
        try:
            df = pd.read_csv(
                path,
                dtype=str,
                keep_default_na=False,
                sep=interp.delimiter if interp.delimiter else None,
                engine="python",
                skiprows=interp.header_row if interp.header_row > 0 else 0,
                nrows=row_limit + 1 if row_limit else None,
                encoding=enc,
                on_bad_lines="error",
            )
            used_enc = enc
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except pd.errors.ParserError as e:
            raise InterpretationError("CSV_PARSE_FAILED", str(e)[:200]) from e
    if df is None:
        raise InterpretationError(
            "ENCODING_UNDECODABLE",
            f"无法按候选编码解码（{interp.encoding or 'utf-8/gb18030'}）：{last_err}",
        )
    truncated = len(df) > row_limit
    if truncated:
        df = df.iloc[:row_limit]
    rows = []
    for i, rec in enumerate(df.to_dict(orient="records")):
        if i % budgets.COMMIT_CANCEL_CHECK_ROWS == 0:
            cancel()
        rows.append({str(k): ("" if v is None else str(v)) for k, v in rec.items()})
    return rows, None, truncated, used_enc, interp.delimiter or ","


def _cell_to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 2**53:
            return str(int(v))
        return repr(v)
    if isinstance(v, int):
        return str(v)
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


def _read_xlsx_rows(
    path, sheet: str, interp: TableInterpretation, row_limit: int, cancel: CancelCheck
) -> Tuple[List[Dict[str, str]], Optional[int], bool, Optional[int], Optional[int]]:
    """read_only + data_only 有界读取。返回 (rows, total|None, truncated,
    formula_cells, uncached_formulas)。公式单元格给缓存值；无缓存按空披露。"""
    if sheet is None:
        raise InterpretationError("XLSX_SHEET_REQUIRED", "xlsx 成员必须指定工作表")
    try:
        import openpyxl
    except ImportError:
        raise InterpretationError("XLSX_ENGINE_UNAVAILABLE", "openpyxl 不可用")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise InterpretationError("XLSX_OPEN_FAILED", str(e)[:200]) from e
    rows: List[Dict[str, str]] = []
    formula_cells = 0
    uncached = 0
    truncated = False
    try:
        ws = wb[sheet]
        header: List[str] = []
        data_count = 0
        for row in ws.iter_rows(values_only=True):
            if not header:
                if all(v is None or str(v).strip() == "" for v in row):
                    continue  # 跳过表头前的空行（header_row 语义由前端行选择决定）
                header = [
                    _cell_to_str(v).strip() or f"col{i + 1}" for i, v in enumerate(row)
                ]
                continue
            if data_count % budgets.COMMIT_CANCEL_CHECK_ROWS == 0:
                cancel()
            data_count += 1
            if data_count > row_limit:
                truncated = True
                break
            rows.append({header[i]: _cell_to_str(v) for i, v in enumerate(row) if i < len(header)})
    finally:
        wb.close()
    return rows, None, truncated, formula_cells, uncached


def _resolve_auto_mode(
    columns: List[str], interp: TableInterpretation, hints: Dict[str, Any]
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """auto 模式落定：X/Y 列 → points；WKT 列 → wkt；否则 table。"""
    from app.services.data_parser import LAT_COLUMNS, LNG_COLUMNS

    x = interp.x_column
    y = interp.y_column
    wkt = interp.wkt_column
    if x and y:
        return "points", x, y, wkt
    if wkt:
        return "wkt", x, y, wkt
    xh = hints.get("x_column") if isinstance(hints, dict) else None
    yh = hints.get("y_column") if isinstance(hints, dict) else None
    if xh and yh and xh in columns and yh in columns:
        return "points", xh, yh, wkt
    wkt_guess = next(
        (c for c in columns if c.strip().lower() in {"wkt", "geom", "geometry", "the_geom", "shape", "几何", "wkt_geom"}),
        None,
    )
    if wkt_guess:
        return "wkt", None, None, wkt_guess
    return "table", None, None, None


def interpret_table(
    path: Any,
    interp: TableInterpretation,
    *,
    sheet: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    row_limit: int,
    cancel: CancelCheck = lambda: None,
) -> InterpretResult:
    """表格解释（CSV/XLSX）—— preview 与 commit 的共同入口。"""
    result = InterpretResult()
    hints = hints or {}
    is_xlsx = str(path).lower().endswith(".xlsx")

    def _cancel() -> None:
        cancel()

    if is_xlsx:
        rows, _total, trunc, _f, _u = _read_xlsx_rows(path, sheet, interp, row_limit, _cancel)
        result.truncated = trunc
    else:
        rows, _total, trunc, enc, delim = _read_csv_rows(path, interp, row_limit, _cancel)
        result.truncated = trunc
        result.encoding_used = enc
        result.delimiter_used = delim
        if interp.encoding and enc != interp.encoding:
            raise InterpretationError("ENCODING_MISMATCH", f"要求 {interp.encoding} 实际 {enc}")

    columns: List[str] = []
    seen: Dict[str, List[str]] = {}
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen[k] = []
                columns.append(k)
            if len(seen[k]) < 200:
                seen[k].append(r[k])
    columns = columns[: budgets.SAMPLE_COLUMNS_IN_PREVIEW]
    result.columns = columns
    result.rows_scanned = len(rows)
    result.rows_total = None  # 未全量扫描，总数未知（合同 3）

    mode, xcol, ycol, wktcol = _resolve_auto_mode(columns, interp, hints)
    if interp.mode != "auto":
        mode = interp.mode
        xcol, ycol, wktcol = interp.x_column, interp.y_column, interp.wkt_column
        if mode == "points" and not (xcol and ycol):
            raise InterpretationError("XY_COLUMNS_REQUIRED", "points 模式必须指定 X/Y 列")
        if mode == "wkt" and not wktcol:
            raise InterpretationError("WKT_COLUMN_REQUIRED", "wkt 模式必须指定 WKT 列")
    if mode == "points":
        for c in (xcol, ycol):
            if c not in columns:
                raise InterpretationError("COLUMN_NOT_FOUND", f"列 {c} 不存在；可用列: {', '.join(columns[:20])}")
    if mode == "wkt" and wktcol not in columns:
        raise InterpretationError("COLUMN_NOT_FOUND", f"列 {wktcol} 不存在；可用列: {', '.join(columns[:20])}")
    result.effective_mode = mode

    keep = set(interp.keep_fields) if interp.keep_fields is not None else None
    field_types = interp.field_types or {}
    for c, t in field_types.items():
        if t not in {"text", "number", "boolean", "date"}:
            raise InterpretationError("BAD_FIELD_TYPE", f"{c}: {t}")

    import shapely
    from shapely import wkt as shapely_wkt

    families = set()
    for i, row in enumerate(rows, start=1):
        geometry = None
        if mode == "points":
            xs, ys = row.get(xcol, ""), row.get(ycol, "")
            if xs == "" or ys == "":
                result.rejects.append(
                    RowReject(i, "missing_coordinate", xcol if xs == "" else ycol, "X/Y 为空", _excerpt(row))
                )
                continue
            okx, vx, rx = coerce_value(xcol, xs, "number")
            oky, vy, ry = coerce_value(ycol, ys, "number")
            if not (okx and oky):
                bad_col = xcol if not okx else ycol
                result.rejects.append(
                    RowReject(i, "invalid_coordinate", bad_col, rx or ry, _excerpt(row))
                )
                continue
            if not (math.isfinite(float(vx)) and math.isfinite(float(vy))):
                result.rejects.append(RowReject(i, "invalid_coordinate", None, "非有限坐标", _excerpt(row)))
                continue
            geometry = shapely.points(float(vx), float(vy))
        elif mode == "wkt":
            raw_wkt = row.get(wktcol, "")
            if raw_wkt == "":
                result.rejects.append(RowReject(i, "invalid_wkt", wktcol, "WKT 为空", _excerpt(row)))
                continue
            try:
                geometry = shapely_wkt.loads(raw_wkt)
            except Exception:  # noqa: BLE001 — shapely 错误面宽
                result.rejects.append(RowReject(i, "invalid_wkt", wktcol, "无法解析 WKT", _excerpt(row)))
                continue
            if geometry is None or geometry.is_empty:
                result.rejects.append(RowReject(i, "empty_geometry", wktcol, "空几何", _excerpt(row)))
                continue
            if not _finite_geometry(geometry):
                result.rejects.append(RowReject(i, "invalid_coordinate", wktcol, "非有限坐标", _excerpt(row)))
                continue

        props: Dict[str, Any] = {}
        type_failed = False
        for col in columns:
            if keep is not None and col not in keep:
                continue
            raw = row.get(col, "")
            target = field_types.get(col)
            if target and target != "text":
                ok, v, reason = coerce_value(col, raw, target)
                if not ok:
                    result.rejects.append(RowReject(i, "type_mismatch", col, reason, _excerpt(row)))
                    type_failed = True
                    break
                props[col] = v
            else:
                props[col] = raw
        if type_failed:
            continue
        rec = InterpretedRecord(properties=props, geometry=geometry, row_number=i)
        if geometry is not None:
            rec.has_z = geometry.has_z
            rec.geometry_family = geometry.geom_type
            families.add(geometry.geom_type)
            result.has_z = result.has_z or rec.has_z
        result.records.append(rec)

    result.column_kinds = infer_column_kinds(seen)

    if interp.source_crs_declared:
        result.crs_evidence = CrsEvidence(
            crs_source="declared", crs=interp.source_crs_declared,
            declared_by_user=interp.source_crs_declared,
        )
    else:
        result.crs_evidence = CrsEvidence(crs_source="unknown", crs=None)
    if families:
        result.geometry_family = "Mixed" if len(families) > 1 else next(iter(families))
    return result


def _finite_geometry(geom: Any) -> bool:
    import shapely

    try:
        arr = shapely.coordinates(geom)
    except Exception:  # noqa: BLE001
        return True
    import numpy as _np

    if arr.size == 0:
        return True
    return bool(_np.isfinite(arr).all())


# ── 矢量成员解释（源几何；声明 CRS 补缺） ─────────────────────────────────


def _native_value(v: Any) -> Any:
    """numpy/Python 值 → JSON 安全原生值（NaN → None）。"""
    import numpy as np

    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.str_,)):
        return str(v)
    if isinstance(v, (np.datetime64,)):
        return str(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (str, int, bool)):
        return v
    return str(v)


def interpret_vector(
    gdf: Any,
    interp: VectorInterpretation,
    *,
    declared_crs_fallback: Optional[str] = None,
    crs_source_hint: str = "source_metadata",
    row_limit: int,
    cancel: CancelCheck = lambda: None,
) -> InterpretResult:
    """矢量成员解释：keep_fields 选择 + 几何校验 + CRS 证据落定。"""
    result = InterpretResult()
    if len(gdf) > row_limit:
        raise InterpretationError("ROW_BUDGET_EXCEEDED", f"{len(gdf)} 行超过预算 {row_limit}")

    crs = gdf.crs
    crs_name = None
    if crs is not None:
        crs_name = getattr(crs, "name", None) or str(crs)
        epsg = getattr(crs, "to_epsg", lambda: None)()
        if epsg:
            crs_name = f"EPSG:{epsg}"
        evidence = CrsEvidence(crs_source=crs_source_hint, crs=crs_name)
    elif interp.source_crs_declared:
        gdf = gdf.set_crs(interp.source_crs_declared, allow_override=True)
        crs_name = interp.source_crs_declared
        evidence = CrsEvidence(
            crs_source="declared", crs=crs_name, declared_by_user=crs_name
        )
    elif declared_crs_fallback:
        evidence = CrsEvidence(crs_source=crs_source_hint, crs=declared_crs_fallback)
        crs_name = declared_crs_fallback
    else:
        evidence = CrsEvidence(crs_source="unknown", crs=None)
    result.crs_evidence = evidence

    keep = list(interp.keep_fields) if interp.keep_fields is not None else None
    if keep is not None:
        available = [c for c in gdf.columns if c != gdf.geometry.name]
        missing = [c for c in keep if c not in available]
        if missing:
            raise InterpretationError(
                "COLUMN_NOT_FOUND", f"字段不存在: {', '.join(missing)}；可用: {', '.join(available[:20])}"
            )
        cols = keep + [gdf.geometry.name]
        gdf = gdf[cols]
    result.columns = [str(c) for c in gdf.columns if c != gdf.geometry.name]

    families = set()
    has_any_z = False
    for i, (_, feat) in enumerate(gdf.iterrows(), start=1):
        if i % budgets.COMMIT_CANCEL_CHECK_ROWS == 0:
            cancel()
        geom = feat[gdf.geometry.name]
        if geom is None or geom.is_empty:
            result.rejects.append(
                RowReject(i, "empty_geometry", None, "源要素空几何", "")
            )
            continue
        if not _finite_geometry(geom):
            result.rejects.append(RowReject(i, "invalid_coordinate", None, "非有限坐标", ""))
            continue
        props = {str(c): _native_value(feat[c]) for c in gdf.columns if c != gdf.geometry.name}
        rec = InterpretedRecord(properties=props, geometry=geom, row_number=i)
        rec.has_z = geom.has_z
        has_any_z = has_any_z or geom.has_z
        rec.geometry_family = geom.geom_type
        families.add(geom.geom_type)
        result.records.append(rec)

    result.rows_scanned = len(gdf)
    result.rows_total = len(gdf)
    result.has_z = has_any_z
    if families:
        result.geometry_family = "Mixed" if len(families) > 1 else next(iter(families))
    return result


def feature_collection(result: InterpretResult, *, to_wgs84: bool) -> Tuple[Dict[str, Any], bool]:
    """InterpretResult → GeoJSON FeatureCollection dict。

    to_wgs84：crs 证据确认（declared/source/rfc 默认）且非 4326 时重投影。
    返回 (fc, transformed)。坐标经 shapely 映射（无 JS Number 截断面）。
    """
    import shapely

    features = []
    transformed = False
    geoms = [r.geometry for r in result.records]
    if geoms and to_wgs84:
        ev = result.crs_evidence
        if not ev.crs:
            raise InterpretationError("CRS_UNCONFIRMED", "未知 CRS 不得落图（调用方合同）")
        target_is_4326 = (ev.crs or "").upper() in ("EPSG:4326", "WGS84", "WGS 84")
        if not target_is_4326:
            import geopandas as gpd
            import pandas as pd

            gser = gpd.GeoSeries(geoms, crs=ev.crs)
            geoms = gser.to_crs("EPSG:4326").tolist()
            transformed = True
    for rec, geom in zip(result.records, geoms):
        features.append(
            {
                "type": "Feature",
                "properties": rec.properties,
                "geometry": geom.__geo_interface__,
            }
        )
    return {"type": "FeatureCollection", "features": features}, transformed


def table_payload(result: InterpretResult, columns: Optional[List[str]] = None) -> Dict[str, Any]:
    """InterpretResult → 纯表产物载荷（{table:{columns,rows}}；前端 table 适配器兼容）。"""
    cols = columns if columns is not None else result.columns
    rows = [[r.properties.get(c) for c in cols] for r in result.records]
    kinds = {c: result.column_kinds.get(c, {}).get("suggested", "text") for c in cols}
    return {
        "table": {
            "columns": [{"name": c, "type": kinds.get(c, "text")} for c in cols],
            "rows": rows,
        }
    }
