"""InferenceFingerprint —— 推理复用身份（同 key 必同结果）。

复用资格 = 下列全部字段**精确匹配**（canonical JSON → sha256）：

- 模型：descriptor 语义投影（含 checksum/version/normalization/空间/时序）；
- provider：形态 + semantic_version（provider 语义升级 ⇒ 旧结果不可复用）；
- 输入：DataObject content_sha256（内容寻址，revision 变化即内容变化）；
- 预处理：band select/order、reprojection（目标 CRS+resampling）、归一化；
- tile plan：chip/stride/overlap/padding/geometry（尺寸+transform）；
- 后处理：thresholds/postprocess 参数/output schema 版本；
- 策略：owner policy scope（复用绝不跨 owner）。

明确**不**进入 key：注册者/时间戳等 provenance 元数据、墙钟时间、
请求侧标签。

**输入内容身份的唯一口径（架构挑战 B1，硬约束）**：
``input_content_sha256`` 必须是

- DataObject 输入 → DataObject manifest 的 merkle ``content_sha256``
  （app/services/lakehouse/data_object.py 发布即有）；或
- 非 DataObject 输入 → 全内容流式 sha256（chunk_digest 口径，
  app/lib/geo_raster/chunk.py:332）。

**禁止**使用 ``RasterMetadata.fingerprint``（reader 角块摘要，reader.py
自注 "identity, NOT a cache-invalidation key"）参与复用 key——mid-raster
编辑不可见，会命中陈旧结果。引擎侧的 ``compute_input_content_identity``
是唯一合法构造点。

**归一化统计来源（架构挑战 M3-5，硬约束）**：归一化统计必须是
descriptor 固定声明（NormalizationSpec）；逐景采样统计是隐藏参数，
引擎拒绝（逐景统计既不可进 key 也不可复现）。

``software_env`` 指纹参与 key（numpy/rasterio/python 版本，有界）——
跨环境复用必须显式 ``include_software_env=False``（诚实降级，调用方
自担）。provider 身份 = provider_ref + semantic_version + capabilities
投影（架构挑战 M3-1：同 semantic_version 的不同实现因 provider_ref
不同而 key 不同）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.lib.data.fingerprints import canonical_dumps, sha256_hex

#: 输出 schema 版本：merge/postprocess 语义变化时 bump ⇒ 全量复用失效。
INFERENCE_OUTPUT_SCHEMA_VERSION = "modelops.inference-output/v1"

_REUSE_FINGERPRINT_VERSION = "modelops.reuse-key/v1"

try:  # 软件环境指纹（缺依赖不致命——指纹字段如实标注 unavailable）
    import platform as _platform

    import numpy as _np
    import rasterio as _rio

    _SOFTWARE_ENV = (
        f"python={_platform.python_version()};"
        f"numpy={_np.__version__};rasterio={_rio.__version__}"
    )
except Exception:  # pragma: no cover - 无栅格栈的环境
    _SOFTWARE_ENV = "unavailable"


@dataclass(frozen=True)
class ReuseKeyComponents:
    """复用 key 的结构化成分（构造方负责全部来源真实）。"""

    descriptor_payload: Dict[str, Any]
    provider_payload: Dict[str, Any]
    input_content_sha256: str
    preprocess_payload: Dict[str, Any]
    tile_plan_payload: Dict[str, Any]
    postprocess_payload: Dict[str, Any]
    owner_scope: Dict[str, str]
    #: V3 §C：双时相任务的 B 时相内容身份（None = 单栅格任务；不参与 key
    #  会造成「同 key 不同结果」——不同 B 影像命中同一缓存）。
    input_b_content_sha256: Optional[str] = None
    include_software_env: bool = True

    def canonical(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "key_version": _REUSE_FINGERPRINT_VERSION,
            "output_schema_version": INFERENCE_OUTPUT_SCHEMA_VERSION,
            "descriptor": self.descriptor_payload,
            "provider": self.provider_payload,
            "input_content_sha256": self.input_content_sha256,
            "preprocess": self.preprocess_payload,
            "tile_plan": self.tile_plan_payload,
            "postprocess": self.postprocess_payload,
            "owner_scope": dict(sorted(self.owner_scope.items())),
        }
        if self.input_b_content_sha256 is not None:
            payload["input_b_content_sha256"] = self.input_b_content_sha256
        if self.include_software_env:
            payload["software_env"] = _SOFTWARE_ENV
        return payload

    def digest(self) -> str:
        return sha256_hex(canonical_dumps(self.canonical()))


def build_reuse_key(
    *,
    descriptor_payload: Dict[str, Any],
    provider_payload: Dict[str, Any],
    input_content_sha256: str,
    preprocess_payload: Dict[str, Any],
    tile_plan_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    owner_scope: Dict[str, str],
    input_b_content_sha256: Optional[str] = None,
    include_software_env: bool = True,
) -> str:
    """一次性构造复用 key（sha256 hex）。"""
    return ReuseKeyComponents(
        descriptor_payload=descriptor_payload,
        provider_payload=provider_payload,
        input_content_sha256=input_content_sha256,
        preprocess_payload=preprocess_payload,
        tile_plan_payload=tile_plan_payload,
        postprocess_payload=postprocess_payload,
        owner_scope=owner_scope,
        input_b_content_sha256=input_b_content_sha256,
        include_software_env=include_software_env,
    ).digest()


def software_env_fingerprint() -> str:
    """当前软件环境指纹（进 inference manifest；测试断言用）。"""
    return _SOFTWARE_ENV


def canonical_payload(payload: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    """把 dict + kwargs 合成为 canonical JSON 保险的 payload（None 剔除）。"""
    merged: Dict[str, Any] = dict(payload or {})
    merged.update(extra)
    return {k: v for k, v in merged.items() if v is not None}
