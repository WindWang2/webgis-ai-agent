"""G41 同版本原子冻结（WP03 / B15 / B37 / B38）。

G33 的 ``_hydrate_features`` 分开读 descriptor 与内容，且缺 revision 时回退
0 —— **不是**一致快照保证。本模块为该领域的成对统计建立更强的合同：

1. 在权威 ``session_lock`` 内先读 descriptor（rev0）；
2. 用一次 ``session_data_manager.get()`` 取**整份** FeatureCollection —— 该
   API 在存储 ``self._lock`` 内取对象引用、锁外 ``to_thread(deepcopy)`` 返回
   调用方独占的深拷贝；覆写是**替换对象**而非原地修改，因此拿到的对象在
   读取期不会被混入另一代内容。不分页、不按可见瓦片/视窗拼样本；
3. 再读 descriptor（rev1）。``rev0 != rev1`` → typed 拒绝。
4. ``content_revision`` 是权威存储的**单调 generation**：每次 ``overwrite``
   +1（`session_data.py:505-507`）。「改值后恢复原内容」的 ABA 仍会推进两次
   generation，因此被 rev 前后核对捕获；**不靠前后 fingerprint 相同**来判定
   同代（那正是任务明确禁止的做法）。
5. 无 descriptor / revision 缺失或为 0 → ``NO_STRONG_REVISION`` typed 拒绝，
   不 warning 后发布；客户端给定 revision、mtime/size、边角 hash 都不构成保证。
6. 冻结副本计算 ``content_fingerprint`` 仅作辅助标识与前后对账，不作为版本
   保证。冻结结果覆盖统计、分类、派生表达与发布所用的**全部**字段/范围。

apply 时不是「相信 preview 的数字」而是**重新冻结**并与计划锚核对，故两批
之间任何写入都会导致 typed 拒绝，绝不发布混合两代统计与地图。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    MAX_SOURCE_FEATURES,
    SourceAnchor,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrozenSource:
    """一次同代冻结：内容与版本原子绑定，调用方只读。"""

    ref_id: str
    content_revision: int
    content_fingerprint: str
    features: List[Dict[str, Any]]
    feature_count: int
    algorithm_version: str = ALGORITHM_VERSION

    def anchor(self) -> Dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "content_revision": self.content_revision,
            "content_fingerprint": self.content_fingerprint,
            "feature_count": self.feature_count,
            "algorithm_version": self.algorithm_version,
        }


def _json_safe(value: Any) -> Any:
    """把 NaN/Inf 变成可复现的字符串标记（否则 allow_nan=False 会抛）。"""
    if isinstance(value, float) and not math.isfinite(value):
        return "__nonfinite__:" + repr(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def content_fingerprint(features: List[Any]) -> str:
    """冻结内容的规范化 SHA-256（辅助标识，不是版本保证）。"""
    payload = json.dumps(
        _json_safe(features), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False, default=str,
    )
    return "g41f-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def extract_feature_collection(payload: Any) -> List[Dict[str, Any]]:
    """从权威 ref payload 提取完整 FeatureCollection 的 features 列表。"""
    fc = payload
    if isinstance(payload, dict) and payload.get("type") != "FeatureCollection":
        fc = payload.get("geojson")
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise AuthoringError(
            "SOURCE_NOT_FEATURECOLLECTION",
            "源 ref 不是 FeatureCollection，无法建立成对统计", status_code=409,
            correction_hint="使用矢量 GeoJSON ref 源；纯瓦片 URL/视窗不构成完整源。")
    raw = fc.get("features")
    if not isinstance(raw, list):
        raise AuthoringError(
            "SOURCE_NOT_FEATURECOLLECTION",
            "FeatureCollection.features 不是数组", status_code=409)
    if len(raw) > MAX_SOURCE_FEATURES:
        raise AuthoringError(
            "SOURCE_TOO_LARGE",
            f"源要素 {len(raw)} 超过硬帽 {MAX_SOURCE_FEATURES}；"
            "超限明确拒绝，不静默截断后称完整", status_code=413)
    return [f for f in raw if isinstance(f, dict)]


def source_ref_of(source_entry: Dict[str, Any]) -> Optional[str]:
    """MapSpec source entry → 会话 ref id（只认 ``ref:`` 前缀）。"""
    for key in ("ref_id", "ref"):
        v = source_entry.get(key)
        if isinstance(v, str) and v.startswith("ref:"):
            return v
    return None


async def _descriptor_revision(session_id: str, ref_id: str) -> int:
    """读权威 descriptor 的 live content_revision；无强版本 → typed 拒绝。"""
    from app.services.session_data import session_data_manager

    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
    if not isinstance(descriptor, dict):
        raise AuthoringError(
            "NO_STRONG_REVISION",
            "源 ref 没有权威 descriptor —— 无法证明内容与版本同代；"
            "不把『缺失 revision=0』当作稳定版本",
            status_code=409,
            correction_hint="使用由 session store 建立且带 content_revision 的 ref 源。")
    raw = descriptor.get("content_revision")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AuthoringError(
            "NO_STRONG_REVISION",
            f"源 ref 没有可用的强内容版本（content_revision={raw!r}）；"
            "不能用 mtime/size/边角 hash/客户端给定 revision 替代",
            status_code=409,
            correction_hint="重新以权威存储写入该 ref，或改用支持单调版本的源。")
    return raw


async def freeze_source(
    session_id: str,
    ref_id: str,
    *,
    owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> FrozenSource:
    """建立一次同代原子冻结。失败一律 typed 拒绝，不返回降级快照。"""
    from app.services.distributed_lock import session_lock
    from app.services.session_data import session_data_manager

    if not (isinstance(ref_id, str) and ref_id.startswith("ref:")):
        raise AuthoringError(
            "NO_STRONG_REVISION",
            f"层源 {ref_id!r} 不是会话 ref —— 无权威单调版本可绑定",
            status_code=409,
            correction_hint="G41 只接受 ref: 矢量源；MVT 可见瓦片/纯 URL 不构成完整源。")

    resolved = await session_data_manager.resolve_alias(session_id, ref_id)

    # 鉴权双通道（与 G20/G33 同款）：HTTP 路由由 require_owned_session 守卫并
    # 传 owner_token；Pi 工具走 trusted_dispatch（session_id 由 ToolRegistry
    # 从已授权上下文注入，模型不能指定别的 session）。这里额外做一次所有权
    # 校验，随后冻结读取仍走权威 get() 的深拷贝。
    if not trusted_dispatch:
        auth = await session_data_manager.get_ref_data_shared(
            session_id, resolved, owner_token=owner_token)
        if not getattr(auth, "success", False):
            raise AuthoringError(
                "SOURCE_UNAVAILABLE",
                f"源 ref 不可用或无访问权：{getattr(auth, 'error', 'unknown')}",
                status_code=403)

    async with session_lock(session_id, fail_on_degraded=True, fail_on_lost=True):
        rev0 = await _descriptor_revision(session_id, resolved)
        # 单次 get() 已返回独占深拷贝；读取期不分页、不追加 chunk。
        payload = await session_data_manager.get(session_id, resolved)
        if payload is None:
            raise AuthoringError(
                "SOURCE_UNAVAILABLE", "冻结读取期间源 ref 消失", status_code=409)
        features = extract_feature_collection(payload)
        fingerprint = content_fingerprint(features)
        rev1 = await _descriptor_revision(session_id, resolved)
        if rev1 != rev0:
            raise AuthoringError(
                "SOURCE_MUTATION_DURING_READ",
                f"读取期间源 content_revision 从 {rev0} 变为 {rev1} —— "
                "拒绝发布混合两代的统计/分类/表达；请重新 inspect 并重做预览",
                status_code=409,
                correction_hint="重新冻结同代快照后再预览/应用。")

    return FrozenSource(
        ref_id=resolved,
        content_revision=rev0,
        content_fingerprint=fingerprint,
        features=features,
        feature_count=len(features),
    )


def check_anchor(plan_anchor: Optional[SourceAnchor], frozen: FrozenSource) -> None:
    """计划锚 vs 新冻结代次：任何漂移 → STALE_SOURCE（含 ABA）。"""
    if plan_anchor is None:
        return
    if plan_anchor.ref_id != frozen.ref_id:
        raise AuthoringError(
            "STALE_SOURCE",
            f"计划锚定源 {plan_anchor.ref_id!r} ≠ 当前源 {frozen.ref_id!r}",
            status_code=409,
            correction_hint="重新 inspect 获取当前源锚后再提交。")
    if plan_anchor.content_revision != frozen.content_revision:
        raise AuthoringError(
            "STALE_SOURCE",
            f"源内容代次已变化（计划 rev={plan_anchor.content_revision}，"
            f"当前 rev={frozen.content_revision}）——单调 generation 捕获了刷新/ABA",
            status_code=409,
            correction_hint="数据已被编辑或刷新，重新检查分布并重做计划；不盲目重试覆盖。")
    if (plan_anchor.content_fingerprint
            and plan_anchor.content_fingerprint != frozen.content_fingerprint):
        raise AuthoringError(
            "STALE_SOURCE",
            "源内容指纹与计划锚不一致（同 rev 但内容被替换）——拒绝混合来源",
            status_code=409,
            correction_hint="重新 inspect 并重做预览。")
