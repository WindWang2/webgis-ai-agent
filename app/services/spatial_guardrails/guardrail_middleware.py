"""空间反幻觉守护网关编排器（ADR-0195 D7 / spec §7）。

两个唯一事实源挂载点共享本网关：
- `ToolDispatchService.dispatch`（去重后、分析复用前）→ check_tool_args；
- `lifecycle_engine.apply_mutation`（取锁前）→ check_intent。

纪律：
- 网关内部任何异常 → fail-open（记 GUARDRAIL_INTERNAL_ERROR 警示放行），
  守护网关绝不因自身故障瘫痪调度面；
- 只依赖标准库 + 同包校验器；对两个挂载方保持单向依赖（只被 import）。
"""
from __future__ import annotations

import dataclasses
import json
import math
import threading
import time

from app.services.spatial_guardrails.admin_division_verifier import verify_code
from app.services.spatial_guardrails.errors import (
    GeographicImpossibilityError,
    SpatialGuardrailError,
)
from app.services.spatial_guardrails.landmask_validator import (
    LAND_FACILITY_KINDS,
    WATER_FACILITY_KINDS,
    SurfaceZone,
    classify_point,
)
from app.services.spatial_guardrails.latlon_inversion_detector import (
    detect_pair,
)
from app.services.spatial_guardrails.redlines import (
    RedlineRegistry,
    check_area_budget,
)
from app.services.spatial_guardrails.topology_checks import (
    validate_linestring,
    validate_polygon,
)
from app.services.spatial_guardrails.types import (
    CODE_BBOX_AREA_BUDGET_EXCEEDED,
    CODE_FABRICATED_ADMIN_CODE,
    CODE_GUARDRAIL_INTERNAL_ERROR,
    CODE_L1_INVALID_COORDINATE,
    CODE_LATLON_INVERTED_AUTO_FLIP,
    CODE_SUSPICIOUS_LATLON_ORDER,
    CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE,
    DefenseMode,
    GuardLevel,
    GuardrailConfig,
    GuardrailIssue,
    GuardrailVerdict,
    guardrails_enabled,
)

_GEOJSON_GEOMETRY_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon",
}
_GEOJSON_TYPES = _GEOJSON_GEOMETRY_TYPES | {"Feature", "FeatureCollection"}

# 坐标形态抽取（键名白名单，保守避免把普通二元数组误当坐标）
_PAIR_KEYS = {
    "center", "location", "coordinates", "point", "origin", "destination",
    "centroid", "lnglat", "latlng", "coord", "coords",
}
_BBOX_KEYS = {"bbox", "bounds", "extent"}
_ADMIN_KEYS = {
    "adcode", "admin_code", "city_code", "county_code", "district_code",
    "province_code", "area_code", "region_code",
}
_FACILITY_KEYS = {"kind", "facility", "facility_kind", "facility_type",
                  "poi_type", "poi_kind", "category"}
_LATLNG_KEYS = {"lng": "lat", "lon": "lat", "longitude": "latitude"}
_ALL_FACILITY_KINDS = LAND_FACILITY_KINDS | WATER_FACILITY_KINDS


class _ScanState:
    """单次校验的累积状态：issues + 是否发生 AUTO_FLIP 突变。"""

    def __init__(self, config: GuardrailConfig) -> None:
        self.config = config
        self.issues: list[GuardrailIssue] = []
        self.mutated = False

    def blocking_issues(self) -> list[GuardrailIssue]:
        return [i for i in self.issues if i.mode == DefenseMode.BLOCK]

    def block(self, level: GuardLevel, code: str, message: str,
              location: str, evidence: dict) -> None:
        self.issues.append(GuardrailIssue(
            level=level, mode=DefenseMode.BLOCK, code=code,
            message=message, location=location, evidence=evidence,
        ))

    def warn(self, level: GuardLevel, code: str, message: str,
             location: str, evidence: dict) -> None:
        self.issues.append(GuardrailIssue(
            level=level, mode=DefenseMode.WARN_DEGRADE, code=code,
            message=message, location=location, evidence=evidence,
        ))


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class SpatialGuardrails:
    """守护网关：参数/GeoJSON/MapSpec 意图的统一校验入口。"""

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self._config = config or GuardrailConfig.from_env()
        self._redlines = (
            RedlineRegistry.from_json(self._config.redlines_json)
            if self._config.redlines_json
            else RedlineRegistry()
        )

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def check_tool_args(self, tool_name: str, args) -> GuardrailVerdict:
        """工具参数全层校验（L1–L4）。BLOCK → 调用方拒绝执行工具；
        AUTO_FLIP → 参数已就地纠偏（verdict.mutated=True）。"""
        t0 = time.perf_counter()
        verdict = GuardrailVerdict()
        if not guardrails_enabled():
            verdict.duration_ms = (time.perf_counter() - t0) * 1000.0
            return verdict
        state = _ScanState(self._config)
        try:
            if isinstance(args, dict):
                self._scan_value(args, "$", state, facility=None)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
        except Exception as exc:  # noqa: BLE001 — fail-open 纪律
            state.warn(
                GuardLevel.L1_FORMAT_CRS,
                CODE_GUARDRAIL_INTERNAL_ERROR,
                f"守护网关内部异常（已放行不阻断）：{type(exc).__name__}: {exc}",
                "$",
                {"tool": tool_name},
            )
        self._finalize(verdict, state, t0)
        return verdict

    def check_geojson(self, obj, *, location: str = "$") -> GuardrailVerdict:
        """GeoJSON 对象全层校验。"""
        t0 = time.perf_counter()
        verdict = GuardrailVerdict()
        if not guardrails_enabled():
            verdict.duration_ms = (time.perf_counter() - t0) * 1000.0
            return verdict
        state = _ScanState(self._config)
        try:
            self._scan_geojson(obj, location, state, facility=None)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
        except Exception as exc:  # noqa: BLE001 — fail-open 纪律
            state.warn(
                GuardLevel.L1_FORMAT_CRS,
                CODE_GUARDRAIL_INTERNAL_ERROR,
                f"守护网关内部异常（已放行不阻断）：{type(exc).__name__}: {exc}",
                location,
                {},
            )
        self._finalize(verdict, state, t0)
        return verdict

    def check_intent(self, intent):
        """MapSpec 意图锁前校验：返回 (可能被纠偏的 intent, verdict)。

        BLOCK → 调用方（apply_mutation）返回错误 MapSpecResult，不占锁；
        AUTO_FLIP → SetViewIntent 以修正后的 center 重建，UpsertLayerIntent
        的 layer/source_data 已就地纠偏。
        """
        t0 = time.perf_counter()
        verdict = GuardrailVerdict()
        if not guardrails_enabled():
            verdict.duration_ms = (time.perf_counter() - t0) * 1000.0
            return intent, verdict
        state = _ScanState(self._config)
        try:
            intent = self._scan_intent(intent, state)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
        except Exception as exc:  # noqa: BLE001 — fail-open 纪律
            state.warn(
                GuardLevel.L1_FORMAT_CRS,
                CODE_GUARDRAIL_INTERNAL_ERROR,
                f"守护网关内部异常（已放行不阻断）：{type(exc).__name__}: {exc}",
                "$",
                {"intent": type(intent).__name__},
            )
        self._finalize(verdict, state, t0)
        return intent, verdict

    # ------------------------------------------------------------------
    # 意图分发（鸭子类型，避免对 lifecycle_engine 的反向依赖）
    # ------------------------------------------------------------------
    def _scan_intent(self, intent, state: _ScanState):
        cls_name = type(intent).__name__
        if cls_name == "SetViewIntent":
            center = getattr(intent, "center", None)
            if isinstance(center, (list, tuple)) and len(center) == 2:
                fixed = dict(center=center)
                self._check_pair_in_container(fixed, "center", "$.center", state,
                                              facility=None)
                if state.blocking_issues():
                    return intent
                if fixed["center"] != list(center):
                    return dataclasses.replace(
                        intent,
                        center=[float(fixed["center"][0]), float(fixed["center"][1])],
                    )
            return intent
        if cls_name == "InitProjectIntent":
            view = getattr(intent, "view", None)
            if isinstance(view, dict):
                self._scan_value(view, "$.view", state, facility=None)
            return intent
        if cls_name == "UpsertLayerIntent":
            layer = getattr(intent, "layer", None)
            if isinstance(layer, dict):
                self._scan_value(layer, "$.layer", state, facility=None)
            source_data = getattr(intent, "source_data", None)
            if source_data is not None and isinstance(source_data, dict):
                self._scan_geojson(source_data, "$.source_data", state, facility=None)
            return intent
        # 其余意图不携带空间几何：显式通过
        return intent

    # ------------------------------------------------------------------
    # 递归扫描
    # ------------------------------------------------------------------
    def _scan_value(self, value, path: str, state: _ScanState, *, facility) -> None:
        if isinstance(value, dict):
            geo_type = value.get("type")
            if isinstance(geo_type, str) and geo_type in _GEOJSON_TYPES:
                self._scan_geojson(value, path, state, facility)
                return
            kind = self._detect_facility(value, facility)
            for key in _ADMIN_KEYS:
                if key in value:
                    self._check_admin_code(value[key], f"{path}.{key}", state)
            for key in _BBOX_KEYS:
                raw = value.get(key)
                if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(_is_number(v) for v in raw):
                    self._check_bbox([float(v) for v in raw], f"{path}.{key}", state)
            for lng_key, lat_key in _LATLNG_KEYS.items():
                if lng_key in value and lat_key in value \
                        and _is_number(value[lng_key]) and _is_number(value[lat_key]):
                    self._check_lnglat_dict(value, lng_key, lat_key, path, state)
                    break
            for key, item in value.items():
                if key in _PAIR_KEYS and isinstance(item, (list, tuple)) \
                        and len(item) == 2 and all(_is_number(v) for v in item):
                    self._check_pair_in_container(value, key, f"{path}.{key}", state,
                                                  facility=kind)
                else:
                    self._scan_value(item, f"{path}.{key}", state, facility=kind)
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                self._scan_value(item, f"{path}[{i}]", state, facility=facility)

    def _detect_facility(self, value: dict, inherited) -> str | None:
        for key in _FACILITY_KEYS:
            v = value.get(key)
            if isinstance(v, str) and v.lower() in _ALL_FACILITY_KINDS:
                return v.lower()
        return inherited

    def _scan_geojson(self, geo, path: str, state: _ScanState, *, facility) -> None:
        if not isinstance(geo, dict):
            return
        geo_type = geo.get("type")
        if geo_type == "FeatureCollection":
            features = geo.get("features")
            if isinstance(features, list):
                for i, feat in enumerate(features):
                    self._scan_geojson(feat, f"{path}.features[{i}]", state,
                                       facility=facility)
            return
        if geo_type == "Feature":
            props = geo.get("properties")
            kind = facility
            if isinstance(props, dict):
                kind = self._detect_facility(props, facility)
                for key in _ADMIN_KEYS:
                    if key in props:
                        self._check_admin_code(props[key], f"{path}.properties.{key}",
                                               state)
            self._scan_geojson(geo.get("geometry"), f"{path}.geometry", state,
                               facility=kind)
            return
        if geo_type in _GEOJSON_GEOMETRY_TYPES:
            coords = geo.get("coordinates")
            if geo_type == "Point":
                if isinstance(coords, (list, tuple)) and len(coords) >= 2 \
                        and all(_is_number(v) for v in coords[:2]):
                    self._check_pair_in_container(geo, "coordinates", path, state,
                                                  facility=facility,
                                                  allow_extra_z=True)
            elif geo_type == "MultiPoint":
                self._scan_vertex_list(coords, path, state, facility=facility,
                                       treat_all_ocean_as_block=True)
            elif geo_type in ("LineString", "MultiLineString"):
                lines = [coords] if geo_type == "LineString" else (
                    coords if isinstance(coords, list) else []
                )
                for i, line in enumerate(lines):
                    self._check_linestring(line, f"{path}[{i}]" if geo_type != "LineString"
                                           else path, state, facility=facility)
            elif geo_type in ("Polygon", "MultiPolygon"):
                polys = [coords] if geo_type == "Polygon" else (
                    coords if isinstance(coords, list) else []
                )
                for i, poly in enumerate(polys):
                    self._check_polygon(poly,
                                        f"{path}[{i}]" if geo_type != "Polygon" else path,
                                        state, facility=facility)

    # ------------------------------------------------------------------
    # 单项校验
    # ------------------------------------------------------------------
    def _check_admin_code(self, raw, path: str, state: _ScanState) -> None:
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            return
        code = str(raw).strip()
        report = verify_code(code)
        if not report.ok:
            state.block(
                GuardLevel.L1_FORMAT_CRS,
                CODE_FABRICATED_ADMIN_CODE,
                f"行政区划代码 {code!r} 在第一道防线被截获：{report.suggestion}",
                path,
                {"code": code, "suggestion": report.suggestion,
                 "edition": report.issue_code},
            )
        elif report.issue_code == CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE:
            state.warn(
                GuardLevel.L3_LANDMASK_PLAUSIBILITY,
                CODE_UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE,
                f"行政区划代码 {code} 结构合法但未收录（{report.suggestion or '近似码无'}）"
                "—— 降级放行，请人工复核",
                path,
                {"code": code, "suggestion": report.suggestion},
            )

    def _check_bbox(self, bbox: list[float], path: str, state: _ScanState) -> None:
        minx, miny, maxx, maxy = bbox
        if not (-180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0
                and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0):
            state.block(
                GuardLevel.L1_FORMAT_CRS,
                CODE_L1_INVALID_COORDINATE,
                f"bbox {bbox} 值域非法（经度 ∈ [-180,180]，纬度 ∈ [-90,90]）",
                path,
                {"bbox": bbox},
            )
            return
        try:
            self._redlines.check_bbox(bbox, action="fetch")
            check_area_budget(bbox, max_km2=self._config.max_bbox_km2)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
            if exc.issue.code == CODE_BBOX_AREA_BUDGET_EXCEEDED:
                return

    def _check_pair_in_container(self, container, key, path: str,
                                 state: _ScanState, *, facility,
                                 allow_extra_z: bool = False) -> None:
        """container[key]（或 container 本身，见 _check_lnglat_dict）上的
        点坐标全层校验：L1 值域 → 倒置检测/AUTO_FLIP → L2/L3 海陆常识。"""
        pair = container[key]
        a, b = float(pair[0]), float(pair[1])
        extra = "" if not allow_extra_z or len(pair) <= 2 else "（含高程维）"
        location = f"{path}{extra}"
        if not self._domain_ok(a, b):
            state.block(
                GuardLevel.L1_FORMAT_CRS,
                CODE_L1_INVALID_COORDINATE,
                f"坐标 {json.dumps(pair, ensure_ascii=False)} 双解释值域非法"
                "（经度 ∈ [-180,180]，纬度 ∈ [-90,90]）",
                location,
                {"pair": [a, b]},
            )
            return
        report = detect_pair([a, b], ocean_confirm_km=self._config.ocean_confirm_km)
        lng, lat = a, b
        suspicious_inversion = False
        if report.is_inverted:
            if report.confidence >= self._config.auto_flip_min_confidence:
                lng, lat = report.corrected
                container[key] = [lng, lat]
                state.mutated = True
                state.issues.append(GuardrailIssue(
                    level=GuardLevel.L1_FORMAT_CRS,
                    mode=DefenseMode.AUTO_FLIP,
                    code=CODE_LATLON_INVERTED_AUTO_FLIP,
                    message=(
                        f"检出经纬度倒置 [a={a}, b={b}]（置信度 "
                        f"{report.confidence:.2f}），已自动纠偏为 [{lng}, {lat}]"
                    ),
                    location=location,
                    evidence=dict(report.evidence, confidence=report.confidence),
                    corrected=[lng, lat],
                ))
            else:
                # 置信不足：轴序两可 → 点位本身不可信，只警示不做 L2/L3 硬阻断
                # （宁可 WARN 放行，绝不基于未确认的坐标冤杀）。
                suspicious_inversion = True
                state.warn(
                    GuardLevel.L1_FORMAT_CRS,
                    CODE_SUSPICIOUS_LATLON_ORDER,
                    f"坐标 [a={a}, b={b}] 疑似经纬度倒置（置信度 "
                    f"{report.confidence:.2f} 低于纠偏阈值），未自动修改",
                    location,
                    dict(report.evidence, confidence=report.confidence),
                )
        self._facility_zone_checks(
            lng, lat,
            None if suspicious_inversion else facility,
            location, state,
        )

    def _check_lnglat_dict(self, container: dict, lng_key: str, lat_key: str,
                           path: str, state: _ScanState) -> None:
        a, b = float(container[lng_key]), float(container[lat_key])
        location = f"{path}.{lng_key}/{lat_key}"
        if not self._domain_ok(a, b):
            state.block(
                GuardLevel.L1_FORMAT_CRS,
                CODE_L1_INVALID_COORDINATE,
                f"坐标 ({a}, {b}) 双解释值域非法",
                location,
                {"pair": [a, b]},
            )
            return
        report = detect_pair([a, b], ocean_confirm_km=self._config.ocean_confirm_km)
        lng, lat = a, b
        if report.is_inverted:
            if report.confidence >= self._config.auto_flip_min_confidence:
                lng, lat = report.corrected
                container[lng_key], container[lat_key] = lng, lat
                state.mutated = True
                state.issues.append(GuardrailIssue(
                    level=GuardLevel.L1_FORMAT_CRS,
                    mode=DefenseMode.AUTO_FLIP,
                    code=CODE_LATLON_INVERTED_AUTO_FLIP,
                    message=f"检出经纬度倒置并自动纠偏为 [{lng}, {lat}]",
                    location=location,
                    evidence=dict(report.evidence, confidence=report.confidence),
                    corrected=[lng, lat],
                ))
            else:
                state.warn(
                    GuardLevel.L1_FORMAT_CRS,
                    CODE_SUSPICIOUS_LATLON_ORDER,
                    f"坐标 ({a}, {b}) 疑似经纬度倒置（置信度不足），未自动修改",
                    location,
                    dict(report.evidence, confidence=report.confidence),
                )
        self._facility_zone_checks(lng, lat, None, location, state)

    @staticmethod
    def _domain_ok(a: float, b: float) -> bool:
        a_lng_ok = abs(a) <= 180.0 and abs(b) <= 90.0
        b_lng_ok = abs(b) <= 180.0 and abs(a) <= 90.0
        return a_lng_ok or b_lng_ok

    def _facility_zone_checks(self, lng: float, lat: float, facility,
                              location: str, state: _ScanState) -> None:
        try:
            issues = self._validate_point(lng, lat, facility, location)
        except GeographicImpossibilityError as exc:
            state.issues.append(exc.issue)
            return
        state.issues.extend(issues)

    def _validate_point(self, lng: float, lat: float, facility,
                        location: str = "$"):
        from app.services.spatial_guardrails.landmask_validator import (
            validate_facility_point,
        )

        return validate_facility_point(
            lng, lat,
            facility_kind=facility,
            ocean_confirm_km=self._config.ocean_confirm_km,
            location=location,
        )

    def _check_linestring(self, line, path: str, state: _ScanState, *,
                          facility) -> None:
        if not isinstance(line, list) or len(line) < 2:
            return
        bad = [c for c in line if not (isinstance(c, (list, tuple)) and len(c) >= 2
                                       and all(_is_number(v) for v in c[:2]))]
        if bad:
            state.block(
                GuardLevel.L1_FORMAT_CRS,
                CODE_L1_INVALID_COORDINATE,
                f"线要素含 {len(bad)} 个非法顶点",
                path,
                {"invalid_vertices": len(bad)},
            )
            return
        try:
            validate_linestring(line, teleport_km=self._config.teleport_km,
                                location=path)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
            return
        self._sample_zone_checks(line, path, state, facility=facility)

    def _check_polygon(self, rings, path: str, state: _ScanState, *, facility) -> None:
        if not isinstance(rings, list) or not rings:
            return
        try:
            validate_polygon(rings, location=path)
        except SpatialGuardrailError as exc:
            state.issues.append(exc.issue)
            return
        outer = rings[0]
        if not isinstance(outer, list) or len(outer) < 3:
            return
        lats = [float(c[1]) for c in outer if isinstance(c, (list, tuple)) and len(c) >= 2]
        lngs = [float(c[0]) for c in outer if isinstance(c, (list, tuple)) and len(c) >= 2]
        if not lngs:
            return
        self._sample_zone_checks(
            [[(min(lngs) + max(lngs)) / 2.0, (min(lats) + max(lats)) / 2.0]],
            f"{path}.centroid",
            state,
            facility=facility,
        )

    def _scan_vertex_list(self, coords, path: str, state: _ScanState, *,
                          facility, treat_all_ocean_as_block: bool = False) -> None:
        if not isinstance(coords, list):
            return
        self._sample_zone_checks(coords, path, state, facility=facility,
                                 treat_all_ocean_as_block=treat_all_ocean_as_block)

    def _sample_zone_checks(self, vertices, path: str, state: _ScanState, *,
                            facility, treat_all_ocean_as_block: bool = False) -> None:
        """线/面要素的顶点抽样海陆判定：已知陆上设施全部顶点落在确信
        开阔水域 → BLOCK；部分落水 → 警示。"""
        pts = [
            (float(c[0]), float(c[1]))
            for c in vertices
            if isinstance(c, (list, tuple)) and len(c) >= 2 and _is_number(c[0]) and _is_number(c[1])
        ]
        if not pts:
            return
        step = max(1, len(pts) // 16)
        sampled = pts[::step]
        zones = [classify_point(x, y, ocean_confirm_km=self._config.ocean_confirm_km)
                 for x, y in sampled]
        ocean_cnt = sum(1 for z in zones if z == SurfaceZone.OCEAN_CONFIDENT)
        if ocean_cnt == 0:
            return
        evidence = {"ocean_vertices": ocean_cnt, "sampled": len(sampled),
                    "facility_kind": facility}
        if facility in LAND_FACILITY_KINDS and ocean_cnt == len(sampled) \
                and treat_all_ocean_as_block:
            state.block(
                GuardLevel.L3_LANDMASK_PLAUSIBILITY,
                "OCEAN_LINE_FACILITY",
                f"陆上设施线要素的全部抽样顶点都落在确信开阔水域（{path}）",
                path,
                evidence,
            )
        else:
            state.warn(
                GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                "SUSPICIOUS_OCEAN_VERTEX",
                f"要素部分/全部抽样顶点落在确信开阔水域（{path}）—— 请核实",
                path,
                evidence,
            )

    # ------------------------------------------------------------------
    @staticmethod
    def _finalize(verdict: GuardrailVerdict, state: _ScanState,
                  t0: float) -> None:
        verdict.issues = state.issues
        verdict.mutated = state.mutated
        verdict.passed = not state.blocking_issues()
        verdict.duration_ms = (time.perf_counter() - t0) * 1000.0


_lock = threading.Lock()
_default: SpatialGuardrails | None = None


def get_guardrails() -> SpatialGuardrails:
    """进程内惰性单例（数据资产随首次校验构建一次）。"""
    global _default
    if _default is None:
        with _lock:
            if _default is None:
                _default = SpatialGuardrails()
    return _default
