"""Ingest Pipeline V3 —— 统一摄入管线（§二十五/§三十一/§六 dedup）。

审计 Agent B 的核心缺口：上传无内容指纹/无去重（同一文件两次导入 =
两个逻辑对象）、格式只认扩展名、缺 CRS 的栅格被静默接受、编码问题
变 500、三个摄入面（upload / session ref / fabric）互不相通。

本模块是**会话 ref 面的 V3 摄入管线**（对 upload 路由是增值适配，
不替换既有路径）：

```
detect → validate → profile → quality → register(source revision)
       → store(ref) → register_artifact → optional materialize
```

- **内容指纹去重**（§六 duplicate detection）：sha256 载荷字节 →
  同会话同指纹直接复用既有 ref（``duplicate_of``），不再铸造重复
  逻辑对象；指纹写入 descriptor 侧车道（payload 元数据）；
- **诚实 CRS**（§二十七）：payload 显式声明 crs 缺失时如实记录
  （绝不虚构 EPSG:4326）并交由 quality 报告警告；
- **失败回滚**（§二十五 failure rollback）：store 成功后任何一步失败
  → 删除已落 store 的 ref（补偿删除），绝不留半截产物；
- **全程有界**：profile 走 bounded profiler；契约注册走 registry
  既有锁与容错纪律。

大文件本体永远只进 session store（ref 语义），管线各步只传递
ref + 有界元数据。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.data.profile import DatasetProfileV3, profile_features
from app.lib.data.quality import run_quality_checks
from app.services.data_profile.profiler import get_dataset_profiler

logger = logging.getLogger(__name__)

_DEDUP_META_KEY = "ingest_content_sha256"


@dataclass
class IngestResult:
    """管线结果（成功 / 重复 / 失败 三态，全部字段诚实）。"""

    ok: bool = False
    duplicate: bool = False
    ref_id: str = ""
    duplicate_of: str = ""
    error: str = ""
    error_code: str = ""
    profile_summary: Dict[str, Any] = field(default_factory=dict)
    quality_summary: Dict[str, Any] = field(default_factory=dict)
    steps_completed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "duplicate": self.duplicate,
            "ref_id": self.ref_id or None,
            "duplicate_of": self.duplicate_of or None,
            "error": self.error or None,
            "error_code": self.error_code or None,
            "steps_completed": list(self.steps_completed),
            "profile": self.profile_summary or None,
            "quality": self.quality_summary or None,
        }


def compute_payload_fingerprint(data: Any) -> str:
    """载荷 → sha256（canonical JSON；去重键。不可序列化/含 NaN → repr 兜底）。

    与 fingerprints.canonical_fingerprint 的差异：dedup 键必须对**任意**
    运行时载荷可用（含 NaN/set），故兜底 repr —— 语义是同进程内
    「同指纹 ⇒ 同载荷」，跨进程稳定性不是目标。
    """
    try:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = repr(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_crs(value: Any) -> str:
    """CRS 证据归一：pre-RFC GeoJSON 的 crs 成员是 dict（取 properties.name）；
    其余标量 str()；无法提取 → ""（诚实未知）。"""
    if isinstance(value, dict):
        props = value.get("properties")
        if isinstance(props, dict):
            return str(props.get("name") or "")[:64]
        return ""
    if isinstance(value, str):
        return value[:64]
    if value is None:
        return ""
    return str(value)[:64]


def _extract_fc(data: Any) -> Optional[Dict[str, Any]]:
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        return fc
    return None


class IngestPipeline:
    """会话 ref 面 V3 摄入管线。"""

    def __init__(self) -> None:
        self._profiler = get_dataset_profiler()

    async def ingest(
        self,
        session_id: str,
        data: Any,
        *,
        name: str = "",
        crs: str = "",
        source_type: str = "inline",
        logical_role: Optional[str] = None,
        prefix: str = "ingest",
        materialize: bool = False,
        dedup: bool = True,
    ) -> IngestResult:
        """数据 → 会话产物（§二十五全流程；失败补偿回滚）。"""
        result = IngestResult()
        if not session_id:
            result.error_code = "NO_SESSION"
            result.error = "session_id required"
            return result

        # 1) Detect：只接受 FC 形状（其余形状走各自专用面 —— 诚实不吞）
        fc = _extract_fc(data)
        if fc is None:
            result.error_code = "UNSUPPORTED_SHAPE"
            result.error = "only FeatureCollection payloads are ingestable here"
            return result
        result.steps_completed.append("detect")

        # 2) Validate：载荷指纹（去重键）+ 基本形状（大载荷 O(n) 串行化
        # 与哈希卸载到线程 —— 不阻塞事件循环）。
        import asyncio as _asyncio

        fingerprint = await _asyncio.to_thread(compute_payload_fingerprint, data)
        result.steps_completed.append("validate")

        from app.services.session_data import session_data_manager

        # 3) Dedup（§六）：同会话同指纹 → 复用既有 ref
        if dedup:
            dup_ref = await self._find_duplicate(session_id, fingerprint)
            if dup_ref is not None:
                result.ok = True
                result.duplicate = True
                result.duplicate_of = dup_ref
                result.ref_id = dup_ref
                result.steps_completed.append("dedup-hit")
                return result
        result.steps_completed.append("dedup-miss")

        # 4) Profile（有界）+ Quality
        features = fc.get("features") if isinstance(fc.get("features"), list) else []
        declared_crs = _normalize_crs(
            crs
            or fc.get("crs")
            or (data.get("crs") if isinstance(data, dict) else "")
        )
        vp, quality = profile_features(features, crs=declared_crs)
        profile = DatasetProfileV3(
            target_ref="pending",
            category="vector",
            crs=declared_crs,
            extent=vp.extent,
            vector=vp,
            profile_quality=quality,
            source_fingerprint=fingerprint,
        )
        report = run_quality_checks(profile)
        result.steps_completed.append("profile")
        result.steps_completed.append("quality")

        # 5) Store（ref 铸造；失败即终止，无回滚对象）
        try:
            payload = dict(data) if isinstance(data, dict) else {"geojson": fc}
            if isinstance(payload, dict) and "geojson" not in payload:
                payload = {"geojson": fc}
            ref_id = await session_data_manager.store(
                session_id, payload, prefix=prefix
            )
        except Exception as e:  # noqa: BLE001
            result.error_code = "STORE_FAILED"
            result.error = f"session store failed: {e}"
            return result
        result.ref_id = ref_id
        result.steps_completed.append("store")

        # 6) Register artifact（失败 → 补偿删除 ref；§二十五 rollback）
        try:
            from app.services.artifact_registry import register_artifact

            rec = await register_artifact(
                session_id,
                artifact_id=ref_id,
                artifact_type="feature_collection",
                producer_tool="ingest_pipeline",
                metadata={
                    "source_type": str(source_type)[:32],
                    "display_name": str(name or "")[:96],
                    "content_fingerprint": fingerprint,
                    _DEDUP_META_KEY: fingerprint,
                    "logical_role": (logical_role or "source"),
                },
            )
            if rec is None:
                raise RuntimeError("artifact registration declined")
        except Exception as e:  # noqa: BLE001
            await self._rollback_ref(session_id, ref_id)
            result.error_code = "REGISTER_FAILED_ROLLED_BACK"
            result.error = f"registration failed, ref rolled back: {e}"
            return result
        result.steps_completed.append("register")

        # 7) Profile 绑定产物 + 汇总输出
        profile.target_ref = ref_id
        result.profile_summary = profile.summary()
        result.quality_summary = report.summary()
        result.ok = True
        if materialize:
            # V3 语义中 materialize = 确认会话载荷就是持久载体（当前全部
            # 会话 ref 即 session store 载荷）—— 显式声明 session 持久层。
            from app.services.artifact_registry import update_record_metadata

            await update_record_metadata(
                session_id, ref_id, metadata={"materialization_policy": "cached",
                                              "persistence_tier": "session"}
            )
            result.steps_completed.append("materialize")
        return result

    async def _find_duplicate(self, session_id: str, fingerprint: str) -> Optional[str]:
        from app.services.artifact_registry import list_artifacts

        for rec in await list_artifacts(session_id):
            md = getattr(rec, "metadata", None) or {}
            if isinstance(md, dict) and md.get(_DEDUP_META_KEY) == fingerprint:
                if rec.status == "valid":
                    return rec.artifact_id
        return None

    async def _rollback_ref(self, session_id: str, ref_id: str) -> None:
        try:
            from app.services.session_data import session_data_manager

            await session_data_manager.delete_ref(session_id, ref_id)
        except Exception:  # noqa: BLE001 — 回滚失败只记录；孤儿 ref 无台账
            # 记录、GC 不可见，仅随会话 LRU/TTL 逐出（会话级有界，不跨会话泄漏）。
            logger.warning(
                "[IngestPipeline] rollback failed session=%s ref=%s",
                session_id, ref_id,
            )


_pipeline: Optional[IngestPipeline] = None


def get_ingest_pipeline() -> IngestPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = IngestPipeline()
    return _pipeline


def reset_ingest_pipeline() -> None:
    global _pipeline
    _pipeline = None
