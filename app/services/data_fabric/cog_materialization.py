"""STAC → COG 远程资产受控物化（GOAL-02 / M4）。

链路（每一步可解释、可取消、可幂等）：

- **discover**：复用 ``STACAdapter.search``（bbox/datetime/CQL2 门控全在），
  枚举 item assets 并标注 COG 候选（媒体类型声明或 href 后缀，诚实标注
  ``declared``/`inferred`）；
- **preview/validate**：``validate_remote_href`` SSRF 门（geo_raster 唯一
  入口，绝不另写一套）+ rasterio 头读（bounds/crs/分辨率/波段/nodata）+
  ``cog_structure_ok`` advisory（tiled+overviews，非 COG 也能物化但如实
  标注 range-read 代价）；
- **bounded materialize**：窗口读（AOI 经 transform_bounds 到栅格 CRS；
  无 AOI 时整幅但先过像元/字节预算门）+ ``RemoteReadSession``（请求/字节
  双预算 + 瞬态重试 + 断路器 + 取消协作）——**绝不整幅 GET**；
- **publish**：本地 COG → ``raster_materialization.publish_cog_artifact``
  （``ref:fabric-cog/`` + sha256 指纹 + CAS DataObject + 台账 + provenance）；
- **幂等**：同 (href, params) 内容一致 → CAS 同 data_object_id（内容寻址
  天然去重）；远端变化 → 新 sha256 = 新身份（旧 ref 不被冒用）；
- **脱敏**：签名 URL（含 token/SAS query）经 ``redact_url`` 后才进
  provenance/回执；密钥不落盘。

reason code 封闭：``budget_exceeded`` / ``ssrf_blocked`` / ``unreachable`` /
``not_a_raster`` / ``cancelled``。
"""
from __future__ import annotations

from app.lib.geo_raster.env import open_raster

import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from app.services.data_fabric.security import DataFabricSecurity  # noqa: E402

RC_BUDGET_EXCEEDED = "budget_exceeded"
RC_SSRF_BLOCKED = "ssrf_blocked"
RC_UNREACHABLE = "unreachable"
RC_NOT_A_RASTER = "not_a_raster"
RC_CANCELLED = "cancelled"

#: 默认物化预算（窗口读输出 + 单请求会话上限；低于 geo_raster 全局红线）。
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_REQUESTS = 600

_COG_MEDIA_HINTS = (
    "image/tiff; application=geotiff; profile=cloud-optimized",
    "image/tiff",
)
_COG_SUFFIXES = (".tif", ".tiff")


#: 签名 URL 敏感参数键（封闭词表 + 子串匹配兜底；命中即整段 query 脱敏）。
_SENSITIVE_QUERY_TOKENS = (
    "token", "sig", "secret", "credential", "api_key", "apikey",
    "password", "x-amz-", "x-goog-", "x-ms-", "sas",
)


def redact_signed_url(url: str) -> str:
    """远程资产 href 的出仓形态：userinfo（redact_url）+ 敏感 query 整段脱敏。

    ``DataFabricSecurity.redact_url`` 只剥 userinfo —— 签名 URL 的凭证在
    query（``?sig=…``/``?X-Goog-Credential=…``）。这里在 userinfo 剥离后
    检查 query 参数键：命中敏感词表任一（含子串）→ query 整段替换为
    ``?<redacted>``（保守：不逐参数挑选，避免漏掉配对参数）。无 query
    或无敏感键 → 原样（非敏感参数如 tile 坐标保留可诊断性）。
    """
    from urllib.parse import parse_qsl, urlparse, urlunparse

    base = DataFabricSecurity.redact_url(url) or url
    try:
        parsed = urlparse(base)
    except Exception:  # noqa: BLE001
        return base
    if not parsed.query:
        return base
    try:
        keys = [k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    except Exception:  # noqa: BLE001
        return urlunparse(parsed._replace(query="<redacted>"))
    if any(tok in k for k in keys for tok in _SENSITIVE_QUERY_TOKENS):
        return urlunparse(parsed._replace(query="<redacted>"))
    return base


from app.services.data_fabric.errors import DataFabricError


class CogMaterializationError(DataFabricError):
    """物化失败（reason code 封闭；details 携带预算/协商事实）。"""

    code = "MATERIALIZATION_FAILED"

    def __init__(self, message: str, *, reason_code: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


# ── discover：STAC item assets → COG 候选 ─────────────────────────────────

def extract_cog_assets(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """STAC item → COG 候选 asset 摘要（标注 declared/inferred，不下载）。"""
    assets = item.get("assets") or {}
    out: List[Dict[str, Any]] = []
    if not isinstance(assets, dict):
        return out
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        href = asset.get("href")
        if not isinstance(href, str) or not href:
            continue
        media = str(asset.get("type") or "")
        roles = [str(r) for r in (asset.get("roles") or [])]
        declared = "cloud-optimized" in media.lower()
        inferred = (
            not declared
            and media.lower() in {m.lower() for m in _COG_MEDIA_HINTS}
        ) or (
            not media
            and href.lower().split("?")[0].endswith(_COG_SUFFIXES)
        )
        if declared or inferred:
            out.append({
                "asset_key": key,
                "href": href,
                "media_type": media or None,
                "roles": roles,
                "match": "declared" if declared else "inferred",
                "title": asset.get("title") or key,
            })
    return out


async def discover_stac_cog_assets(
    stac_adapter: Any,
    *,
    collection_id: str,
    bbox: Optional[Sequence[float]] = None,
    datetime_range: Optional[Sequence[str]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """STAC 搜索 → item 列表 + 每 item 的 COG 候选（零下载）。

    ``datetime_range``：``[start, end]``（ISO8601；QuerySpec 既有语义，
    normalize → temporal 下推）。
    """
    from app.schemas.data_fabric_schema import QuerySpec

    spec = QuerySpec(
        limit=limit,
        bbox=list(bbox) if bbox else None,
        datetime_range=list(datetime_range) if datetime_range else None,
    )
    if not collection_id or collection_id == "_search":
        raise ValueError("COG discovery requires an explicit real STAC collection_id")
    result = await asyncio.to_thread(stac_adapter.query, collection_id, spec)
    items = result.features or []
    enriched: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        enriched.append({
            "item_id": item.get("id"),
            "datetime": (item.get("properties") or {}).get("datetime"),
            "bbox": item.get("bbox"),
            "assets": extract_cog_assets(item),
        })
    return {
        "items": enriched,
        "total_items": len(enriched),
        "query_metadata": result.metadata or {},
    }


# ── preview / validate：头读 + 结构 advisory（不搬像元） ──────────────────

def _remote_vsi_href(href: str) -> str:
    """http(s) → /vsicurl 形（GDAL 显式远端语义；本地路径原样）。"""
    if href.startswith(("http://", "https://")):
        return f"/vsicurl/{href}"
    return href


def preview_remote_cog(href: str) -> Dict[str, Any]:
    """远程 COG 头读（SSRF 门 + 结构 advisory；不搬像元）。"""
    from app.lib.geo_raster.env import rasterio_env


    try:
        vsi = _remote_vsi_href(href)
        # 模块绑定：validate_remote_href(vsi)（reader 同款入口；测试可注入）
        from app.lib.geo_raster import env as geo_env

        geo_env.validate_remote_href(vsi)
    except ValueError as e:  # DataFabricSecurityError 是 ValueError 子类
        raise CogMaterializationError(
            f"remote raster href blocked by SSRF policy: {e}",
            reason_code=RC_SSRF_BLOCKED,
            details={"href": redact_signed_url(href)},
        ) from e
    try:
        import rasterio
        from contextlib import ExitStack

        with ExitStack() as _stack:
            _stack.enter_context(rasterio_env())
            # PAM 边车（.aux.xml/.aux）对只读远程栅格无意义且每次探测
            # 一次网络往返 —— GDAL 官方建议 /vsicurl 关闭（读取语义不变）。
            _stack.enter_context(rasterio.Env(GDAL_PAM_ENABLED="NO"))
            with open_raster(vsi) as ds:
                info = {
                    "width": ds.width,
                    "height": ds.height,
                    "count": ds.count,
                    "dtypes": list(ds.dtypes),
                    "crs": ds.crs.to_string() if ds.crs else None,
                    "bounds": list(ds.bounds),
                    "res": list(ds.res),
                    "nodata": ds.nodata,
                    "descriptions": list(ds.descriptions),
                    "overviews": list(ds.overviews(1)),
                    "tiled": ds.profile.get("tiled", False),
                }
        info["cog_structure_ok"] = bool(info["tiled"] and info["overviews"])
        info["structure_note"] = (
            "cloud-optimized (tiled + overviews)"
            if info["cog_structure_ok"]
            else "not COG-structured (tiled/overviews missing) — range-read "
            "cost may be high; materialization still possible within budget"
        )
        info["href_redacted"] = redact_signed_url(href)
        return info
    except CogMaterializationError:
        raise
    except Exception as e:  # noqa: BLE001
        raise CogMaterializationError(
            f"remote raster open failed: {e}",
            reason_code=RC_NOT_A_RASTER,
            details={"href": redact_signed_url(href)},
        ) from e


# ── bounded materialize ────────────────────────────────────────────────────

async def materialize_remote_cog_asset(
    href: str,
    *,
    session_id: str,
    bands: Optional[Sequence[int]] = None,
    bbox: Optional[Sequence[float]] = None,
    overview_level: int = 0,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    title: Optional[str] = None,
    cancel_token: Any = None,
) -> Dict[str, Any]:
    """远程 COG → 窗口读（预算/重试/取消）→ 本地 COG ref。

    ``bbox``：EPSG:4326（agent 面）；内部 transform_bounds 到栅格 CRS。
    ``overview_level``：0=全分辨率；N=全分辨率窗口读后按 2^N nearest
    抽样（**不**读取服务端 overview 层 —— wire 成本仍按全窗发生，预算门
    同口径估算；标注 decimated=True）。
    """
    from app.lib.geo_raster.remote import RemoteReadBudgetExceeded
    from rasterio.windows import from_bounds

    # 1) SSRF 门（与 preview 同一入口）

    try:
        vsi = _remote_vsi_href(href)
        from app.lib.geo_raster import env as geo_env

        geo_env.validate_remote_href(vsi)
    except ValueError as e:
        raise CogMaterializationError(
            f"remote raster href blocked by SSRF policy: {e}",
            reason_code=RC_SSRF_BLOCKED,
            details={"href": redact_signed_url(href)},
        ) from e

    def _read_and_write(staging: Path) -> Dict[str, Any]:
        """同步半边：rasterio_env 内开远端、算窗口/预算、窗口读、写 GTiff。"""
        from app.lib.geo_raster.env import rasterio_env
        from app.lib.geo_raster.remote import RemoteReadPolicy, RemoteReadSession

        policy = RemoteReadPolicy(max_requests=max_requests)
        session = RemoteReadSession(policy=policy)
        from contextlib import ExitStack

        with ExitStack() as _stack:
            _stack.enter_context(rasterio_env())
            import rasterio

            # 同 preview：只读远程栅格关闭 PAM 边车探测。
            _stack.enter_context(rasterio.Env(GDAL_PAM_ENABLED="NO"))
            with open_raster(vsi) as ds:
                band_idx = list(bands) if bands else list(range(1, ds.count + 1))
                for b in band_idx:
                    if not (1 <= int(b) <= ds.count):
                        raise CogMaterializationError(
                            f"band {b} out of range (1..{ds.count})",
                            reason_code=RC_NOT_A_RASTER,
                        )
                window = None
                if bbox is not None:
                    # bbox(4326) → 栅格 CRS（transform_bounds；不猜轴序）。
                    if ds.crs is None:
                        raise CogMaterializationError(
                            "remote raster declares no CRS — bbox subsetting "
                            "refused (no basis for reprojection)",
                            reason_code=RC_NOT_A_RASTER,
                        )
                    from rasterio.warp import transform_bounds

                    b4326 = transform_bounds("EPSG:4326", ds.crs, *bbox, densify_pts=21)
                    window = from_bounds(*b4326, transform=ds.transform)
                    window = window.round_lengths(op="floor").round_offsets(op="floor")
                    w = max(1, min(ds.width, int(window.width)))
                    h = max(1, min(ds.height, int(window.height)))
                    col = max(0, min(ds.width - w, int(window.col_off)))
                    row = max(0, min(ds.height - h, int(window.row_off)))
                else:
                    col, row, w, h = 0, 0, ds.width, ds.height

                scale = 1 << max(0, int(overview_level))
                out_w = max(1, w // scale)
                out_h = max(1, h // scale)
                import numpy as np

                itemsize = max(
                    np.dtype(t).itemsize
                    for t in (ds.dtypes[int(b) - 1] for b in band_idx)
                )
                # R1 P1-8：预算按 **wire 读尺寸**（w×h 全分辨率窗口）估算 ——
                # decimation 在读后发生，网络搬运的是全窗像元；输出尺寸仅作
                # 事实披露。
                est_bytes = w * h * len(band_idx) * itemsize
                est_out_bytes = out_w * out_h * len(band_idx) * itemsize
                if est_bytes > max_output_bytes:
                    raise CogMaterializationError(
                        f"window read would produce ~{est_bytes / 1e6:.0f} MB "
                        f"(budget {max_output_bytes / 1e6:.0f} MB) — narrow the "
                        "bbox, select fewer bands, or raise the budget explicitly",
                        reason_code=RC_BUDGET_EXCEEDED,
                        details={
                            "estimated_wire_bytes": int(est_bytes),
                            "estimated_output_bytes": int(est_out_bytes),
                            "max_output_bytes": int(max_output_bytes),
                            "window": [col, row, w, h],
                            "out_shape": [out_h, out_w],
                            "bands": band_idx,
                            "overview_level": overview_level,
                            "full_raster": [ds.width, ds.height],
                        },
                    )

                from rasterio.windows import Window

                win = Window(col, row, w, h)
                if cancel_token is not None and cancel_token.cancelled:
                    from app.lib.cancellation import OperationCancelled

                    raise OperationCancelled("cancelled before window read")
                # 预算化窗口读：out_shape 抽样时按输出数组记账（诚实下界）。
                from app.lib.geo_raster.remote import remote_read_window

                if scale == 1:
                    arr = remote_read_window(
                        session, vsi, ds, band_idx, win, cancel_token=cancel_token,
                    )
                else:
                    arr = remote_read_window(
                        session, vsi, ds, band_idx, win, cancel_token=cancel_token,
                    )
                    # 读后 nearest 抽样（remote_read_window 已按全窗记账，
                    # 此处不再重复累加 —— R1 P1-8 双计清除）。注意：decimation
                    # 发生在网络读之后，wire 成本按全分辨率窗口发生。
                    arr = arr[:, ::scale, ::scale][:, :out_h, :out_w]

                transform = ds.window_transform(win)
                # 抽样后像元分辨率同步放大（transform 缩放）。
                if scale > 1:
                    from rasterio import Affine

                    transform = (
                        Affine.translation(transform.c, transform.f)
                        * Affine.scale(
                            transform.a * (w / arr.shape[2]),
                            transform.e * (h / arr.shape[1]),
                        )
                    )
                crs = ds.crs.to_string() if ds.crs else None
                nodata = ds.nodata

                # 写 staging GTiff（write_cog 随后转 COG）。
                out = staging / "window_raw.tif"
                profile = {
                    "driver": "GTiff",
                    "width": arr.shape[2],
                    "height": arr.shape[1],
                    "count": arr.shape[0],
                    "dtype": str(arr.dtype),
                    "transform": transform,
                    "crs": crs,
                    "nodata": nodata,
                    "tiled": True,
                }
                with rasterio.open(out, "w", **profile) as dst:
                    dst.write(arr.astype(arr.dtype))
                return {
                    "grid_shape": list(arr.shape),
                    "window": [col, row, w, h],
                    "crs": crs,
                    "nodata": nodata,
                    "band_indices": band_idx,
                    "requests": session.requests,
                    "retries": session.retries,
                    "bytes_read": int(session.bytes_read),
                    "overview_level": overview_level,
                    "decimated": scale > 1,
                    "full_raster": [ds.width, ds.height],
                }

    with tempfile.TemporaryDirectory(prefix="cog-mat-") as td:
        staging = Path(td)
        try:
            ledger = await asyncio.to_thread(_read_and_write, staging)
        except CogMaterializationError:
            raise
        except RemoteReadBudgetExceeded as e:
            # R1 P1-2：读中预算耗尽是预算事实，不是网络不可达。
            raise CogMaterializationError(
                f"remote read budget exceeded during window read: {e}",
                reason_code=RC_BUDGET_EXCEEDED,
            ) from e
        except Exception as e:  # noqa: BLE001
            text = str(e)
            if "cancelled" in text.lower():
                raise CogMaterializationError(
                    f"materialization cancelled: {e}",
                    reason_code=RC_CANCELLED,
                ) from e
            raise CogMaterializationError(
                f"remote window read failed: {e}",
                reason_code=RC_UNREACHABLE,
                details={"href": redact_signed_url(href)},
            ) from e

        from app.lib.geo_raster.cog import write_cog

        cog_path = staging / "asset.cog.tif"
        await asyncio.to_thread(write_cog, str(staging / "window_raw.tif"), str(cog_path))

        params_fingerprint = _params_fingerprint(href, bands, bbox, overview_level)
        provenance = {
            "source_url": redact_signed_url(href),
            "source_type": "remote_cog_asset",
            "params_fingerprint": params_fingerprint,
            "ledger": ledger,
        }
        from app.services.data_fabric.raster_materialization import publish_cog_artifact

        result = await publish_cog_artifact(
            session_id=session_id,
            source_path=cog_path,
            title=title or f"cog:{Path(href.split('?')[0]).name}",
            producer_tool="materialize_remote_cog_asset",
            provenance=provenance,
            redacted_source_url=provenance["source_url"],
            cancel_token=cancel_token,
        )
    result.update({
        "params_fingerprint": params_fingerprint,
        "ledger": ledger,
        "bands": ledger["band_indices"],
        "crs": ledger["crs"],
        "nodata": ledger["nodata"],
    })
    return result


def _params_fingerprint(
    href: str,
    bands: Optional[Sequence[int]],
    bbox: Optional[Sequence[float]],
    overview_level: int,
) -> str:
    """(href+params) 规范指纹 —— 同参重试幂等键（内容身份另由 sha256 担当）。"""
    payload = {
        "href": href,
        "bands": list(bands) if bands else None,
        "bbox": list(bbox) if bbox else None,
        "overview_level": int(overview_level),
    }
    import json as _json

    canon = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "cogparams-sha256:" + hashlib.sha256(canon.encode()).hexdigest()[:32]


__all__ = [
    "CogMaterializationError",
    "discover_stac_cog_assets",
    "extract_cog_assets",
    "materialize_remote_cog_asset",
    "preview_remote_cog",
    "RC_BUDGET_EXCEEDED",
    "RC_CANCELLED",
    "RC_NOT_A_RASTER",
    "RC_SSRF_BLOCKED",
    "RC_UNREACHABLE",
]
