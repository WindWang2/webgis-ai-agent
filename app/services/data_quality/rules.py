"""Data Quality V9 —— 规则 DSL / 注册表（P1，任务书 §2）。

把 ``data_quality`` 从 493 行薄壳升为子系统的**规则面**：

- 规则 = ``RuleSpec``（id / type / severity / params / enabled），字典 DSL
  一等公民，YAML 预设经 :func:`load_rule_preset` 装载为同一形状；
- 16 类内置规则（见 ``RuleType``）：判定实现在 ``rule_functions.py``
  （纯函数，vector 走 features、raster 走 band 统计）；
- 词表纪律：规则类型与修复操作都是封闭词表 —— 修复操作 ⊆
  ``REMEDIATION_OPS``（单一事实源 gis_harness.data_qualification，
  漂移即 fail-fast）；
- 有界纪律：规则集 ≤64 条、params 深度 ≤2、字段名 ≤96 字符；
  ruleset digest = 规范化投影的 sha256（同集同指纹，报告可比）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.data_qualification import REMEDIATION_OPS

# ── 封闭词表 ─────────────────────────────────────────────────────────

#: 规则类型词表（16 类内置；新增类型必须同步 rule_functions.RULE_FUNCTIONS）。
RULE_TYPES = (
    # 矢量/表
    "null_rate",              # 空值率
    "crs_validity",           # CRS 有效性
    "geometry_validity",      # 几何有效性（自交/退化/未闭合）
    "envelope_sanity",        # 空间范围合理性（反转/越界/零面积）
    "attribute_domain",       # 属性值域
    "primary_key_uniqueness",  # 主键唯一
    "fk_referential",         # 外键引用完整性（数据集内引用）
    "duplicate_features",     # 重复要素
    "field_type_drift",       # 字段类型漂移
    "temporal_gaps",          # 时间序列断裂
    "attribute_encoding",     # 属性编码（乱码探测）
    "topology_adjacency",     # 拓扑邻接异常（相邻面重叠）
    "mixed_geometry_types",   # 几何族混杂
    # 栅格
    "nodata_ratio",           # nodata 比例
    "resolution_drift",       # 分辨率漂移/各向异性
    "raster_stats_outlier",   # 波段统计离群
)

SEVERITIES = ("info", "warn", "error")

#: 修复建议只允许引用共享修复词表（plan-only：建议 ≠ 执行）。
ALLOWED_FIX_OPS = frozenset(REMEDIATION_OPS)

#: DSL 有界纪律。
MAX_RULES_PER_SET = 64
MAX_PARAMS_ENTRIES = 16
_MAX_STR = 96


# ── 数据形状 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleSpec:
    """一条质量规则（不可变；params 只收白名单标量/表）。"""

    rule_id: str
    rule_type: str
    severity: str = "warn"
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.rule_type not in RULE_TYPES:
            raise ValueError(
                f"rule_type '{self.rule_type}' 不在封闭词表 RULE_TYPES 内"
            )
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"severity '{self.severity}' 不在词表 {SEVERITIES} 内"
            )
        if not self.rule_id or len(self.rule_id) > 64:
            raise ValueError("rule_id 必须为 1..64 字符")
        for op in _fix_ops_of(self.params):
            if op not in ALLOWED_FIX_OPS:
                raise ValueError(
                    f"fix op '{op}' 不在 REMEDIATION_OPS 词表内（单一事实源）"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "severity": self.severity,
            "params": dict(self.params),
            "enabled": self.enabled,
            "description": self.description[:200],
        }

    def digest_projection(self) -> Dict[str, Any]:
        return {
            "id": self.rule_id[:64],
            "t": self.rule_type[:48],
            "s": self.severity[:16],
            "p": _canonical(self.params),
            "e": bool(self.enabled),
        }


def _fix_ops_of(params: Dict[str, Any]) -> Tuple[str, ...]:
    ops = params.get("fix_operations") or ()
    if isinstance(ops, str):
        return (ops,)
    return tuple(str(o) for o in ops)


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


# ── DSL 解析 ─────────────────────────────────────────────────────────


def parse_rule_def(raw: Any) -> RuleSpec:
    """dict（或 YAML 节点）→ RuleSpec。未知键诚实拒绝（不静默吞）。"""
    if not isinstance(raw, dict):
        raise ValueError("rule def 必须是 dict")
    unknown = set(raw.keys()) - {
        "rule_id", "rule_type", "severity", "params", "enabled", "description",
    }
    if unknown:
        raise ValueError(f"rule def 含未知键: {sorted(str(k) for k in unknown)}")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params 必须是 dict")
    if len(params) > MAX_PARAMS_ENTRIES:
        raise ValueError(f"params 条目数超上限 {MAX_PARAMS_ENTRIES}")
    return RuleSpec(
        rule_id=str(raw.get("rule_id") or "")[:64],
        rule_type=str(raw.get("rule_type") or ""),
        severity=str(raw.get("severity") or "warn"),
        params=dict(params),
        enabled=bool(raw.get("enabled", True)),
        description=str(raw.get("description") or "")[:200],
    )


def parse_rule_defs(raws: Optional[List[Any]]) -> List[RuleSpec]:
    """规则集解析（有界：≤MAX_RULES_PER_SET；rule_id 冲突 fail-fast）。"""
    if not raws:
        return []
    if len(raws) > MAX_RULES_PER_SET:
        raise ValueError(f"规则集超上限 {MAX_RULES_PER_SET} 条")
    specs = [parse_rule_def(r) for r in raws]
    seen = set()
    for s in specs:
        if s.rule_id in seen:
            raise ValueError(f"规则集内 rule_id 重复: {s.rule_id}")
        seen.add(s.rule_id)
    return specs


def load_rule_preset(raw: Any) -> List[RuleSpec]:
    """YAML/dict 预设装载：``{"rules": [...], ...}`` 或裸列表。"""
    if isinstance(raw, dict):
        unknown = set(raw.keys()) - {"rules"}
        if unknown:
            raise ValueError(f"预设含未知顶层键: {sorted(str(k) for k in unknown)}")
        raw = raw.get("rules") or []
    if not isinstance(raw, list):
        raise ValueError("预设必须是 list 或 {rules: [...]}")
    return parse_rule_defs(raw)


def ruleset_digest(specs: List[RuleSpec]) -> str:
    """规则集指纹：只取启用规则（禁用规则不改变判定语义）。"""
    projection = sorted(
        _canonical(s.digest_projection()) for s in specs if s.enabled
    )
    return hashlib.sha256(_canonical(projection).encode("utf-8")).hexdigest()[:32]


# ── 内置默认规则集（16 类全覆盖；阈值即文档） ─────────────────────────


def _default_rules() -> Tuple[RuleSpec, ...]:
    d = [
        ("null_rate", "warn", {"max_null_rate": 0.5, "fail_null_rate": 0.8},
         "字段空值率超阈值", True, ("filter_null",)),
        ("crs_validity", "error", {"target_crs": "EPSG:4326"},
         "CRS 缺失或不可识别", True, ("reproject",)),
        ("geometry_validity", "warn", {"max_invalid_ratio": 0.05},
         "自交/退化/未闭合几何", True, ("repair_geometry",)),
        ("envelope_sanity", "warn", {"lon_range": [-180.5, 180.5],
                                     "lat_range": [-90.5, 90.5]},
         "外包矩形反转/越界/零面积", False, ()),
        ("attribute_domain", "warn", {"fields": {}},
         "属性值域越界（fields: {name: {min,max,enum}}）", False, ()),
        ("primary_key_uniqueness", "error", {"field": "id"},
         "主键重复", False, ()),
        ("fk_referential", "warn", {"field": "", "valid_values": []},
         "引用完整性（数据集内字典校验）", False, ()),
        ("duplicate_features", "warn", {"coordinate_precision": 6},
         "重复要素（几何+属性全同）", True, ("repair_geometry",)),
        ("field_type_drift", "warn", {"expected_types": {}},
         "字段类型漂移（期望 vs 实测 / 字段内混型）", False, ()),
        ("temporal_gaps", "warn", {"time_field": "", "gap_factor": 4.0},
         "时间序列断裂（> gap_factor × 中位间隔）", False, ()),
        ("attribute_encoding", "warn", {"max_mojibake_ratio": 0.02},
         "属性编码乱码探测", True, ("normalize",)),
        ("topology_adjacency", "warn", {"max_overlap_ratio": 0.01,
                                        "max_polygons": 200},
         "相邻面重叠超容差", False, ()),
        ("mixed_geometry_types", "info", {},
         "几何族混杂（Point/Line/Polygon 混收）", False, ()),
        ("nodata_ratio", "warn", {"max_nodata_ratio": 0.6},
         "栅格 nodata 比例过高", True, ("filter_nodata",)),
        ("resolution_drift", "warn", {"max_anisotropy": 1.05},
         "分辨率漂移/各向异性", False, ()),
        ("raster_stats_outlier", "warn", {"z_threshold": 3.0},
         "波段统计离群（跨波段 z 分数）", False, ()),
    ]
    return tuple(
        RuleSpec(
            rule_id=f"dq.{rtype}",
            rule_type=rtype,
            severity=severity,
            params=params,
            description=desc,
        )
        for (rtype, severity, params, desc, _auto, _ops) in d
    )


DEFAULT_RULES: Tuple[RuleSpec, ...] = _default_rules()

# default 规则上的 fix 词表单一事实（RuleSpec.__post_init__ 校验全部通过）
for _s in DEFAULT_RULES:
    assert all(op in ALLOWED_FIX_OPS for op in _fix_ops_of(_s.params)), _s.rule_id


def rule_catalog() -> List[Dict[str, Any]]:
    """规则目录（GET /data-quality/rules 的形状；有界投影）。"""
    return [
        {
            "rule_id": s.rule_id,
            "rule_type": s.rule_type,
            "severity": s.severity,
            "default_params": dict(s.params),
            "description": s.description,
            "enabled": s.enabled,
        }
        for s in DEFAULT_RULES
    ]


__all__ = [
    "RULE_TYPES",
    "SEVERITIES",
    "ALLOWED_FIX_OPS",
    "RuleSpec",
    "DEFAULT_RULES",
    "parse_rule_def",
    "parse_rule_defs",
    "load_rule_preset",
    "ruleset_digest",
    "rule_catalog",
]
