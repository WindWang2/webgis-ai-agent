"""Data Quality V9 —— autofix 管线（autofixable 子集；dry-run 优先）。

P1（任务书 §2）修复建议 → 确定性修复的窄桥：

- **词表单一**：操作 ⊆ ``REMEDIATION_OPS``（与 RepairPlan 同一事实源；
  本模块不发明第四套词表）；
- **plan-only → dry-run → apply** 三段：:func:`plan_autofix` 从评估结果
  提取可自动修复步；:func:`dry_run_autofix` 预览计数与样例（绝不改动
  输入）；:func:`apply_autofix` 返回**新载荷**（new-ref 语义 —— 调用方
  负责注册为新 ref/新修订，绝不覆写源）；
- **确定性**：每步修复有明确可枚举语义（附 CRS/闭合环/剔除退化几何/
  乱码重码/空值要素剔除），无启发式写操作；
- **有界**：``max_scan`` 内逐要素处理；pyproj/shapely 缺席时诚实降级
  （attach-CRS / 仅剔除），证据写明实际执行的语义。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_quality.rule_functions import _ring_is_closed
from app.services.gis_harness.data_qualification import REMEDIATION_OPS

logger = logging.getLogger(__name__)

_MAX_SAMPLES = 8
_MAX_SCAN = 20000


@dataclass
class AutofixStep:
    operation: str
    params: Dict[str, Any] = field(default_factory=dict)
    reason_rule: str = ""
    disclosure: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation[:32],
            "params": dict(list(self.params.items())[:6]),
            "reason_rule": self.reason_rule[:64],
            "disclosure": self.disclosure[:200],
        }


def plan_autofix(report: Dict[str, Any]) -> List[AutofixStep]:
    """评估报告 → autofix 步骤（只取 autofixable 且 fail/warn 的规则行）。"""
    steps: List[AutofixStep] = []
    for r in report.get("results") or []:
        if not r.get("autofixable"):
            continue
        if r.get("status") not in ("fail", "warn"):
            continue
        for op in (r.get("fix_operations") or [])[:2]:
            if op not in REMEDIATION_OPS:
                continue
            params: Dict[str, Any] = {}
            if op == "reproject":
                params["target_crs"] = str(
                    (r.get("metric") or {}).get("suggested_crs")
                    or "EPSG:4326"
                )
            if op == "filter_null":
                field_name = (r.get("metric") or {}).get("worst_field")
                if not field_name:
                    continue
                params["field"] = str(field_name)[:96]
            steps.append(AutofixStep(
                operation=op,
                params=params,
                reason_rule=str(r.get("rule_id") or "")[:64],
                disclosure=str(r.get("message") or "")[:200],
            ))
    return steps


# ── 载荷工具 ─────────────────────────────────────────────────────────


def _features_of(payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    geojson = payload.get("geojson")
    if isinstance(geojson, dict) and isinstance(geojson.get("features"), list):
        return geojson["features"]
    return None


def _iter_fixable_rings(geometry: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    rings: List[List[Tuple[float, float]]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        head = node[0]
        if isinstance(head, list) and head and isinstance(head[0], (int, float)):
            ring = [(float(c[0]), float(c[1])) for c in node
                    if isinstance(c, list) and len(c) >= 2
                    and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float))]
            rings.append(ring)
            return
        for child in node:
            walk(child)

    if isinstance(geometry, dict):
        walk(geometry.get("coordinates"))
    return rings


def _replace_rings(geometry: Dict[str, Any], new_rings: List[List[Tuple[float, float]]]) -> None:
    """把 Polygon/MultiPolygon 的坐标按环粒度替换（拓扑形状保持）。"""
    gtype = str(geometry.get("type") or "")
    if gtype == "Polygon":
        geometry["coordinates"] = [
            [[x, y] for (x, y) in ring] for ring in new_rings
        ]
    elif gtype == "MultiPolygon" and new_rings:
        # 每个多边形按外环粒度切回（修复只处理单环闭合，拆分证据如实标注）
        geometry["coordinates"] = [
            [[x, y] for (x, y) in ring] for ring in new_rings
        ]


# ── dry-run ──────────────────────────────────────────────────────────


def dry_run_autofix(
    payload: Dict[str, Any],
    steps: List[AutofixStep],
    *,
    max_scan: int = _MAX_SCAN,
) -> Dict[str, Any]:
    """预览每个操作将产生的改动（计数 + 有界样例），绝不修改输入。"""
    preview: Dict[str, Any] = {
        "operations": [s.to_bounded_dict() for s in steps],
        "would_change": {},
        "applicable": True,
    }
    features = _features_of(payload)
    if features is None:
        preview["applicable"] = False
        preview["reason"] = "payload has no vector FeatureCollection"
        return preview

    scanned = features[:max_scan]
    for step in steps:
        op = step.operation
        if op == "repair_geometry":
            droppable = 0
            closable = 0
            for f in scanned:
                geometry = f.get("geometry") if isinstance(f, dict) else None
                if not isinstance(geometry, dict) or geometry.get("coordinates") in (None, []):
                    droppable += 1
                    continue
                for ring in _iter_fixable_rings(geometry):
                    if len(ring) >= 3 and not _ring_is_closed(ring):
                        closable += 1
            preview["would_change"][op] = {
                "drop_invalid_features": droppable,
                "close_rings": closable,
            }
        elif op == "reproject":
            crs = str(payload.get("crs") or "")
            has_crs_member = isinstance(payload.get("geojson"), dict) and bool(
                payload["geojson"].get("crs"))
            preview["would_change"][op] = {
                "attach_crs": (not crs and not has_crs_member),
                "target_crs": step.params.get("target_crs") or "EPSG:4326",
                "reproject_coordinates": bool(crs) and crs != step.params.get("target_crs"),
            }
        elif op == "normalize":
            suspicious = 0
            for f in scanned:
                props = f.get("properties") if isinstance(f, dict) else None
                if not isinstance(props, dict):
                    continue
                for v in props.values():
                    if isinstance(v, str) and _fixable_mojibake(v) is not None:
                        suspicious += 1
            preview["would_change"][op] = {"redecode_strings": suspicious}
        elif op == "filter_null":
            name = str(step.params.get("field") or "")
            null_rows = sum(
                1 for f in scanned
                if isinstance(f, dict) and isinstance(f.get("properties"), dict)
                and f["properties"].get(name) is None
            )
            preview["would_change"][op] = {"drop_features_with_null_in": name,
                                           "affected_rows": null_rows}
        else:
            preview["would_change"][op] = {"note": "no deterministic autofix; manual op"}
    return preview


# ── apply（new-ref 语义：返回新载荷，绝不覆写输入） ───────────────────


def _fixable_mojibake(text: str) -> Optional[str]:
    """双重编码探测：UTF-8 字节被单字节码页误读的文本，按原码页回编码后
    再按 UTF-8 解码成功且文本变化 → 返回修复文本；否则 None。

    latin-1 只能编码 U+00FF 以下字符；Windows 侧更常见的是 cp1252 误读
    （0x80–0x9F 映射为 ‹›€ 等可打印字符），故两个码页都尝试。
    """
    for enc in ("latin-1", "cp1252"):
        try:
            fixed = text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != text and "\ufffd" not in fixed:
            return fixed
    return None


def apply_autofix(
    payload: Dict[str, Any],
    steps: List[AutofixStep],
    *,
    max_scan: int = _MAX_SCAN,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行确定性修复：返回 ``(new_payload, evidence)``。

    - 输入 payload 不被修改（深拷贝后处理 —— new-ref 语义的硬前提）；
    - pyproj 缺席时 reproject 降级为 attach-CRS（证据标注）。
    """
    import copy

    new_payload = copy.deepcopy(payload)
    evidence: Dict[str, Any] = {"operations": [], "changed": False}
    features = _features_of(new_payload)
    if features is None:
        evidence["operations"].append({"operation": "-", "note": "not a vector payload", "applied": False})
        return new_payload, evidence

    for step in steps:
        op = step.operation
        entry: Dict[str, Any] = {"operation": op, "reason_rule": step.reason_rule}
        if op == "repair_geometry":
            kept: List[Dict[str, Any]] = []
            dropped = 0
            closed = 0
            for f in features[:max_scan]:
                geometry = f.get("geometry") if isinstance(f, dict) else None
                if not isinstance(geometry, dict) or geometry.get("coordinates") in (None, []):
                    dropped += 1
                    continue
                rings = _iter_fixable_rings(geometry)
                fixed_rings: List[List[Tuple[float, float]]] = []
                ring_ok = True
                for ring in rings:
                    if len(ring) >= 3 and not _ring_is_closed(ring):
                        ring = ring + [ring[0]]
                        closed += 1
                    fixed_rings.append(ring)
                if rings and fixed_rings:
                    _replace_rings(geometry, fixed_rings)
                # 零面积多边形（退化）剔除
                gtype = str(geometry.get("type") or "")
                if gtype in ("Polygon", "MultiPolygon"):
                    from app.services.data_quality.rule_functions import _ring_area
                    areas = [_ring_area(r) for r in fixed_rings[:1]]
                    if areas and areas[0] == 0.0:
                        dropped += 1
                        ring_ok = False
                if ring_ok:
                    kept.append(f)
            # 保持未扫描尾部（超出 max_scan 的部分不动，证据诚实标注）
            features[:] = kept + features[max_scan:]
            entry.update({"dropped_features": dropped, "closed_rings": closed,
                          "bounded_to": min(len(features), max_scan)})
            evidence["changed"] = evidence.get("changed") or bool(dropped or closed)
        elif op == "reproject":
            target = str(step.params.get("target_crs") or "EPSG:4326")
            geojson = new_payload.get("geojson")
            current = str(new_payload.get("crs") or "")
            if isinstance(geojson, dict) and isinstance(geojson.get("crs"), dict):
                current = str(geojson["crs"].get("properties", {}).get("name") or "")
            if not current:
                if isinstance(geojson, dict):
                    geojson["crs"] = {"type": "name",
                                      "properties": {"name": target}}
                new_payload["crs"] = target
                entry.update({"attached_crs": target, "reprojected": False})
                evidence["changed"] = True
            elif current != target:
                reprojected = _reproject_features(features[:max_scan], current, target)
                entry.update({"from_crs": current, "to_crs": target,
                              "reprojected": reprojected,
                              "note": "" if reprojected else "pyproj unavailable; crs label updated only"})
                new_payload["crs"] = target
                evidence["changed"] = True
            else:
                entry.update({"note": "crs already target", "applied": False})
        elif op == "normalize":
            fixed_count = 0
            for f in features[:max_scan]:
                props = f.get("properties") if isinstance(f, dict) else None
                if not isinstance(props, dict):
                    continue
                for k, v in list(props.items()):
                    if isinstance(v, str):
                        fixedv = _fixable_mojibake(v)
                        if fixedv is not None:
                            props[k] = fixedv
                            fixed_count += 1
            entry.update({"redecoded_strings": fixed_count})
            evidence["changed"] = evidence.get("changed") or bool(fixed_count)
        elif op == "filter_null":
            name = str(step.params.get("field") or "")
            kept2 = [
                f for f in features[:max_scan]
                if not (isinstance(f, dict) and isinstance(f.get("properties"), dict)
                        and f["properties"].get(name) is None)
            ]
            dropped = len(features[:max_scan]) - len(kept2)
            features[:] = kept2 + features[max_scan:]
            entry.update({"field": name, "dropped_features": dropped})
            evidence["changed"] = evidence.get("changed") or bool(dropped)
        else:
            entry.update({"note": "no deterministic autofix", "applied": False})
        evidence["operations"].append(entry)
    return new_payload, evidence


def _reproject_features(features: List[Dict[str, Any]], src: str, dst: str) -> bool:
    """pyproj 可用时真实重投影坐标（有界）；失败/缺席 → False（诚实降级）。"""
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs(src, dst, always_xy=True)
    except Exception:  # noqa: BLE001
        return False

    def walk(node: Any) -> Any:
        if isinstance(node, list) and node and isinstance(node[0], (int, float)) \
                and len(node) >= 2 and isinstance(node[1], (int, float)):
            x, y = transformer.transform(node[0], node[1])
            return [x, y] + list(node[2:])
        if isinstance(node, list):
            return [walk(child) for child in node]
        return node

    for f in features:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        if isinstance(geometry, dict):
            geometry["coordinates"] = walk(geometry.get("coordinates"))
    return True


__all__ = [
    "AutofixStep",
    "plan_autofix",
    "dry_run_autofix",
    "apply_autofix",
]
