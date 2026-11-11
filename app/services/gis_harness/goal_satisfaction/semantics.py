"""科学语义与完整性闸（G28 P2）—— 回执事实 → 有界语义 cap 码。

单一职责（与 evaluator 同 fail-closed 纪律）：消费既有回执/契约上已存在的
诚实字段（row_count/skipped/unit_status/measurement_kind/crs_source/
completeness/observation 模式……），把「不能冒充全集/正确」的情形投影成
机器可读 cap 码。**本模块不重算任何算法、不遍历数据、不从标题或 LLM 叙述
推断事实**；证据缺席 → 无码（unknown 就是 unknown，绝不虚构）。

语义（全部只降级、只披露，永不升级）：
- ``population_partial``：计算/导入/交付回执明示 skipped/rejected/missing
  ——「前 N 行/部分范围」不得背书「全部」需求（A07）；
- ``density_without_denominator`` / ``rate_without_timebase``：率/密度类
  派生列单位语义未声明 —— 绝对 count 不得背书密度需求（A08/A09）；
- ``crs_unverified``：CRS 来源 unknown/assumed —— 不虚构正确性（A09）；
- ``artifact_checksum_mismatch``：同名工件不同 checksum —— 不得混用（A18）；
- ``observation_snapshot_not_live``：快照观察不冒充 live（A14）；
- ``scope_subset``：交付 scope 明示 requested ⊃ delivered（A17 配套）。

旧回执缺字段 → 对应事实按 unknown 处理（无码），兼容面测试锁定
（A25：既不假 pass 也不假 fail）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── cap 码（机器可读；语料与测试锁定）────────────────────────────────────
CAP_POPULATION_PARTIAL = "population_partial"
CAP_DENSITY_NO_DENOM = "density_without_denominator"
CAP_RATE_NO_TIMEBASE = "rate_without_timebase"
CAP_UNIT_UNDECLARED = "unit_undeclared"
CAP_CRS_UNVERIFIED = "crs_unverified"
CAP_ARTIFACT_MISMATCH = "artifact_checksum_mismatch"
CAP_SNAPSHOT_NOT_LIVE = "observation_snapshot_not_live"
CAP_SCOPE_SUBSET = "scope_subset"
CAP_RECEIPT_FAILED = "receipt_failed"
CAP_RECEIPT_CANCELLED = "receipt_cancelled"

#: 需要「全集」语义的 measurement_kind —— 这些口径没有诚实分母就不能 PASS。
_SEMANTIC_KINDS_REQUIRING_BASIS = frozenset({"rate", "density", "percent"})

_MAX_CAPS = 6


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> Optional[int]:
    """诚实整数读取：非 int（含 None/缺失/字符串）→ None = unknown。"""
    return value if isinstance(value, int) and not isinstance(value, bool) \
        else None


# ── 单回执族 cap 计算（纯函数；字段缺席 = 无码 = unknown）────────────────

def caps_from_compute(row: Dict[str, Any]) -> List[str]:
    """G19 ComputeReceipt 投影行 → cap 码（诚实分母词表）。"""
    caps: List[str] = []
    row_count = _int(row.get("row_count"))
    computed = _int(row.get("computed_rows"))
    skipped = _int(row.get("skipped_rows"))
    errors = _int(row.get("error_count"))
    # 未知总数或未知计算数 → 不判 partial（unknown 不造码，A25）。
    if row_count is not None and computed is not None and row_count > 0:
        incomplete = (
            computed < row_count
            or (isinstance(skipped, int) and skipped > 0)
            or (isinstance(errors, int) and errors > 0)
        )
        if incomplete:
            caps.append(CAP_POPULATION_PARTIAL)
    # 率/密度/百分比列必须声明单位语义；纯 count/标识列不要求。
    for col in (row.get("columns") or [])[:8]:
        if not isinstance(col, dict):
            continue
        kind = str(col.get("measurement_kind") or "").strip().lower()
        unit_status = str(col.get("unit_status") or "").strip().lower()
        if kind in _SEMANTIC_KINDS_REQUIRING_BASIS and unit_status == "undeclared":
            caps.append(
                CAP_DENSITY_NO_DENOM if kind == "density"
                else CAP_RATE_NO_TIMEBASE
            )
    return caps


def caps_from_import(row: Dict[str, Any]) -> List[str]:
    """G17 CommitReceipt 投影行 → cap 码。"""
    caps: List[str] = []
    status = str(row.get("status") or "").strip().lower()
    if status == "cancelled":
        caps.append(CAP_RECEIPT_CANCELLED)
    elif status == "failed":
        caps.append(CAP_RECEIPT_FAILED)
    rejected = _int(row.get("rejected_rows"))
    if isinstance(rejected, int) and rejected > 0:
        caps.append(CAP_POPULATION_PARTIAL)
    crs_source = str(row.get("crs_source") or "").strip().lower()
    if crs_source in ("unknown", "assumed"):
        caps.append(CAP_CRS_UNVERIFIED)
    return caps


def caps_from_delivery(row: Dict[str, Any]) -> List[str]:
    """G18 DeliveryReceipt 投影行 → cap 码（范围/身份/取消）。"""
    caps: List[str] = []
    status = str(row.get("status") or "").strip().lower()
    if status == "cancelled":
        caps.append(CAP_RECEIPT_CANCELLED)
    elif status and status != "completed":
        caps.append(CAP_RECEIPT_FAILED)
    missing_ids = row.get("scope_missing_ids")
    if isinstance(missing_ids, list) and missing_ids:
        caps.append(CAP_SCOPE_SUBSET)
    subset = row.get("scope_subset_of_selection")
    if subset is True:
        caps.append(CAP_SCOPE_SUBSET)
    # 身份可验证性：completed 但无 content_sha256 → 不判 mismatch（诚实
    # unknown，由 evaluator missing 面「artifact_unverified」披露）。
    return caps


def caps_from_edit(row: Dict[str, Any]) -> List[str]:
    """G14 EditReceipt 投影行 → cap 码（盖章欠账披露；提交本身即事实）。"""
    stamp = str(row.get("stamp_status") or "").strip().lower()
    if stamp in ("deferred", "pending"):
        # MapSpec 数据戳欠账 —— 编辑已提交但期望修订未落 spec；
        # 不算失败（journal 是真源），披露为 partial 语义面。
        return [CAP_POPULATION_PARTIAL]
    return []


def artifact_identity_caps(rows: List[Dict[str, Any]]) -> List[str]:
    """同名工件身份对账（A18）：filename 相同 ∧ content_sha256 均在场且
    互相不同 → 全部标记 mismatch（fail-closed 双侧；不得混用）。"""
    if conflicted_artifact_names(rows):
        return [CAP_ARTIFACT_MISMATCH]
    return []


def conflicted_artifact_names(rows: List[Dict[str, Any]]) -> set:
    """同名不同 checksum 的工件名集合（供逐需求绑定；空 = 无冲突）。"""
    by_name: Dict[str, set] = {}
    for row in rows[:8]:
        row = row if isinstance(row, dict) else {}
        name = str(row.get("filename") or "").strip()
        sha = str(row.get("content_sha256") or "").strip()
        if name and sha:
            by_name.setdefault(name, set()).add(sha)
    return {n for n, shas in by_name.items() if len(shas) > 1}


def caps_from_refresh(row: Dict[str, Any]) -> List[str]:
    """G10 AdoptionReceipt 投影行 → cap 码（观察未证实不冒充已观察）。"""
    caps: List[str] = []
    map_obs = str(row.get("map_observation") or "").strip().lower()
    if map_obs == "unverified":
        caps.append(CAP_SNAPSHOT_NOT_LIVE)
    status = str(row.get("status") or "").strip().lower()
    if status == "blocked":
        caps.append(CAP_RECEIPT_FAILED)
    return caps


# ── 汇总入口 ─────────────────────────────────────────────────────────────

_CAP_BY_FAMILY = {
    "compute": caps_from_compute,
    "import": caps_from_import,
    "delivery": caps_from_delivery,
    "edit": caps_from_edit,
    "refresh": caps_from_refresh,
}


def caps_for_receipt_row(family: str, row: Dict[str, Any]) -> List[str]:
    """单族单行 cap 投影（未知族 → 无码，不猜）。"""
    fn = _CAP_BY_FAMILY.get(str(family or "").strip().lower())
    if fn is None:
        return []
    try:
        return fn(_dict(row))[:_MAX_CAPS]
    except Exception:  # noqa: BLE001 — 闸是增值投影，坏行不阻断评估
        return []


def bundle_artifact_caps(bundle: Optional[Dict[str, Any]]) -> List[str]:
    """bundle 级工件身份对账（delivery 族跨行）。"""
    if not isinstance(bundle, dict):
        return []
    try:
        return artifact_identity_caps(
            [r for r in (bundle.get("delivery") or [])
             if isinstance(r, dict)])
    except Exception:  # noqa: BLE001
        return []


__all__ = [
    "CAP_POPULATION_PARTIAL",
    "CAP_DENSITY_NO_DENOM",
    "CAP_RATE_NO_TIMEBASE",
    "CAP_UNIT_UNDECLARED",
    "CAP_CRS_UNVERIFIED",
    "CAP_ARTIFACT_MISMATCH",
    "CAP_SNAPSHOT_NOT_LIVE",
    "CAP_SCOPE_SUBSET",
    "CAP_RECEIPT_FAILED",
    "CAP_RECEIPT_CANCELLED",
    "caps_for_receipt_row",
    "bundle_artifact_caps",
    "conflicted_artifact_names",
    "caps_from_compute",
    "caps_from_import",
    "caps_from_delivery",
    "caps_from_edit",
    "caps_from_refresh",
    "artifact_identity_caps",
]
