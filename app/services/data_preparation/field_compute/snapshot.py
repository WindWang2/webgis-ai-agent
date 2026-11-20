"""一致快照与版本锚（G19 / WP06）。

执行/预览开始时把 ref 载荷**一次性**冻结到内存（预算内），此后所有批次只读
该快照；发布前再核一次当前 store 的内容哈希，变化即 ``SNAPSHOT_CHANGED``
中止且不发布 —— 即使 G14 或 #2022 在第二批期间改了源，也不会产出混合
revision 结果。旧 executor 的一次 get/deepcopy 不足以覆盖分页路径，本模块
显式承担该语义。

输入形态（在 UI / API 如实披露，只支持一种时不宣称通用表格）：
- ``{"geojson": FeatureCollection, "crs": ..., "crs_source": ...}``；
- FeatureCollection 本体；
- ``{"rows": [dict, ...], "crs": ...}``（真正的非空间表格/行结果）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_preparation.field_compute.contracts import (
    MAX_ROWS, ExecError, InputAnchor,
)

_SCHEMA_SAMPLE_ROWS = 200


def canonical_hash(payload: Any) -> str:
    """canonical payload → sha256[:32]（内容锚；与 executor 同纪律但更长）。"""
    try:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str).encode("utf-8")
    except Exception:  # noqa: BLE001
        blob = str(payload).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _type_names(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """字段名 → 观测类型（前 200 行；多类型按排序拼接，变化即 schema 变化）。"""
    out: Dict[str, set[str]] = {}
    for props in rows[:_SCHEMA_SAMPLE_ROWS]:
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            if v is None:
                t = "null"
            elif isinstance(v, bool):
                t = "boolean"
            elif isinstance(v, int):
                t = "int"
            elif isinstance(v, float):
                t = "float"
            elif isinstance(v, str):
                t = "string"
            else:
                t = type(v).__name__
            out.setdefault(str(k), set()).add(t)
    return {k: "|".join(sorted(v)) for k, v in sorted(out.items())}


def schema_fingerprint(rows: List[Dict[str, Any]]) -> str:
    blob = json.dumps(_type_names(rows), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def parse_payload(payload: Any) -> Tuple[str, List[Dict[str, Any]], str, str, Dict[str, Any]]:
    """载荷 → (data_shape, rows[props], crs, crs_source, meta)。

    ``rows`` 是属性 dict 列表；几何从每个 feature 的 ``geometry`` 读取，
    由 ``extract_rows`` 配对。失败抛 ``ExecError(PAYLOAD_UNSUPPORTED)``。
    """
    if not isinstance(payload, dict):
        raise ExecError("PAYLOAD_UNSUPPORTED", "ref 载荷不是 dict")
    crs = str(payload.get("crs") or "")
    crs_source = str(payload.get("crs_source") or "")
    fc = payload.get("geojson")
    if isinstance(fc, dict):
        shape = "geojson"
        features = fc.get("features")
        if not isinstance(features, list):
            raise ExecError("PAYLOAD_UNSUPPORTED", "geojson.features 不是列表")
        if not crs and isinstance(fc.get("crs"), dict):
            crs = str(fc["crs"].get("properties", {}).get("name") or "")
        meta = {"fc": fc}
    elif payload.get("type") == "FeatureCollection" and isinstance(
            payload.get("features"), list):
        shape = "geojson"
        features = payload["features"]
        meta = {"fc": payload}
    elif isinstance(payload.get("rows"), list):
        shape = "table"
        features = None
        meta = {}
    else:
        raise ExecError("PAYLOAD_UNSUPPORTED",
                        "只支持 ref-backed GeoJSON FeatureCollection 或 rows 表格")
    if shape == "geojson":
        rows: List[Dict[str, Any]] = []
        for f in features or []:
            if not isinstance(f, dict):
                raise ExecError("PAYLOAD_UNSUPPORTED", "feature 不是 dict")
            props = f.get("properties")
            rows.append(dict(props) if isinstance(props, dict) else {})
    else:
        rows = []
        for r in payload["rows"]:
            if not isinstance(r, dict):
                raise ExecError("PAYLOAD_UNSUPPORTED", "table row 不是 dict")
            rows.append(dict(r))
    if len(rows) > MAX_ROWS:
        raise ExecError("ROW_LIMIT_EXCEEDED",
                        f"{len(rows)} 行超过本工作台上限 {MAX_ROWS}（不静默截断）")
    return shape, rows, crs, crs_source, meta


def extract_rows(payload: Any) -> Tuple[List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]], str]:
    """→ ([(props, geometry), ...], data_shape)。表格形态几何恒为 None。"""
    shape, rows, _crs, _src, meta = parse_payload(payload)
    if shape == "geojson":
        features = meta["fc"].get("features") or []
        out = []
        for r, f in zip(rows, features):
            geom = f.get("geometry") if isinstance(f, dict) else None
            out.append((r, geom if isinstance(geom, dict) else None))
        return out, shape
    return [(r, None) for r in rows], shape


def build_anchor(payload: Any, input_ref: str,
                 descriptor_fingerprint: str = "") -> InputAnchor:
    """冻结前构造版本锚（内容/schema/CRS/字段全集/行数）。"""
    shape, rows, crs, crs_source, _meta = parse_payload(payload)
    fields = sorted({k for r in rows for k in r.keys()})
    return InputAnchor(
        input_ref=input_ref,
        content_hash=canonical_hash(payload),
        schema_fingerprint=schema_fingerprint(rows),
        descriptor_fingerprint=str(descriptor_fingerprint or "")[:96],
        row_count=len(rows), crs=crs[:64], crs_source=crs_source[:32],
        available_fields=tuple(fields),
    )


def freeze(payload: Any) -> Any:
    """一次性深拷贝冻结（此后所有批次只读此对象，不回 store）。"""
    return copy.deepcopy(payload)


def verify_anchor(current_payload: Any, anchor: InputAnchor) -> None:
    """发布前二次核对：内容或 schema 任一变化即中止且不发布。"""
    _, rows, _, _, _ = parse_payload(current_payload)
    if canonical_hash(current_payload) != anchor.content_hash:
        raise ExecError("SNAPSHOT_CHANGED",
                        "输入内容在执行期间发生变化（内容锚不匹配）—— 已中止，未发布")
    if schema_fingerprint(rows) != anchor.schema_fingerprint:
        raise ExecError("SNAPSHOT_CHANGED",
                        "输入 schema 在执行期间发生变化 —— 已中止，未发布")


def build_output_payload(frozen_payload: Any,
                         new_rows: List[Dict[str, Any]],
                         *, subset: Optional[List[bool]] = None) -> Dict[str, Any]:
    """冻结载荷 → 新载荷（只替换属性/行；几何、CRS、原件字段一律保留）。"""
    payload = copy.deepcopy(frozen_payload)
    shape, _rows, _crs, _src, meta = parse_payload(payload)
    if shape == "geojson":
        fc = payload.get("geojson") if isinstance(payload.get("geojson"), dict) else payload
        features = fc.get("features") or []
        if len(features) != len(new_rows):
            raise ExecError("WRITE_FAILED", "输出行数与输入不一致")
        keep = [f for f, s in zip(features, subset)] if subset else features
        kept_rows = [r for r, s in zip(new_rows, subset)] if subset else new_rows
        for feat, props in zip(keep, kept_rows):
            if isinstance(feat, dict):
                feat["properties"] = props
        if subset:
            fc["features"] = keep
        payload["field_compute_derived"] = True
        return payload
    if subset:
        payload["rows"] = [r for r, s in zip(new_rows, subset) if s]
    else:
        payload["rows"] = new_rows
    payload["field_compute_derived"] = True
    return payload


__all__ = [
    "canonical_hash", "schema_fingerprint", "parse_payload", "extract_rows",
    "build_anchor", "freeze", "verify_anchor", "build_output_payload",
]
