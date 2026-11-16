"""G47 同代原子冻结（WP03 / D18 / D19）。

合同与 G41 一致（``bivariate_reliability_authoring/snapshot.py`` 是同一模式的
先例），本域只改**资格**（面要素/统计单元上限），不改保证强度：

1. 在权威 ``session_lock`` 内先读 descriptor（rev0）；
2. 一次 ``session_data_manager.get()`` 取整份 FeatureCollection —— 该 API 在存储
   锁内取对象引用、锁外深拷贝返回调用方独占副本，覆写是**替换对象**而非原地改；
3. 再读 descriptor（rev1）。``rev0 != rev1`` → typed 拒绝（``SOURCE_MUTATION_DURING_READ``）；
4. ``content_revision`` 是权威存储的**单调 generation**：改值后恢复原内容（ABA）
   仍推进两次，因此被版本核对捕获 —— 不靠"前后 fingerprint 相同"判定同代；
5. 缺 descriptor / revision < 1 → ``NO_STRONG_REVISION`` typed 拒绝，绝不 warning
   后继续；mtime/size/客户端 revision 都不构成保证；
6. 冻结副本算 ``content_fingerprint`` 仅作辅助标识与对账。

apply 阶段不是"相信预览的数字"，而是**重新冻结**并与计划锚核对。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.cartogram_authoring.contract import ALGORITHM_VERSION
from app.services.cartogram_authoring.errors import AuthoringError

logger = logging.getLogger(__name__)

#: 单次冻结读取的要素硬帽（范围收窄后还需通过 plan.budgets.max_features）。
SOURCE_FEATURE_HARD_CAP = 5000


@dataclass(frozen=True)
class FrozenSource:
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
            "snapshot_guarantee": (
                "authoritative monotonic content_revision checked before/after a "
                "single deep-copied read under session_lock; ABA captured by revision"),
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "__nonfinite__:" + repr(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def content_fingerprint(features: List[Any]) -> str:
    payload = json.dumps(
        _json_safe(features), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False, default=str,
    )
    return "g47f-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def extract_feature_collection(payload: Any) -> List[Dict[str, Any]]:
    fc = payload
    if isinstance(payload, dict) and payload.get("type") != "FeatureCollection":
        fc = payload.get("geojson")
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise AuthoringError(
            "SOURCE_NOT_FEATURECOLLECTION",
            "源 ref 不是 FeatureCollection —— 纯瓦片/视窗/图表不能建立完整区域总体",
            status_code=409,
            correction_hint="使用矢量 GeoJSON ref 源（每个统计单元一个面要素）。")
    raw = fc.get("features")
    if not isinstance(raw, list):
        raise AuthoringError(
            "SOURCE_NOT_FEATURECOLLECTION", "FeatureCollection.features 不是数组",
            status_code=409)
    if len(raw) > SOURCE_FEATURE_HARD_CAP:
        raise AuthoringError(
            "SOURCE_TOO_LARGE",
            f"源要素 {len(raw)} 超过冻结硬帽 {SOURCE_FEATURE_HARD_CAP}；"
            "超限明确拒绝，不静默截断后称完整",
            status_code=413,
            correction_hint="先聚合到统计单元（或收窄范围）再进入 cartogram。")
    return [f for f in raw if isinstance(f, dict)]


def source_ref_of(source_entry: Dict[str, Any]) -> Optional[str]:
    for key in ("ref_id", "ref"):
        v = source_entry.get(key)
        if isinstance(v, str) and v.startswith("ref:"):
            return v
    return None


async def _descriptor_revision(session_id: str, ref_id: str) -> int:
    from app.services.session_data import session_data_manager

    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
    if not isinstance(descriptor, dict):
        raise AuthoringError(
            "NO_STRONG_REVISION",
            "源 ref 没有权威 descriptor —— 无法证明内容与版本同代；"
            "不把『缺失 revision=0』当稳定版本",
            status_code=409,
            correction_hint="使用由 session store 建立且带 content_revision 的 ref 源。")
    raw = descriptor.get("content_revision")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AuthoringError(
            "NO_STRONG_REVISION",
            f"源 ref 没有可用的强内容版本（content_revision={raw!r}）；"
            "mtime/size/边角 hash/客户端 revision 都不是保证",
            status_code=409,
            correction_hint="以权威存储重新写入该 ref，或改用支持单调版本的源。")
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
            correction_hint="把数据注册为 session ref 源；MVT 可见瓦片/URL 不构成完整源。")

    resolved = await session_data_manager.resolve_alias(session_id, ref_id)

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
                "拒绝发布混合两代的布局；请重新 inspect 并重做计划",
                status_code=409,
                correction_hint="重新冻结同代快照后再运行求解。")

    return FrozenSource(
        ref_id=resolved, content_revision=rev0, content_fingerprint=fingerprint,
        features=features, feature_count=len(features))


def check_anchor(plan_anchor: Optional[Dict[str, Any]], frozen: FrozenSource) -> None:
    """布局锚 vs 新冻结代次：任何漂移 → STALE_SOURCE（含 ABA）。"""
    if not plan_anchor:
        return
    a_ref = plan_anchor.get("ref_id")
    if a_ref and a_ref != frozen.ref_id:
        raise AuthoringError(
            "STALE_SOURCE",
            f"计划锚定源 {a_ref!r} ≠ 当前源 {frozen.ref_id!r}",
            status_code=409, correction_hint="重新 inspect 获取当前源锚。")
    a_rev = plan_anchor.get("content_revision")
    if isinstance(a_rev, int) and a_rev != frozen.content_revision:
        raise AuthoringError(
            "STALE_SOURCE",
            f"源内容代次已变化（计划 rev={a_rev}，当前 rev={frozen.content_revision}）——"
            "单调 generation 捕获了刷新/ABA",
            status_code=409,
            correction_hint="数据已被编辑/刷新，重新检查分布并重做计划。")
    a_fp = plan_anchor.get("content_fingerprint")
    if isinstance(a_fp, str) and a_fp and a_fp != frozen.content_fingerprint:
        raise AuthoringError(
            "STALE_SOURCE",
            "源内容指纹与计划锚不一致（同 rev 但内容被替换）——拒绝混合来源",
            status_code=409, correction_hint="重新 inspect 并重做计划。")
