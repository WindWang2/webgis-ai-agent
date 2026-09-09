"""InferenceManifest —— 推理出处（ADR-0119 §3.6/§L；Epic §L）。

字段集 = Epic 规格全集（model id/version/checksum、provider、输入、
preprocessing、reproject、tile 参数、thresholds、postprocess、software
env、inference time、class schema、confidence semantics、perf counters、
reuse identity）。进入 reuse eligibility；secret 经 redaction 单一口径。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.lib.data.fingerprints import canonical_dumps, sha256_hex
from app.lib.modelops.fingerprint import (
    INFERENCE_OUTPUT_SCHEMA_VERSION,
    software_env_fingerprint,
)
from app.services.provenance.manifest import redact_provenance_args

MANIFEST_SCHEMA_VERSION = "modelops.inference-manifest/v1"


def build_inference_manifest(
    *,
    descriptor_payload: Dict[str, Any],
    provider_payload: Dict[str, Any],
    input_payload: Dict[str, Any],
    preprocess_payload: Dict[str, Any],
    tile_plan_payload: Dict[str, Any],
    postprocess_payload: Dict[str, Any],
    reproject_payload: Optional[Dict[str, Any]],
    owner_scope: Dict[str, str],
    task_type: str,
    outputs: List[Dict[str, Any]],
    perf: Dict[str, Any],
    reuse_key: Optional[str],
    reused: bool,
    compatibility: Dict[str, Any],
    run_id: str,
    device_plan: Dict[str, Any],
    prompt_payload: Optional[Dict[str, Any]] = None,
    temporal_payload: Optional[Dict[str, Any]] = None,
    evaluation_payload: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 manifest dict（redact 后 sha256 指纹；结构稳定可审计）。"""
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "output_schema_version": INFERENCE_OUTPUT_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": time.time(),
        "task_type": task_type,
        "owner_scope": dict(sorted(owner_scope.items())),
        "model": descriptor_payload,
        "provider": provider_payload,
        "input": input_payload,
        "preprocess": preprocess_payload,
        "reproject": reproject_payload,
        "tile_plan": tile_plan_payload,
        "postprocess": postprocess_payload,
        "prompt": prompt_payload,
        "temporal": temporal_payload,
        "compatibility": compatibility,
        "device_plan": device_plan,
        "outputs": outputs,
        "evaluation": evaluation_payload,
        "performance": perf,
        "software_env": software_env_fingerprint(),
        "reuse_key": reuse_key,
        "reused": reused,
        "error": error,
    }
    # 指纹先于 redaction（redaction 有 4KB 全局界，超界返回 str 形态）。
    fingerprint = sha256_hex(canonical_dumps(manifest))
    redacted = redact_provenance_args(manifest)
    if isinstance(redacted, dict):
        redacted["manifest_fingerprint"] = fingerprint
        return redacted
    manifest["manifest_fingerprint"] = fingerprint
    return manifest
