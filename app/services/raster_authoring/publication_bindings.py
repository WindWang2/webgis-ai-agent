"""G26 出版冻结：一次读 MapSpec + 逐层 owner 校验的栅格绑定。

复用 G20 的冻结纪律（``frozen_layers`` 的 CAS/单读/复检模式），但冻结的是
**整份文档**（矢量 + 栅格同修订）并产出可序列化的 per-layer 绑定
（scoped 安全路径 + stat 身份 + 已冻结 RasterExpressionSpec）—— 供同步
编译面（mapspec_to_svg / publication_export / worker 子进程）按窗口渲染。

纪律：
- 一次读 = 一个冻结快照；绑定解析后复检修订，移动即 ``CAS_SUPERSEDED``。
- ``resolve_source``（owner 三重校验）+ ``STALE_BINDING`` 复核 + stat 冻结；
  离线产物只携带嵌入 bytes，绝不写 session URL / owner token。
- 无 raster_style 的层不进绑定（矢量编译面自理）；绑定失败的层不静默：
  typed 失败上抛（blocking）或按调用方策略进 ``unbound`` 披露。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.schemas.raster_spec import RasterExpressionError


def _binding_payload(layer_id: str, source_id: str, ref_id: str, path: str,
                     stat_key: tuple[int, int], spec_payload: Dict[str, Any],
                     style_id: str, data_revision: str, crs: Optional[str],
                     source_bounds: Optional[List[float]]) -> Dict[str, Any]:
    """绑定 → JSON 可序列化 dict（跨 worker 子进程边界 / manifest 共用）。"""
    return {
        "layer_id": layer_id,
        "source_id": source_id,
        "ref_id": ref_id,
        "path": path,
        "stat_key": [int(stat_key[0]), int(stat_key[1])],
        "spec": spec_payload,
        "style_id": style_id,
        "data_revision": data_revision,
        "crs": crs,
        "source_bounds": source_bounds,
    }


async def freeze_raster_bindings(
    session_id: str,
    document: Dict[str, Any],
    *,
    revision: int,
    owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
    on_unresolvable: str = "raise",
) -> Dict[str, Any]:
    """冻结文档 → 数值栅格层绑定（见模块 docstring）。

    ``document``/``revision`` 由调用方按 ``frozen_layers`` 同模式先行读取
    （一次读冻结）；本函数只做逐层 owner 校验 + 绑定，结束后由调用方复检
    修订（见 ``mixed_map_export``）。``on_unresolvable``：
    ``raise``（默认，blocking）或 ``disclose``（层进 unbound，partial 导出）。
    """
    from app.services.raster_authoring.service import (
        current_revision,
        resolve_committed_expression,
    )
    from app.services.raster_authoring.source import resolve_source

    if on_unresolvable not in ("raise", "disclose"):
        raise ValueError("on_unresolvable must be 'raise' or 'disclose'")

    sources = document.get("sources")
    sources = sources if isinstance(sources, dict) else {}
    bindings: Dict[str, Dict[str, Any]] = {}
    unbound: List[Dict[str, Any]] = []

    seen_refs: Dict[str, Any] = {}
    for layer in document.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("id") or "")
        if not isinstance(layer.get("raster_style"), dict):
            continue  # 矢量层 / 非数值栅格层 —— 编译面既有语义
        try:
            committed = await resolve_committed_expression(
                session_id, lid, mapspec=document, mapspec_revision=revision,
            )
        except RasterExpressionError as exc:
            if on_unresolvable == "disclose":
                unbound.append({"layer_id": lid, "code": exc.code,
                                "message": exc.message})
                continue
            raise
        ref_id = committed.ref_id
        if ref_id in seen_refs:
            src = seen_refs[ref_id]
        else:
            try:
                src = await resolve_source(
                    session_id, ref_id, owner_token,
                    trusted_dispatch=trusted_dispatch,
                )
            except RasterExpressionError as exc:
                if on_unresolvable == "disclose":
                    unbound.append({"layer_id": lid, "code": exc.code,
                                    "message": exc.message})
                    continue
                raise
            seen_refs[ref_id] = src
        if src.data_revision != committed.spec.data_revision:
            # 与 export_snapshot 同判：数据修订漂移 → 拒绝导出，不混版本。
            if on_unresolvable == "disclose":
                unbound.append({"layer_id": lid, "code": "STALE_BINDING",
                                "message": "raster source revision drifted from the committed expression"})
                continue
            raise RasterExpressionError(
                "STALE_BINDING",
                "the raster source data revision no longer matches the committed "
                "expression binding",
                status_code=409,
                correction_hint="Re-commit the raster expression against the current data revision.",
            )
        try:
            stat = os.stat(src.path)
            stat_key = (stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            if on_unresolvable == "disclose":
                unbound.append({"layer_id": lid, "code": "SOURCE_UNAVAILABLE",
                                "message": "frozen raster file is unreadable"})
                continue
            raise RasterExpressionError(
                "SOURCE_UNAVAILABLE", "frozen raster file is no longer readable",
                status_code=404,
            ) from exc
        source_entry = sources.get(committed.source_id)
        declared_bounds = None
        if isinstance(source_entry, dict) and isinstance(
                source_entry.get("bounds"), list) and len(source_entry["bounds"]) == 4:
            try:
                declared_bounds = [float(v) for v in source_entry["bounds"]]
            except (TypeError, ValueError):
                declared_bounds = None
        bindings[lid] = _binding_payload(
            lid, committed.source_id, ref_id, src.path, stat_key,
            committed.spec.model_dump(mode="json"), committed.spec.style_id,
            committed.spec.data_revision, src.crs, declared_bounds,
        )

    # 复检：绑定解析（含 IO）期间修订移动 → 拒绝，不拼两个修订。
    if await current_revision(session_id) != revision:
        raise RasterExpressionError(
            "CAS_SUPERSEDED",
            "MapSpec revision changed while the publication bindings were being frozen",
            status_code=409,
            correction_hint="Re-export the current committed state.",
        )
    return {"revision": revision, "bindings": bindings, "unbound": unbound}


__all__ = ["freeze_raster_bindings"]
