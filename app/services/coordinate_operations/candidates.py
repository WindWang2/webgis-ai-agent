"""G45 WP04/05 — 坐标操作候选发现、服务器签发与环境固定。

候选事实两个来源（pyproj 3.8 / PROJ 9.8 实测语义）：

- ``TransformerGroup(...).transformers`` —— 当前环境**可执行**的操作
  （``.description`` 全名、``.accuracy``（-1=unknown）、``.operations`` 步骤、
  ``.to_json()`` PROJJSON 冻结定义）；
- ``TransformerGroup(...).unavailable_operations`` —— **不可执行**的最佳已知操作
  （CoordinateOperation：``.grids`` 逐格网 available/url、accuracy、area_of_use）。

纪律（任务书 WP04/WP05/C07–C11）：

- 不可执行候选**保留在列表中**并给出原因（缺格网名/官方 URL），绝不静默消失
  制造「最佳可用」错觉；亦不把 unknown accuracy(-1) 显示为 0；
- ``candidate_id`` = 服务器对（PROJJSON sha + 名称 + 精度）的确定性签发 ——
  客户端只能回传 id；任意 pipeline 串/格网路径/URL 一律不进执行定义；
- 锁定时**重算**同参数候选集并按 id 匹配后才冻结定义与环境证据 —— 环境漂移
  （pyproj/PROJ 版本、格网清单、network 开关）→ ``OPERATION_ENV_CHANGED``；
- 候选有界（默认各 16 条，溢出计数如实报告）；排序只呈现、不构成批准。
"""
from __future__ import annotations

import hashlib
import json
import warnings
from typing import Any, Dict, List, Optional, Tuple

from app.services.coordinate_operations.contracts import CoordinateOperationError

#: 单边候选返回上限（available / unavailable 各自独立）。
MAX_CANDIDATES_PER_KIND = 16

_AOI_KEYS = ("west", "south", "east", "north")


def normalize_aoi(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """AOI 规范化：单 bbox、度、west<east（跨反经线/多块范围 typed 拒绝）。

    返回 ``{west, south, east, north}``；``None`` 输入 → ``None``（无 AOI）。
    C13：跨反经线（west≥east）与 ≥360° 宽度本域表达不了 —— 明确拒绝并说明，
    绝不构造错误全球 bbox。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CoordinateOperationError(
            "invalid_spec", "AOI 必须是 {west,south,east,north} 对象")
    try:
        west, south, east, north = (float(raw[k]) for k in _AOI_KEYS)
    except (KeyError, TypeError, ValueError) as e:
        raise CoordinateOperationError(
            "invalid_spec", f"AOI 缺少/非法数值字段: {e}") from e
    if not (-180.0 <= west < east <= 180.0):
        raise CoordinateOperationError(
            "invalid_spec",
            "AOI 经度需满足 -180 ≤ west < east ≤ 180（跨反经线/多块范围本域"
            "暂不支持；请拆分数据或提供单一连续范围）",
            west=west, east=east)
    if not (-90.0 <= south < north <= 90.0):
        raise CoordinateOperationError(
            "invalid_spec", "AOI 纬度需满足 -90 ≤ south < north ≤ 90",
            south=south, north=north)
    return {"west": west, "south": south, "east": east, "north": north}


def environment_fingerprint() -> Dict[str, Any]:
    """当前执行环境证据（计划冻结与执行前复核的同构锚）。

    - pyproj/PROJ 版本（实际安装，非文档标签）；
    - network 开关必须为 False（运行时禁自动联网补网格；若被外部打开 → 环境已变）；
    - 受信格网清单 digest（proj 数据目录 grids/ 下文件名+大小排序哈希；本基线
      环境为空清单 —— 不扫描全机目录，只看 PROJ 自身数据目录）。
    """
    import pyproj
    import pyproj.datadir

    network = bool(pyproj.network.is_network_enabled())
    grid_names: List[str] = []
    for base in pyproj.datadir.get_data_dir().split(":"):
        gdir = None
        try:
            import os
            gdir = __import__("pathlib").Path(base) / "grids"
            if gdir.is_dir():
                for p in sorted(gdir.iterdir()):
                    if p.is_file():
                        grid_names.append(f"{p.name}:{p.stat().st_size}")
        except Exception:  # noqa: BLE001 — 清单失败按空（离线基线即空）
            continue
    grid_names.sort()
    grids_digest = hashlib.sha256(
        "\n".join(grid_names).encode("utf-8")).hexdigest()
    return {
        "pyproj_version": pyproj.__version__,
        "proj_version": pyproj.proj_version_str,
        "network_enabled": network,
        "trusted_grid_inventory": grids_digest,
        "trusted_grid_count": len(grid_names),
    }


def _candidate_id(projjson: str, name: str, accuracy: Optional[float]) -> str:
    basis = json.dumps({
        "projjson_sha256": hashlib.sha256(
            projjson.encode("utf-8")).hexdigest(),
        "name": name,
        "accuracy": accuracy if accuracy is not None and accuracy >= 0 else None,
    }, sort_keys=True, ensure_ascii=False)
    return "cand_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _area_of_use(obj: Any) -> Optional[Dict[str, Any]]:
    a = getattr(obj, "area_of_use", None)
    if a is None:
        return None
    try:
        return {
            "name": str(a.name)[:200],
            "bounds": [a.west, a.south, a.east, a.north],
        }
    except Exception:  # noqa: BLE001
        return None


def _steps_grids(steps: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for op in steps:
        for g in getattr(op, "grids", None) or []:
            out.append({
                "short_name": g.short_name,
                "available": bool(g.available),
                "url": getattr(g, "url", None) or None,
                "license": bool(getattr(g, "open_license", False)),
            })
    # 去重保序
    seen = set()
    dedup = []
    for g in out:
        key = (g["short_name"], g["available"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(g)
    return dedup


def _accuracy_field(acc: Any) -> Tuple[Optional[float], bool]:
    """accuracy 规范化：-1/None → (None, unknown=True)；绝不显示 0 冒充已知。"""
    try:
        a = float(acc)
    except (TypeError, ValueError):
        return None, True
    if a < 0:
        return None, True
    return a, False


def list_operations(
    source_crs_input: Any,
    target_crs_input: Any,
    aoi: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """冻结的源/目标 CRS + 明确 AOI → 有界候选列表 + 环境证据。

    Raises:
        operation_no_candidates: 两侧均无候选（含 available 与 unavailable）。
    """
    from pyproj import CRS
    from pyproj.transformer import TransformerGroup

    from app.services.coordinate_operations.crs_contract import _crs_from_input

    src = _crs_from_input(source_crs_input)
    dst = _crs_from_input(target_crs_input)
    norm_aoi = normalize_aoi(aoi)

    kwargs: Dict[str, Any] = {"always_xy": True}
    if norm_aoi is not None:
        kwargs["area_of_interest"] = (
            norm_aoi["west"], norm_aoi["south"], norm_aoi["east"], norm_aoi["north"])

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tg = TransformerGroup(src, dst, **kwargs)
    except Exception as e:
        raise CoordinateOperationError(
            "operation_no_candidates",
            f"当前环境无法枚举 {source_crs_input!r} → {target_crs_input!r} 的坐标"
            f"操作: {e}") from e

    available: List[Dict[str, Any]] = []
    for t in tg.transformers:
        steps = list(t.operations or [])
        projjson = t.to_json()
        acc, unknown = _accuracy_field(t.accuracy)
        ballpark = any(
            bool(getattr(op, "has_ballpark_transformation", False)) for op in steps)
        available.append({
            "candidate_id": _candidate_id(projjson, t.description, acc),
            "availability": "available",
            "name": t.description,
            "accuracy": acc,
            "accuracy_unknown": unknown,
            "ballpark": ballpark,
            "area_of_use": _area_of_use(t),
            "grids": _steps_grids(steps),
            "definition_sha256": hashlib.sha256(
                projjson.encode("utf-8")).hexdigest(),
            "selectable": True,
        })
    unavailable: List[Dict[str, Any]] = []
    for op in tg.unavailable_operations:
        projjson = op.to_json()
        acc, unknown = _accuracy_field(op.accuracy)
        grids = [{
            "short_name": g.short_name,
            "available": bool(g.available),
            "url": getattr(g, "url", None) or None,
            "license": bool(getattr(g, "open_license", False)),
        } for g in (op.grids or [])]
        missing = [g["short_name"] for g in grids if not g["available"]]
        unavailable.append({
            "candidate_id": _candidate_id(projjson, op.name, acc),
            "availability": "unavailable",
            "name": op.name,
            "accuracy": acc,
            "accuracy_unknown": unknown,
            "ballpark": bool(getattr(op, "has_ballpark_transformation", False)),
            "area_of_use": _area_of_use(op),
            "grids": grids,
            "definition_sha256": hashlib.sha256(
                projjson.encode("utf-8")).hexdigest(),
            "missing_reason": (
                f"依赖格网在当前离线环境不可用: {', '.join(missing)}"
                if missing else "当前环境不可执行"),
            "selectable": False,
        })

    if not available and not unavailable:
        raise CoordinateOperationError(
            "operation_no_candidates",
            f"{source_crs_input!r} → {target_crs_input!r} 在当前离线环境无任何"
            "已知坐标操作（available 与 unavailable 均为空）")

    # 有界返回（保持 pyproj 精度优先序；溢出计数如实）
    avail_shown = available[:MAX_CANDIDATES_PER_KIND]
    unavail_shown = unavailable[:MAX_CANDIDATES_PER_KIND]
    env = environment_fingerprint()
    return {
        "source_crs": str(source_crs_input),
        "target_crs": str(target_crs_input),
        "aoi": norm_aoi,
        "axis_convention": "always_xy（载体 x=east/y=north；官方轴序仅展示）",
        "candidates": avail_shown + unavail_shown,
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "available_truncated": len(available) - len(avail_shown),
        "unavailable_truncated": len(unavailable) - len(unavail_shown),
        "environment": env,
    }


def resolve_candidate_for_lock(
    source_crs_input: Any,
    target_crs_input: Any,
    aoi: Optional[Dict[str, Any]],
    candidate_id: str,
) -> Dict[str, Any]:
    """锁定期重算候选集 → 按 id 匹配 → 冻结定义 + 环境证据。

    Returns: {candidate(去列表化字段), operation_projjson, environment,
              definition_sha256}
    Raises:
        operation_not_in_plan: id 不在当前环境候选集中（含伪造/漂移）。
        operation_unavailable: id 命中但当前不可执行（缺格网）。
        operation_env_changed: 环境证据与 listing 时不一致（版本/格网/network）。
    """
    listing = list_operations(source_crs_input, target_crs_input, aoi)
    for cand in listing["candidates"]:
        if cand["candidate_id"] == candidate_id:
            if cand["availability"] != "available":
                raise CoordinateOperationError(
                    "operation_unavailable",
                    f"候选 {candidate_id} 当前不可执行: "
                    f"{cand.get('missing_reason')}")
                # 不可执行候选不给定义 —— 客户端拿不到可绕过的 pipeline。
            from pyproj import CRS
            from pyproj.transformer import TransformerGroup

            from app.services.coordinate_operations.crs_contract import _crs_from_input

            src = _crs_from_input(source_crs_input)
            dst = _crs_from_input(target_crs_input)
            norm_aoi = normalize_aoi(aoi)
            kwargs: Dict[str, Any] = {"always_xy": True}
            if norm_aoi is not None:
                kwargs["area_of_interest"] = (
                    norm_aoi["west"], norm_aoi["south"],
                    norm_aoi["east"], norm_aoi["north"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tg = TransformerGroup(src, dst, **kwargs)
            for t in tg.transformers:
                projjson = t.to_json()
                acc, _ = _accuracy_field(t.accuracy)
                if _candidate_id(projjson, t.description, acc) == candidate_id:
                    return {
                        "candidate": cand,
                        "operation_projjson": projjson,
                        "definition_sha256": cand["definition_sha256"],
                        "environment": listing["environment"],
                    }
            # id 在列表、重算集里却找不到 —— 定义漂移
            raise CoordinateOperationError(
                "operation_env_changed",
                "候选定义在锁定重算中发生变化（PROJ 数据库漂移）")
    raise CoordinateOperationError(
        "operation_not_in_plan",
        f"candidate_id {candidate_id!r} 不属于当前环境签发的候选集"
        "（客户端不可注入任意 pipeline/格网参数）")
