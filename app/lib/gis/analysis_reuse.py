"""Analysis Reuse —— 基于 ArtifactRegistry 的确定性分析结果复用（V2 P10）。

问题：``cached_tool``（Redis 工具缓存）在参数含 ``ref:`` 时**必须**拒绝
缓存 —— ref 是会话内可变指针，同一 id 不同时刻内容可能不同。于是所有
「分析 ref 数据」的工具调用每次都全量重算：同一个 150k 点集连跑两次
H3 聚合，第二次仍付完整解析 + 投影 + 分箱。

本模块把复用键提升到 **artifact 层**（键含 ref id + 全部参数），并靠
两道既有事实补上 cached_tool 缺的那块正确性：

1. **产物记录在 ArtifactRegistry**（per-session，≤128 条，ADR-0082）：
   失败调用不注册 → 失败结果天然不可复用；superseded/expired/stale
   状态 → 不可复用。
2. **RefDescriptor 形状指纹**：生产时记录输入 ref 的
   {feature_count, geometry_types}；复用时对当前 descriptor 复核。
   in-place overwrite（会话协议允许但当前无生产方对分析 ref 使用）
   换了内容 → 形状不符 → miss → 重算。content_hash 被既有决策明确
   保留为 None（store 热路径成本），形状指纹是记录在案的有界启发式，
   不是内容寻址。

边界（刻意收窄）：
- **纯函数 + 只读 IO**：本模块不执行任何工具、不写任何状态（登记由
  dispatch seam 顺路完成）；不是第二缓存框架，不与 cached_tool 竞争
  （cached_tool 管无 ref 小参数；本模块管 ref 参数的跨轮次复用）；
- 复用是**纯加速**：任何一步不可判定（解析失败/尺寸超限/descriptor
  缺失）都 miss 并照常执行，绝不因复用逻辑阻塞或错发结果；
- 空结果（feature_count=0）同样可复用：同输入 → 同空，语义合法（§34
  empty_result 不是错误）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ANALYSIS_KEY_VERSION = "v1"
# 复用时限（秒）：工具实现升级（算法版本变化）没有 per-tool 版本号，
# 时限是兜底失效语义（与 cached_tool TTL 同哲学，取更保守的 24h）。
ANALYSIS_REUSE_MAX_AGE_S = 24 * 3600
# 单条记录的输入形状指纹上限（ref 数；分析输入通常 1-3 个）。
_MAX_INPUT_SHAPES = 8
# 参与内容指纹的栅格路径上限（§有界：指纹本身是有界降采样读）。
_MAX_RASTER_FPS = 4
_RASTER_SUFFIXES = (".tif", ".tiff")


def compute_analysis_key(tool_name: str, args: Any) -> Optional[str]:
    """(tool, args) → 确定性 analysis key；不可键控 → None。

    - args 必须可 JSON 序列化（LLM 工具调用参数本就如此）；
    - canonical = sort_keys + default=str（与 registry dedup 的
      normalize_tool_args 同一口径，但不拒 ref —— ref id 正是键的
      组成部分）；
    - 尺寸闸 256KB（复用 json_size 的估算器）：超大 inline 参数不键控
      （保持 dispatch 快路径，与 make_cache_key 同一决策）。
    """
    from app.lib.json_size import ESTIMATE_SIZE_LIMIT, estimate_json_bytes

    if not tool_name:
        return None
    try:
        estimate_json_bytes(args)
    except Exception:  # noqa: BLE001 — 不可估算的参数直接不键控
        return None
    try:
        # 严格 JSON：LLM 工具参数本就是 JSON 解码产物。default=str 会把
        # 不可序列化对象变成含内存地址的串（非确定性键）—— 严格拒绝。
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    if len(canonical) > ESTIMATE_SIZE_LIMIT:
        return None
    digest = hashlib.sha256(f"{tool_name}::{canonical}".encode()).hexdigest()
    return f"analysis:{ANALYSIS_KEY_VERSION}:{digest}"


def _ref_shape_fingerprint(descriptor: Optional[Dict[str, Any]]) -> Optional[dict]:
    """descriptor → 有界形状指纹（复用复核用；未知 → None → 不复核该 ref）。"""
    if not isinstance(descriptor, dict):
        return None
    fc = descriptor.get("feature_count")
    if not isinstance(fc, int) or isinstance(fc, bool):
        return None
    geom_types = descriptor.get("geometry_types")
    return {
        "feature_count": fc,
        "geometry_types": sorted(str(t) for t in (geom_types or []))[:8],
    }


def _collect_input_shapes(args: Any, resolved_descriptors: Dict[str, Optional[dict]]) -> Dict[str, dict]:
    """args 中的 ref: 游标 → 当前 descriptor 形状指纹（生产时快照）。"""
    shapes: Dict[str, dict] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("ref:") and node not in shapes:
                fp = _ref_shape_fingerprint(resolved_descriptors.get(node))
                if fp is not None:
                    shapes[node] = fp
            return
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(args)
    return dict(list(shapes.items())[:_MAX_INPUT_SHAPES])


def _raster_path_fingerprint(path: str) -> Optional[str]:
    """栅格文件 → 有界内容指纹（grid 身份 + ≤1024 边降采样样本）。

    V2 P10 的复用键只含 raster_path 字符串：同路径下内容被重写（in-place
    overwrite）时旧产物会被错误命中。栅格指纹覆盖 grid+像元内容
    （Raster & RS Runtime V3，ADR-0089 §内容身份）——只对存在的
    .tif/.tiff 计算，成本与一次 inspect 同级，绝不整幅读。
    """
    if not isinstance(path, str) or not path.lower().endswith(_RASTER_SUFFIXES):
        return None
    try:
        from app.schemas.raster_spec import raster_content_fingerprint

        return raster_content_fingerprint(path)
    except Exception:  # noqa: BLE001 — 指纹失败按未知，不阻塞
        return None


def snapshot_raster_fingerprints(args: Any, *, max_paths: int = _MAX_RASTER_FPS) -> Dict[str, str]:
    """args 中的栅格文件路径 → 当前内容指纹（生产时快照；CPU 有界）。

    复用复核时重算并比对：源内容变 → miss（§34 cache miss 条件）。
    任何不可判定（文件缺失/读取失败）→ 该路径不进指纹表（保守：不因
    指纹缺席阻止命中——路径本身已在 analysis_key 里，删除/改名 → key 变）。
    """
    import os as _os

    fps: Dict[str, str] = {}

    def _walk(node: Any) -> None:
        if len(fps) >= max_paths:
            return
        if isinstance(node, str):
            if node not in fps and _os.path.exists(node):
                fp = _raster_path_fingerprint(node)
                if fp is not None:
                    fps[node] = fp
            return
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
                if len(fps) >= max_paths:
                    return
        elif isinstance(node, list):
            for v in node:
                _walk(v)
                if len(fps) >= max_paths:
                    return

    _walk(args)
    return fps


async def snapshot_input_shapes(
    session_id: str,
    args: Any,
    *,
    max_refs: int = 4,
) -> Dict[str, dict]:
    """args 中的输入 ref → 当前 descriptor 形状指纹快照（生产时调用）。

    只在 analysis_key 已成立（参数已过尺寸闸）后调用；ref 数上限
    max_refs（分析输入通常 1-3 个），descriptor 探测失败按未知处理
    （该 ref 不参与复用复核，不阻塞生产路径）。"""
    from app.services.session_data import session_data_manager

    refs: List[str] = []

    def _walk(node: Any) -> None:
        if len(refs) >= max_refs:
            return
        if isinstance(node, str):
            if node.startswith("ref:") and node not in refs:
                refs.append(node)
            return
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
                if len(refs) >= max_refs:
                    return
        elif isinstance(node, list):
            for v in node:
                _walk(v)
                if len(refs) >= max_refs:
                    return

    _walk(args)
    shapes: Dict[str, dict] = {}
    for ref in refs:
        try:
            fp = _ref_shape_fingerprint(
                await session_data_manager.get_ref_descriptor(session_id, ref)
            )
        except Exception:  # noqa: BLE001 — 探测失败按未知，不阻塞
            fp = None
        if fp is not None:
            shapes[ref] = fp
    return shapes


async def find_reusable_artifact(
    session_id: str,
    *,
    analysis_key: Optional[str],
    input_shapes: Optional[Dict[str, dict]] = None,
    raster_fingerprints: Optional[Dict[str, str]] = None,
    args: Any = None,
    now: Optional[float] = None,
    max_age_s: int = ANALYSIS_REUSE_MAX_AGE_S,
) -> Optional[Dict[str, Any]]:
    """查可复用产物（只读；任何不可判定 → None）。

    命中条件（§29 复用规则逐条落地）：
    - ArtifactRecord status == valid（superseded/stale/expired 一票否决）；
    - metadata.analysis_key == 本调用 key（同算法+同参数+同输入指针）；
    - recency ≤ max_age_s（实现升级的兜底失效）；
    - 产物 ref 的 descriptor 仍可探测（store TTL/LRU 驱逐 → miss）；
    - 输入 ref 形状指纹与生产时一致（in-place overwrite 守卫）；
    - 输入栅格内容指纹与生产时一致（同路径重写守卫，V3；args 给定时
      重算并比对 metadata.input_raster_fps）。

    返回 {"artifact_id", "feature_count", "bbox"} 或 None。
    """
    if not session_id or not analysis_key:
        return None
    from app.services.artifact_registry import list_artifacts
    from app.services.session_data import session_data_manager

    records = await list_artifacts(session_id)
    ts = now if now is not None else time.time()
    # 同 key 可能多条（重跑产生新 ref）；取 updated_at 最新的 valid。
    candidates = [
        r for r in records
        if r.status == "valid"
        and isinstance(r.metadata, dict)
        and r.metadata.get("analysis_key") == analysis_key
        and (ts - r.updated_at) <= max_age_s
    ]
    if not candidates:
        return None
    rec = max(candidates, key=lambda r: r.updated_at)

    # 产物本体仍存活？（探测失败按 miss —— 绝不复用不可验证的产物）
    try:
        out_desc = await session_data_manager.get_ref_descriptor(session_id, rec.artifact_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(out_desc, dict) or out_desc.get("feature_count") is None:
        return None

    # 输入形状复核（§29：输入变 → 不可复用）。
    if input_shapes:
        for ref, expected in input_shapes.items():
            try:
                cur = _ref_shape_fingerprint(
                    await session_data_manager.get_ref_descriptor(session_id, ref)
                )
            except Exception:  # noqa: BLE001
                return None
            if cur != expected:
                return None

    # 输入栅格内容复核（V3 §34：different source content → miss）。生产时
    # 记录了 input_raster_fps 的，此处对当前 args 重算指纹并比对；生产时
    # 没有栅格指纹记录的旧产物不因此判 miss（向后兼容）。
    if raster_fingerprints and isinstance(rec.metadata, dict):
        recorded = rec.metadata.get("input_raster_fps")
        if isinstance(recorded, dict) and recorded:
            for path, expected_fp in list(recorded.items())[:_MAX_RASTER_FPS]:
                cur_fp = _raster_path_fingerprint(str(path))
                if cur_fp is None or cur_fp != expected_fp:
                    return None

    fc = rec.feature_count if rec.feature_count is not None else out_desc.get("feature_count")
    return {
        "artifact_id": rec.artifact_id,
        "feature_count": fc,
        "bbox": list(rec.bbox) if rec.bbox else None,
    }
