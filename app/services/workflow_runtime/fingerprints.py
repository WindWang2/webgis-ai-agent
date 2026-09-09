"""Workflow Runtime V5 —— 复用指纹栈（输入/参数/算法/环境/包）。

复用指纹是「同语义工作可跳过执行」的唯一裁决键。红线：

- **宁可假 miss，绝不假 hit**（架构 §6 [R1-M3]）：输入内容身份取 live
  descriptor（非 registry 冻结 metadata）；``shape`` 级指纹只记录不复用；
  ``content_revision`` 必进指纹 —— 同 ref 原地覆写（session overwrite）
  在低级指纹下也必须 miss；
- env_fp 覆盖一切影响数值输出的版本面：运行时/编译器/执行契约版本、
  geo 数值栈（geopandas/shapely/pyproj）实际安装版本、方法论注册表
  指纹、算法注册表指纹（同 id 换实现/换版本 → 全体复用键失效）；
- 全部确定性：同输入同指纹；有界：输入端口 ≤8 参与指纹。
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from typing import Any, Dict, Optional, Tuple

from app.services.workflow_runtime import RUNTIME_SCHEMA_VERSION

#: 指纹 v2（v1 未发布过；字段演进时递增使旧键自然失效）。
_REUSE_FINGERPRINT_VERSION = 2
#: 参与指纹的输入端口上限（与 TypedPort inputs 预算一致）。
_MAX_FINGERPRINT_INPUTS = 8

#: 内容指纹级别词表（eligibility 只认 content / profile_digest）。
FP_LEVEL_CONTENT = "content"
FP_LEVEL_PROFILE_DIGEST = "profile_digest"
FP_LEVEL_SHAPE = "shape"          # 只记录，绝不复用
REUSABLE_LEVELS = frozenset({FP_LEVEL_CONTENT, FP_LEVEL_PROFILE_DIGEST})

#: geo 数值栈：import 失败的成员跳过（诚实降级 —— 少一个分量比假 0 好）。
_GEO_STACK_MODULES: Tuple[str, ...] = ("geopandas", "shapely", "pyproj", "numpy")

_geo_stack_digest_cache: Optional[str] = None
_algorithm_registry_fp_cache: Tuple[Optional[Any], str] = (None, "")


def canonical_fingerprint(payload: Any) -> str:
    """canonical-JSON sha256（截 32；与 workflow_instance 同纪律）。"""
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
    except Exception:  # noqa: BLE001 — 指纹失败退化为 repr（诚实退化）
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8"),
                          usedforsecurity=False).hexdigest()[:32]


def geo_stack_digest() -> str:
    """geo 数值栈版本 digest（进程内缓存；env_fp 分量 [R1-M4]）。"""
    global _geo_stack_digest_cache
    if _geo_stack_digest_cache is not None:
        return _geo_stack_digest_cache
    versions: Dict[str, str] = {}
    for mod in _GEO_STACK_MODULES:
        try:
            versions[mod] = importlib.metadata.version(mod)
        except Exception:  # noqa: BLE001 — 未安装的成员不参与
            continue
    _geo_stack_digest_cache = canonical_fingerprint(versions)
    return _geo_stack_digest_cache


def algorithm_registry_fingerprint() -> str:
    """算法注册表指纹：(algorithm_id, version) 全集 digest（缓存随注册表
    单例身份失效——reset 后重建）[R1-M4]。"""
    global _algorithm_registry_fp_cache
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    reg = get_algorithm_registry()
    cached_reg, cached_fp = _algorithm_registry_fp_cache
    if cached_reg is reg and cached_fp:
        return cached_fp
    try:
        pairs = sorted(
            (algo_id, str(getattr(desc, "version", "") or ""))
            for algo_id, desc in getattr(reg, "_by_id", {}).items()
        )
    except Exception:  # noqa: BLE001 — 指纹是增值面，失败诚实留空
        pairs = []
    fp = canonical_fingerprint(pairs)
    _algorithm_registry_fp_cache = (reg, fp)
    return fp


def environment_fingerprint(
    *,
    compiler_version: str,
    execution_plan_version: int,
    methodology_fingerprint: str = "",
) -> str:
    """环境指纹：影响数值输出的全部版本面 [R1-M4]。"""
    return canonical_fingerprint({
        "runtime": RUNTIME_SCHEMA_VERSION,
        "compiler": str(compiler_version),
        "plan": int(execution_plan_version),
        "geo_stack": geo_stack_digest(),
        "methodology": str(methodology_fingerprint)[:64],
        "algorithms": algorithm_registry_fingerprint(),
    })


def canonicalize_parameters(params: Any) -> Any:
    """参数 canonical 化（同语义同指纹）：
    - dict：键排序递归；
    - 数值：int/float 统一为 float 再比较（1 与 1.0 同参）；
    - list：保序（参数序有语义）。
    """
    if isinstance(params, dict):
        return {str(k): canonicalize_parameters(params[k])
                for k in sorted(params, key=str)}
    if isinstance(params, bool):
        return params
    if isinstance(params, (int, float)):
        return float(params)
    if isinstance(params, (list, tuple)):
        return [canonicalize_parameters(x) for x in params]
    return str(params)


def input_content_identity(descriptor: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """live descriptor → 内容身份 {level, fp, content_revision}。

    级别取最高可用者：
    1. ``content`` —— descriptor 内容 sha256（``content_sha256``，≤1MB 且
       开关开启时才有）；
    2. ``profile_digest`` —— 画像 digest 指纹（内嵌 content_revision，
       覆写自愈）；
    3. ``shape`` —— 形状摘要 + ref 身份（**只记录不复用**：对同 ref
       原地覆写失明）。
    descriptor 缺席 → level=""（空身份，复用必 miss）。
    """
    if not isinstance(descriptor, dict):
        return {"level": "", "fp": "", "content_revision": ""}
    revision = str(descriptor.get("content_revision") or "")
    sha = str(descriptor.get("content_hash") or descriptor.get("content_sha256") or "")
    if sha:
        return {"level": FP_LEVEL_CONTENT, "fp": sha[:32],
                "content_revision": revision}
    digest = descriptor.get("profile_digest")
    if isinstance(digest, dict) and digest:
        return {"level": FP_LEVEL_PROFILE_DIGEST,
                "fp": canonical_fingerprint(digest),
                "content_revision": revision}
    shape = {
        "ref": str(descriptor.get("ref_id") or descriptor.get("ref") or ""),
        "feature_count": descriptor.get("feature_count"),
        "row_count": descriptor.get("row_count"),
        "geometry_kind": str(descriptor.get("geometry_kind") or ""),
        "crs": str(descriptor.get("crs") or ""),
    }
    return {"level": FP_LEVEL_SHAPE, "fp": canonical_fingerprint(shape),
            "content_revision": revision}


def node_reuse_fingerprint(
    *,
    package_fingerprint: str,
    node_id: str,
    algorithm_id: str,
    params: Any,
    inputs: Dict[str, Dict[str, str]],
    env_fp: str,
) -> str:
    """工作流节点复用指纹（架构 §6）。

    ``inputs``: {port: input_content_identity(...) 的输出}。
    """
    bounded_inputs = {
        str(port)[:48]: {
            "level": str(ident.get("level") or ""),
            "fp": str(ident.get("fp") or ""),
            "rev": str(ident.get("content_revision") or ""),
        }
        for port, ident in list(inputs.items())[:_MAX_FINGERPRINT_INPUTS]
    }
    payload = {
        "v": _REUSE_FINGERPRINT_VERSION,
        "pkg": str(package_fingerprint)[:64],
        "node": str(node_id)[:64],
        "algo": str(algorithm_id)[:64],
        "params": canonicalize_parameters(params),
        "inputs": bounded_inputs,
        "env": str(env_fp)[:32],
    }
    return canonical_fingerprint(payload)


__all__ = [
    "FP_LEVEL_CONTENT",
    "FP_LEVEL_PROFILE_DIGEST",
    "FP_LEVEL_SHAPE",
    "REUSABLE_LEVELS",
    "algorithm_registry_fingerprint",
    "canonical_fingerprint",
    "canonicalize_parameters",
    "environment_fingerprint",
    "geo_stack_digest",
    "input_content_identity",
    "node_reuse_fingerprint",
]
