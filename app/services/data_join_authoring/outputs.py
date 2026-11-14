"""G38 输出装配与发布 —— 完整派生载体（单一可写副本）+ 权威收据。

布局（G18 data-delivery 同款纪律；任务独占目录）::

    DATA_DIR/data-join/.staging/<run_id>/output.geojson|.rows.json
    DATA_DIR/data-join/.staging/<run_id>/manifest.json / receipt.json
    DATA_DIR/data-join/<run_id>/…                # 校验通过后同卷原子更名

发布链（WP08）：装配 → staging 写入 → 回读 oracle（行数 + 逐成员 sha256）
→ 原子更名 → ``publish_data_object``（CAS DataObject，全仓单一内容后端）
→ ``register_artifact``（session 台账 ``ref:data-join/<run_id>``）→ 收据可
回读才允许 run 行落 completed。「内容已发布、run 行未落终态」的窗口由
``recovery.py`` 收敛（绝不假 completed）。

载体选择（运行时探测，诚实披露 —— pyarrow 本环境不可用）：
- 富集图层 → ``output.geojson``（FeatureCollection；类型/空值保真；CRS 成员
  在 ``crs`` 字段声明）；
- 纯表 → ``output.rows.json``（行 dict 数组 —— JSON 保真保留 null/类型，
  不用 CSV 当权威载体：CSV 无法区分 null 与空串，违反 J05 政策）。
pyarrow 可用时附加 GeoParquet（``vector_carrier`` 既有能力）作为额外
只读投影 —— 权威副本仍是上述单一可写文件。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_join_authoring import contracts
from app.services.data_join_authoring.errors import DataJoinError

logger = logging.getLogger(__name__)

_DIRNAME = "data-join"
_STAGING = ".staging"

#: 渲染投影（FC session ref）的会话前缀。
RENDER_REF_PREFIX = "data-join-render"


def join_root() -> Path:
    from app.core.config import settings

    root = Path(settings.DATA_DIR) / _DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def staging_dir(run_id: str) -> Path:
    d = join_root() / _STAGING / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_dir(run_id: str) -> Path:
    return join_root() / run_id


def new_run_id() -> str:
    return "dj-" + secrets.token_hex(12)


def discard_staging(run_id: str) -> None:
    shutil.rmtree(join_root() / _STAGING / run_id, ignore_errors=True)


def discard_final(run_id: str) -> None:
    """仅取消收敛/恢复补偿路径调用（不碰源数据/共享 blob）。"""
    shutil.rmtree(final_dir(run_id), ignore_errors=True)


# ───────────────────────── 装配 ─────────────────────────

_INTERNAL_GEOM_KEY = "__geometry__"


def assemble_feature_collection(
    outputs: List[Dict[str, Any]], crs: Optional[str], name: str
) -> Dict[str, Any]:
    """执行行 → GeoJSON FeatureCollection（左几何保留；行序确定性）。"""
    features = []
    for row in outputs:
        geom = row.pop(_INTERNAL_GEOM_KEY, None)
        props = {k: v for k, v in row.items()}
        feature: Dict[str, Any] = {"type": "Feature", "properties": props}
        if geom is not None:
            feature["geometry"] = geom
        else:
            feature["geometry"] = None
        features.append(feature)
    fc: Dict[str, Any] = {
        "type": "FeatureCollection",
        "name": name,
        "features": features,
    }
    if crs:
        fc["crs"] = crs
    return fc


def assemble_rows(outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """执行行 → 纯表行集（JSON 类型保真；无伪造 geometry）。"""
    rows = []
    for row in outputs:
        row.pop(_INTERNAL_GEOM_KEY, None)
        rows.append(row)
    return {"rows": rows, "row_count": len(rows)}


# ───────────────────────── 校验 + 原子发布 ─────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_staging(
    run_id: str,
    *,
    output_kind: str,
    fc: Optional[Dict[str, Any]],
    rows: Optional[Dict[str, Any]],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """staging 写入 + 回读 oracle。返回成员摘要（manifest 补全后落盘）。"""
    d = staging_dir(run_id)
    members: Dict[str, str] = {}
    if output_kind == "enriched_layer":
        assert fc is not None
        p = d / "output.geojson"
        p.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        members["output.geojson"] = _sha256_file(p)
        reread = json.loads(p.read_text(encoding="utf-8"))
        if len(reread.get("features") or []) != len(fc["features"]):
            raise DataJoinError(
                "internal_error", "GeoJSON 回读 oracle 行数不一致（staging 未发布）")
    else:
        assert rows is not None
        p = d / "output.rows.json"
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        members["output.rows.json"] = _sha256_file(p)
        reread = json.loads(p.read_text(encoding="utf-8"))
        if len(reread.get("rows") or []) != len(rows["rows"]):
            raise DataJoinError(
                "internal_error", "rows 回读 oracle 行数不一致（staging 未发布）")
    manifest = dict(manifest)
    manifest["members"] = members
    manifest["output_kind"] = output_kind
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def atomic_publish(run_id: str) -> Path:
    """staging → 终态目录同卷原子更名（已存在 → typed 失败）。"""
    src = join_root() / _STAGING / run_id
    dst = final_dir(run_id)
    if not src.is_dir():
        raise FileNotFoundError(f"staging missing for run {run_id}")
    if dst.exists():
        raise DataJoinError("internal_error", f"run {run_id} 终态目录已存在")
    os.replace(src, dst)
    return dst


def write_receipt(run_id: str, receipt: Dict[str, Any]) -> Path:
    p = final_dir(run_id) / "receipt.json"
    p.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def read_receipt(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(
            (final_dir(run_id) / "receipt.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 不可读按无收据处理（诚实）
        return None


def read_manifest(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(
            (final_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def verify_published(run_id: str) -> Dict[str, Any]:
    """终态包核验：成员存在 + sha256 与 manifest 一致 + 可解析。

    返回成员核验摘要；任一失败 → typed 失效（复用前检查 / 恢复依据）。
    """
    d = final_dir(run_id)
    manifest = read_manifest(run_id)
    if manifest is None or not isinstance(manifest.get("members"), dict):
        raise DataJoinError(
            "receipt_missing", f"run {run_id} 缺 manifest 或成员表", run_id=run_id)
    for name, expect_sha in manifest["members"].items():
        p = d / name
        if not p.is_file():
            raise DataJoinError(
                "receipt_missing", f"run {run_id} 成员 {name} 缺失",
                run_id=run_id, member=name)
        if _sha256_file(p) != expect_sha:
            raise DataJoinError(
                "receipt_missing", f"run {run_id} 成员 {name} sha256 不符",
                run_id=run_id, member=name)
    return {"members": manifest["members"], "output_kind": manifest.get("output_kind")}


def publish_data_object_for_run(
    run_id: str,
    *,
    owner_scope: Dict[str, str],
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """终态包 → durable DataObject（CAS；单一内容后端 ``publish_data_object``）。

    返回三态可区分结果（published/oversized/failed —— 诚实降级，本体不受影响）。
    """
    from app.services.lakehouse.data_object import (
        DataObjectTooLargeError,
        publish_data_object,
    )

    d = final_dir(run_id)
    files = {
        p.name: p for p in sorted(d.iterdir())
        if p.is_file() and p.name not in ("manifest.json", "receipt.json")
    }
    if not files:
        return {"state": "failed", "error": "no output files"}
    try:
        identity = publish_data_object(
            files,
            kind="data_join",
            owner_scope=owner_scope,
            payload={"run_id": run_id},
            producer={"tool": "data_join_authoring", "run_id": run_id},
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
        )
        return {
            "state": "published",
            "data_object_id": identity.data_object_id,
            "manifest_location": identity.manifest_location,
            "content_sha256": identity.content_sha256,
            "byte_size": identity.byte_size,
            "deduped": identity.deduped,
        }
    except DataObjectTooLargeError:
        return {"state": "oversized"}
    except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
        logger.warning("[data-join] data object publish failed for %s: %s",
                       run_id, e)
        return {"state": "failed", "error": str(e)[:200]}


async def register_session_artifact(
    session_id: str, run_id: str, descriptor: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Optional[str]:
    """session 台账登记（ref:data-join/<run_id>；增值记录，绝不阻断本体）。"""
    try:
        from app.services.artifact_registry import register_artifact

        ref = f"ref:data-join/{run_id}"
        await register_artifact(
            session_id,
            artifact_id=ref,
            artifact_type="data_join",
            producer_capability="data_join_authoring",
            producer_tool="data_join_execute",
            descriptor=descriptor,
            metadata=metadata,
        )
        return ref
    except Exception as e:  # noqa: BLE001
        logger.warning("[data-join] ledger registration skipped for %s: %s",
                       run_id, e)
        return None


# ───────────────────────── 结果分页（读权威文件） ─────────────────────────

_page_cache: Dict[str, Tuple[int, Dict[str, Any]]] = {}


def load_output_content(run_id: str) -> Dict[str, Any]:
    """读终态包输出（LRU=2 缓存 keyed by (run_id, mtime)）。"""
    d = final_dir(run_id)
    cached = _page_cache.get(run_id)
    if cached is not None:
        mtime, payload = cached
        try:
            if (d / "manifest.json").stat().st_mtime == mtime:
                return payload
        except OSError:
            pass
    if (d / "output.geojson").is_file():
        payload: Dict[str, Any] = {"kind": "enriched_layer",
                                   "fc": json.loads(
                                       (d / "output.geojson").read_text(
                                           encoding="utf-8"))}
    elif (d / "output.rows.json").is_file():
        payload = {"kind": "table",
                   "rows_doc": json.loads(
                       (d / "output.rows.json").read_text(encoding="utf-8"))}
    else:
        raise DataJoinError(
            "result_unavailable", f"run {run_id} 无可读输出成员", run_id=run_id)
    try:
        mtime = (d / "manifest.json").stat().st_mtime
        if len(_page_cache) >= 2:
            _page_cache.clear()
        _page_cache[run_id] = (mtime, payload)
    except OSError:
        pass
    return payload


def result_page(run_id: str, cursor: int, limit: int) -> Dict[str, Any]:
    """权威文件上的分页读（行序 = 冻结快照输出序）。"""
    limit = max(1, min(int(limit), contracts.RESULT_PAGE_MAX_ROWS))
    cursor = max(0, int(cursor))
    content = load_output_content(run_id)
    if content["kind"] == "enriched_layer":
        feats = content["fc"].get("features") or []
        total = len(feats)
        window = feats[cursor:cursor + limit]
        rows = [
            {"properties": f.get("properties") or {},
             "geometry_summary": _geom_summary(f.get("geometry"))}
            for f in window
        ]
        crs = content["fc"].get("crs")
    else:
        all_rows = content["rows_doc"].get("rows") or []
        total = len(all_rows)
        window = all_rows[cursor:cursor + limit]
        rows = [{"properties": r, "geometry_summary": None} for r in window]
        crs = None
    next_cursor = cursor + len(window)
    return {
        "run_id": run_id,
        "rows": rows,
        "total_rows": total,
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < total else None,
        "page_size": limit,
        "crs": crs,
        "output_kind": content["kind"],
    }


def _geom_summary(geom: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not geom:
        return None
    return {"type": geom.get("type")}


# ───────────────────────── 渲染投影（上图用有界 FC ref） ─────────────────────────

async def mint_render_ref(session_id: str, run_id: str) -> Dict[str, Any]:
    """≤RENDER_REF_MAX_ROWS 时铸 session FC ref（渲染投影，非权威副本）。"""
    content = load_output_content(run_id)
    if content["kind"] != "enriched_layer":
        raise DataJoinError(
            "unsupported_feature", "纯表输出不提供地图渲染投影（走导出交付）")
    fc = content["fc"]
    n = len(fc.get("features") or [])
    if n > contracts.RENDER_REF_MAX_ROWS:
        raise DataJoinError(
            "render_ref_over_budget",
            f"输出 {n} 行超过渲染投影上限 {contracts.RENDER_REF_MAX_ROWS}；"
            "完整数据经导出交付（G18）获取",
            rows=n, cap=contracts.RENDER_REF_MAX_ROWS,
        )
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(
        session_id, fc, prefix=RENDER_REF_PREFIX)
    return {"render_ref": ref, "rows": n}
