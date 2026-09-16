"""Temporal Raster Cube descriptor（refs-only）—— 时序立方体的 Agent 契约面。

定位（本方向 ownership：TemporalRasterCube descriptor/ref）：

- 既有 ``geo_analysis.temporal_cube.TemporalCube`` 是**内存态数据容器**
  （numpy 栈本体）；``lakehouse.rs_cube`` 是**存储级对齐写入器**。本模块
  补的是二者之间缺失的一层：**引用级描述符** —— asset/time/band/极化/
  网格/CRS/质量/缺口/nodata/source_version 的机器可读清单，供 planner /
  LLM / SkillPolicy 在**不加载任何栅格 payload** 的前提下做兼容性裁决
  与计划（对齐 D1 数据供给契约的 additive 风格，
  ``data_fabric/contracts.py``）；
- **refs-only 红线**：asset 只有 ``ref``（``ref:raster/<id>``、fabric
  ref、DataObject id 等 opaque 引用）——ref 存在 ⟺ payload 可由既有
  raster_store/fabric 通道取回，本模块绝不内嵌数组；
- **类型化缺口**：``GAP_CODES`` 封闭词表（缺测/云/云影/无效像元/扰动
  layover/SAR 阴影/离网/低质/未配准），缺口是**声明语义**（上游 QC 或
  计划产物），不是自由文本；本模块不做云判识/layover 计算——那是
  ``rs_v3.cloud_qc_basic`` / ``sar_v3.layover_shadow_mask`` 的职责，
  这里只消费其结论并计数披露；
- **时间语义**：升序、唯一（观测语义键）、不静默重排（与
  ``temporal_cube.build_cube`` 同红线）；date-only = UTC 午夜，
  naive 时刻 = 按 UTC 解释并披露（确定性，绝不用本地时区）；
- 有界：资产数硬顶（先拒绝不 OOM），context 摘要有界且只含标量/短列表。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.lib.geo_analysis.spectral import BAND_ROLES
from app.lib.gis.scientific_errors import DegenerateData, UnsupportedBandSemantics

#: 契约版本（additive 演进：只加可选字段；改语义需 ADR）。
CUBE_DESCRIPTOR_CONTRACT_VERSION = "1.0"

#: 资产条目硬顶（与 lakehouse RS_MAX_SOURCES 同量级；先拒绝不 OOM）。
CUBE_DESCRIPTOR_MAX_ASSETS = 512

#: 源角色词表 —— 镜像 ``lakehouse.rs_cube.RS_ROLES``（lib 层不反向依赖
#: services，靠测试钉住同词表）。
CUBE_SOURCE_ROLES = ("optical", "sar", "cloud_mask", "quality_mask")

#: SAR 极化小写词表（与 sar_temporal.SARAcquisitionMeta 同口径）。
_SAR_POLARIZATIONS_LOWER = frozenset({"vv", "vh", "hh", "hv"})

#: 类型化缺口封闭词表：缺口是声明的语义（上游 QC/计划），不是文本。
GAP_CODES = frozenset({
    "missing_acquisition",   # 计划槽位在源目录中无获取
    "cloud",                 # 光学获取存在但云污染（cloud_qc 结论）
    "cloud_shadow",          # 云影污染
    "nodata",                # 获取存在但全 nodata 足迹
    "layover",               # SAR layover（layover_shadow_mask 结论）
    "sar_shadow",            # SAR 遮挡阴影
    "off_grid",              # 足迹与 cube 网格无有效交叠
    "below_quality",         # 质量掩膜可用度低于阈值（通用）
    "unregistered",          # 配准残差超容差（未通过网格恒等校验）
})

#: 观测角色（质量/掩膜角色不算观测——coverage 统计口径）。
_OBSERVATION_ROLES = frozenset({"optical", "sar"})

_CUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

_MASK_ROLES = frozenset({"cloud_mask", "quality_mask"})


def parse_time_iso(time_iso: str) -> float:
    """ISO-8601 日期/时刻 → epoch 秒（确定性时区规则）。

    - date-only（``YYYY-MM-DD``）= UTC 午夜；
    - naive 时刻 = 按 UTC 解释（绝不使用本地时区——跨机器确定性）；
    - aware 时刻 = 归一到 UTC。
    """
    raw = str(time_iso or "").strip()
    if not raw:
        raise DegenerateData("time_iso 不能为空")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise DegenerateData(
            f"time_iso {raw!r} 不是可解析的 ISO-8601 日期/时刻") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def naive_time_disclosures(time_iso_list) -> Tuple[str, ...]:
    """对 naive（无时区）时刻给出诚实披露（UTC 解释规则）。"""
    out = []
    for raw in time_iso_list or []:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            continue
        if dt.tzinfo is None:
            out.append(
                f"time_iso {s!r} 无时区信息——按 UTC 解释（确定性规则，"
                "非本地时区）")
    return tuple(out)


class CubeGrid(BaseModel):
    """cube 网格恒等（width/height/crs/transform —— 与 lakehouse
    ``_geometry_identity`` 同四键；transform 为 GDAL 6 元组或 rasterio
    9 元组，全部提供时前 6 键须与 GDAL 口径一致）。"""

    model_config = ConfigDict(extra="forbid")

    crs: str
    width: int
    height: int
    transform: Optional[Tuple[float, ...]] = None

    @field_validator("crs")
    @classmethod
    def _crs_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("grid.crs 不能为空（诚实默认：无 CRS 不对齐）")
        return v

    @field_validator("width", "height")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"grid 宽高必须为正，got {v}")
        return v

    @field_validator("transform")
    @classmethod
    def _transform_shape(cls, v):
        if v is None:
            return None
        if len(v) not in (6, 9):
            raise ValueError(
                f"transform 须为 GDAL 6 元组或 rasterio 9 元组，got {len(v)}")
        return tuple(float(x) for x in v)

    def identity(self) -> Dict[str, Any]:
        return {
            "crs": self.crs, "width": self.width, "height": self.height,
            "transform": list(self.transform) if self.transform else None,
        }


class CubeAsset(BaseModel):
    """一个 cube 资产条目：ref + 时间 + 角色 + 语义 + 缺口声明。

    **绝不含 payload**——``ref`` 是 opaque 引用，payload 取回走既有
    raster_store / fabric / DataObject 通道。
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    time_iso: str
    role: str
    band: Optional[str] = None          # optical：语义角色名（spectral.BAND_ROLES）
    polarization: Optional[str] = None  # sar：vv/vh/hh/hv（小写归一）
    gap_code: Optional[str] = None      # GAP_CODES；None = 有效观测
    quality_fraction: Optional[float] = None  # 0..1 声明可用度
    nodata: Optional[float] = None
    source_version: str = ""
    orbit_direction: Optional[str] = None     # sar：ascending/descending
    incidence_angle_deg: Optional[float] = None

    @field_validator("ref")
    @classmethod
    def _ref_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("asset.ref 不能为空（refs-only 契约）")
        return v

    @field_validator("time_iso")
    @classmethod
    def _time_parseable(cls, v: str) -> str:
        parse_time_iso(v)   # 可解析性前置校验（确定性时区规则）
        return v

    @field_validator("role")
    @classmethod
    def _role_vocab(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in CUBE_SOURCE_ROLES:
            raise ValueError(
                f"role {v!r} 不在词表 {CUBE_SOURCE_ROLES}")
        return v

    @field_validator("polarization")
    @classmethod
    def _pol_vocab(cls, v):
        if v is None:
            return None
        v = str(v).strip().lower()
        if v not in _SAR_POLARIZATIONS_LOWER:
            raise ValueError(
                f"polarization {v!r} 必须是 {sorted(_SAR_POLARIZATIONS_LOWER)} 之一")
        return v

    @field_validator("orbit_direction")
    @classmethod
    def _orbit_vocab(cls, v):
        if v is None:
            return None
        v = str(v).strip().lower()
        if v not in ("ascending", "descending"):
            raise ValueError(
                f"orbit_direction {v!r} 必须是 ascending/descending（未知则留空）")
        return v

    @field_validator("gap_code")
    @classmethod
    def _gap_vocab(cls, v):
        if v is None:
            return None
        v = str(v).strip().lower()
        if v not in GAP_CODES:
            raise ValueError(
                f"gap_code {v!r} 不在封闭词表 {sorted(GAP_CODES)}；"
                "缺口必须是声明的类型化语义，不是自由文本")
        return v

    @field_validator("quality_fraction")
    @classmethod
    def _quality_range(cls, v):
        if v is None:
            return None
        v = float(v)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"quality_fraction 须在 [0,1]，got {v}")
        return v

    @model_validator(mode="after")
    def _role_semantics(self) -> "CubeAsset":
        if self.role in _MASK_ROLES:
            if self.band or self.polarization:
                raise ValueError(
                    f"掩膜角色 {self.role} 不携带 band/polarization")
            return self
        if self.role == "optical":
            if not self.band:
                raise ValueError(
                    "optical 资产必须携带语义 band 角色名（如 red/nir）——"
                    "本层绝不按波段位置猜测")
            if self.band not in BAND_ROLES:
                raise UnsupportedBandSemantics(
                    f"band 角色 {self.band!r} 不在类型化词表；"
                    f"可用: {sorted(BAND_ROLES)}",
                    correction_hint="使用 spectral.BAND_ROLES 的语义角色名",
                )
        if self.role == "sar":
            if not self.polarization:
                raise ValueError(
                    "sar 资产必须声明 polarization（vv/vh/hh/hv）")
        return self


class TemporalRasterCubeDescriptor(BaseModel):
    """时序栅格立方体描述符（refs-only；Agent / planner 消费面）。"""

    model_config = ConfigDict(extra="allow")   # additive 契约（D1 风格）

    contract_version: str = CUBE_DESCRIPTOR_CONTRACT_VERSION
    cube_id: str
    label: str = ""
    grid: CubeGrid
    assets: Tuple[CubeAsset, ...]
    nodata: Optional[float] = None
    source_version: str = ""
    lineage: Tuple[str, ...] = Field(default=())
    disclosures: Tuple[str, ...] = Field(default=())

    @field_validator("cube_id")
    @classmethod
    def _cube_id_shape(cls, v: str) -> str:
        v = (v or "").strip()
        if not _CUBE_ID_RE.fullmatch(v):
            raise ValueError(
                "cube_id 须匹配 [A-Za-z0-9_-]{1,64}（ref 安全字符集）")
        return v

    @field_validator("assets")
    @classmethod
    def _assets_bounded(cls, v):
        if not v:
            raise ValueError("时序立方体不允许空资产表（诚实空态=拒绝构建）")
        if len(v) > CUBE_DESCRIPTOR_MAX_ASSETS:
            raise ValueError(
                f"资产条目 {len(v)} 超过上限 {CUBE_DESCRIPTOR_MAX_ASSETS}；"
                "按时间窗切片后再建描述符")
        return tuple(v)

    @field_validator("lineage", "disclosures")
    @classmethod
    def _bounded_str_tuples(cls, v):
        return tuple(str(x) for x in (v or ()))

    @model_validator(mode="after")
    def _ref_and_slot_discipline(self) -> "TemporalRasterCubeDescriptor":
        # 时间轴升序由 build_cube_descriptor 规范化（元数据无行序语义）；
        # 本校验只钉住 ref 唯一性与观测槽位唯一性（诚实去重边界）。
        seen_sem: set = set()
        seen_refs: set = set()
        for a in self.assets:
            if a.ref in seen_refs:
                raise ValueError(
                    f"asset.ref 重复: {a.ref!r}——同一 payload 不应重复入表")
            seen_refs.add(a.ref)
            if a.role in _OBSERVATION_ROLES:
                sem = (a.role, a.time_iso, a.band or a.polarization)
                if sem in seen_sem:
                    raise ValueError(
                        f"重复观测资产（role,time,band/polarization 相同）: {sem}；"
                        "同槽位重复观测必须先去重或声明 gap_code")
                seen_sem.add(sem)
        return self

    # ── 派生量（O(n) 语义视图；全部有界）────────────────────────────

    @property
    def times_sec(self) -> Tuple[float, ...]:
        """观测资产的 epoch 秒时间轴（升序；含掩膜共享时刻）。"""
        return tuple(sorted({
            parse_time_iso(a.time_iso) for a in self.assets
        }))

    @property
    def n_observation_assets(self) -> int:
        return sum(1 for a in self.assets if a.role in _OBSERVATION_ROLES)

    def modalities(self) -> set:
        return {a.role for a in self.assets}

    def coverage_summary(self) -> Dict[str, Any]:
        """有界覆盖摘要：角色计数、缺口计数（按 code）、时间跨度、缺口率。"""
        by_role: Dict[str, int] = {}
        gap_counts: Dict[str, int] = {}
        n_valid = 0
        for a in self.assets:
            if a.role in _OBSERVATION_ROLES:
                by_role[a.role] = by_role.get(a.role, 0) + 1
                if a.gap_code is None:
                    n_valid += 1
                else:
                    gap_counts[a.gap_code] = gap_counts.get(a.gap_code, 0) + 1
        n_obs = sum(by_role.values())
        ts = self.times_sec
        return {
            "n_assets": len(self.assets),
            "n_assets_by_role": {k: by_role[k] for k in sorted(by_role)},
            "n_valid_observation_assets": n_valid,
            "gap_counts": {k: gap_counts[k] for k in sorted(gap_counts)},
            "gap_ratio": (round(1.0 - n_valid / n_obs, 6)) if n_obs else 1.0,
            "time_start": _iso_min(ts) if ts else None,
            "time_end": _iso_max(ts) if ts else None,
            "n_time_steps": len(ts),
            "grid": self.grid.identity(),
        }

    def to_context_summary(self, *, max_assets: int = 16) -> Dict[str, Any]:
        """LLM/planner 上下文摘要：有界、纯标量/短列表，绝无数组 payload。"""
        s = self.coverage_summary()
        sample = [
            {
                "ref": a.ref, "time": a.time_iso, "role": a.role,
                "band": a.band, "polarization": a.polarization,
                "gap_code": a.gap_code,
            }
            for a in self.assets[:max(0, int(max_assets))]
        ]
        return {
            "cube_id": self.cube_id,
            "label": self.label,
            "contract_version": self.contract_version,
            "source_version": self.source_version,
            "n_assets": s["n_assets"],
            "n_assets_by_role": s["n_assets_by_role"],
            "gap_counts": s["gap_counts"],
            "gap_ratio": s["gap_ratio"],
            "time_start": s["time_start"],
            "time_end": s["time_end"],
            "n_time_steps": s["n_time_steps"],
            "grid": s["grid"],
            "nodata": self.nodata,
            "lineage": list(self.lineage),
            "disclosures": list(self.disclosures)[:16],
            "assets_sample": sample,
            "assets_truncated": len(self.assets) > len(sample),
        }


def _iso_of(epoch_sec: float) -> str:
    return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _iso_min(ts: Tuple[float, ...]) -> str:
    return _iso_of(min(ts))


def _iso_max(ts: Tuple[float, ...]) -> str:
    return _iso_of(max(ts))


def build_cube_descriptor(
    *,
    cube_id: str,
    grid,
    assets,
    nodata: Optional[float] = None,
    label: str = "",
    source_version: str = "",
    lineage=(),
    disclosures=(),
) -> TemporalRasterCubeDescriptor:
    """构建描述符（资产表规范化排序 + 确定性时区披露）。

    资产是**元数据**（无行序数据语义）：乱序输入按 ``(time, role, ref)``
    确定性规范化并披露——数据栈层的"不静默重排"红线在
    ``temporal_cube.build_cube``，不在此层。naive 时刻按 UTC 解释并披露。
    """
    entries = [
        a if isinstance(a, CubeAsset) else CubeAsset(**a)
        for a in (assets or [])
    ]
    extra = tuple(disclosures or ())
    extra += naive_time_disclosures([a.time_iso for a in entries])
    decorated = sorted(
        entries, key=lambda a: (parse_time_iso(a.time_iso), a.role, a.ref))
    if len(entries) > 1 and any(
            a is not b for a, b in zip(entries, decorated)):
        extra = extra + (
            "资产表已按 (time, role, ref) 规范化排序——描述符是元数据，"
            "无行序数据语义；数据栈层的重排红线在 temporal_cube.build_cube",
        )
    return TemporalRasterCubeDescriptor(
        cube_id=cube_id, grid=grid, assets=tuple(decorated),
        nodata=nodata, label=label, source_version=source_version,
        lineage=tuple(lineage or ()), disclosures=extra,
    )
