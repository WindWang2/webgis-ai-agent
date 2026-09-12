"""用户数据上传 API 路由"""
import asyncio
import hashlib
import logging
import shutil
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

import ijson
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from sqlalchemy import select, func, update as sa_update
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.upload_schema import (  # noqa: F401 - ErrorResponse 兼容保留
    ErrorResponse,
    UploadDeleteResponse,
    UploadListResponse,
    UploadResponse,
)
from app.core.config import settings
from app.core.auth import authorize_session_write, get_current_user, verify_session_owner
from app.lib.geojson_serializer import serialize_geojson
from app.tools._utils import async_db_session
from app.models.upload import UploadRecord
from app.services.data_ingest.pipeline import get_ingest_pipeline
from app.services.data_parser import (
    MAX_RASTER_SIZE,
    MAX_VECTOR_SIZE,
    ParseError,
    RASTER_FORMATS,
    VECTOR_FORMATS,
    get_upload_dir,
    parse_raster,
    parse_vector,
    save_meta,
)
from app.services.data_profile.profiler import get_dataset_profiler

logger = logging.getLogger(__name__)
router = APIRouter()


def _load_geojson_features(path: Path) -> list:
    """流式解析 GeoJSON 的 features —— 同步 CPU+IO 密集（50MB 上限），
    必须在 worker 线程执行（计算隔离不变式 1）。"""
    with open(path, "rb") as f:
        return list(ijson.items(f, "features.item"))


def _write_upload_bytes(path: Path, content: bytes) -> str:
    """同步写盘（栅格上限 200MB）+ 内容指纹 —— 必须在 worker 线程执行
    （#592：与紧随其后的 parse 走 run_in_executor 同款纪律；慢盘/NFS 上内联写
    会冻结事件循环上全部并发 SSE/WS 流）。

    V4 内容身份（审计 03 R2）：sha256 在同一个 worker 线程里随写盘一并计算
    （流式分块，不额外复制 200MB 缓冲），返回 hex 摘要作为幂等再导入键。
    """
    sha = hashlib.sha256()
    with open(path, "wb") as f:
        # 分块写 + 喂哈希：避免 content 是 mmap/延迟加载时的二次整块拷贝
        view = memoryview(content)
        for i in range(0, len(view), 1024 * 1024):
            chunk = view[i:i + 1024 * 1024]
            sha.update(chunk)
            f.write(chunk)
    return sha.hexdigest()


class _UploadDeduplicated(Exception):
    """同会话同内容命中幂等导入 —— 携带既有记录的有界快照（字段 dict；
    异常穿出 DB 会话上下文时实例会被 rollback 置为 detached/expired，
    不能携带 ORM 实例），由外层统一清理新目录后返回。"""

    def __init__(self, fields: Dict[str, Any]):
        self.fields = fields
        super().__init__(f"deduplicated upload id={fields.get('id')}")


async def _verify_session_owner(
    db, session_id: Optional[str], user_id, owner_token: Optional[str] = None
) -> None:
    """跨租户守卫：若 upload 关联了 session_id，会话必须属于调用方（审计 S42）。

    UploadRecord 无 user_id 列；通过 session_id → Conversation.user_id 解析归属。
    session_id 为 None 时（旧匿名上传）拒绝 —— #1109 起 legacy grandfather
    （知道 session_id 即放行）已移除，与历史语义相反（fail-closed）。
    SEC-08：匿名会话需转发 X-Session-Token（owner_token），与 POST /upload 对齐。
    """
    if not session_id:
        # Session-less records used to skip this check, so GET/DELETE /uploads/{n}
        # was world-reachable for any sequential integer id.
        raise HTTPException(status_code=404, detail="上传记录不存在")
    await verify_session_owner(
        db, session_id, user_id=user_id, owner_token=owner_token
    )


# ==================== 上传接口 ====================

@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: List[UploadFile] = File(..., description="GIS 数据文件（支持多文件上传）"),
    session_id: Optional[str] = Form(None, description="关联的会话 ID"),
    crs: Optional[str] = Form(None, description="声明坐标系（如 EPSG:4326）。CSV 无 CRS 元数据：声明则可信转换，缺省则如实按 unknown 披露"),
    dedup: bool = Form(True, description="同会话同内容（sha256）幂等复用既有上传记录"),
    register_ref: bool = Form(False, description="注册到会话产物台账（V3 摄入管线增值车道，仅矢量；失败不阻断上传）"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id", description="存在即视为请求 register_ref（值不作为会话来源——归属一律以已校验的 session_id 表单为准）"),
    _user: dict = Depends(get_current_user),
):
    """
    上传 GIS 数据文件

    支持格式:
    - 矢量: .geojson, .json, .shp (zip), .kml, .gpkg, .csv (含经纬度列)
    - 栅格: .tif, .tiff

    限制: 矢量文件 50MB, 栅格文件 200MB
    """
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")

    # #1221（audit3 D-14）：session_id 必填 —— dedup / 列表 / GET / DELETE
    # 的归属语义都依赖它；此前 session_id=None 的上传可以写入，但 #1109
    # 的 fail-closed 读/删守卫让这些记录 API 永远不可见不可删（只能等
    # age sweep 回收磁盘）。前置拒绝，不制造孤儿。
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id is required: uploads are scoped to a chat "
                   "session (ownership, dedup and listing all key on it)",
        )

    # 只处理第一个文件（多文件上传可扩展）。V4：丢弃的文件必须如实披露，
    # 不再静默吞掉（审计 R8 —— 数据丢失形状的事实必须可见）。
    file = files[0]
    ignored_files: List[str] = []
    warnings: List[str] = []
    if len(files) > 1:
        ignored_files = [
            Path(f.filename or "").name or f"<unnamed #{i + 1}>"
            for i, f in enumerate(files[1:], start=1)
        ]
        warnings.append(
            f"仅处理第一个文件，其余 {len(ignored_files)} 个已忽略: "
            f"{', '.join(ignored_files[:8])}"
        )
    # —— 文件名清洗：剥离任何路径分隔符与 .. 防穿越 ——
    raw_name = file.filename or "unknown"
    filename = Path(raw_name).name
    if not filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")
    ext = Path(filename).suffix.lower()

    # 检查格式
    if ext not in VECTOR_FORMATS and ext not in RASTER_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。"
                   f"支持的矢量格式: {', '.join(sorted(VECTOR_FORMATS))}；"
                   f"栅格格式: {', '.join(sorted(RASTER_FORMATS))}",
        )

    # 读取文件内容 — SEC-F6: read at most cap+1 bytes so an oversized body is
    # rejected BEFORE being fully buffered into memory (the old unconditional
    # read() let a direct-exposure attacker OOM the process; nginx's 100M
    # body cap only covers the proxied path).
    _max_for_ext = MAX_RASTER_SIZE if ext in RASTER_FORMATS else MAX_VECTOR_SIZE
    content = await file.read(_max_for_ext + 1)
    file_size = len(content)

    # 检查大小
    if ext in RASTER_FORMATS and file_size > MAX_RASTER_SIZE:
        raise HTTPException(
            status_code=413,
            detail="栅格文件大小超过限制 200MB",
        )
    if ext in VECTOR_FORMATS and file_size > MAX_VECTOR_SIZE:
        raise HTTPException(
            status_code=413,
            detail="矢量文件大小超过限制 50MB",
        )

    # 创建上传目录
    # 完整 uuid hex (32 字符, 128 位熵) — 旧的 [:12] 仅 48 位，公网静态 mount 下可枚举
    upload_id = uuid.uuid4().hex
    upload_dir = get_upload_dir(settings.DATA_DIR, upload_id)

    # #546：单一清理纪律 —— 目录在 get_upload_dir 已被 eager 创建，此后任何
    # 失败分支（临时文件写入 / 解析 / save_meta / DB，含 DBAPIError 逃逸）都
    # 必须把本次上传的目录删掉，否则持久卷积累无法通过 API 删除的孤儿目录
    # （ParseError 等 4xx/5xx + 未捕获的 SQLAlchemy DBAPIError 都会把目录留下）。
    # 只删当前上传的 upload_dir，绝不碰 uploads/ 根目录。
    try:
        # 写入临时文件 — 二次防御：解析后必须仍在 upload_dir 之下
        temp_path = upload_dir / filename
        content_sha256: Optional[str] = None
        try:
            resolved = temp_path.resolve()
            upload_root = Path(upload_dir).resolve()
            if upload_root not in resolved.parents:
                raise HTTPException(status_code=400, detail="路径越界")
            # #592：200MB 同步写盘移出事件循环（与下方 parse 的 executor 对称）。
            # V4：sha256 内容指纹在同一 worker 线程随写盘计算（不阻塞事件循环）。
            content_sha256 = await asyncio.to_thread(_write_upload_bytes, temp_path, content)
        except OSError as e:
            logger.error(f"文件保存失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="文件保存失败")

        # 解析文件 — parse_vector / parse_raster 内含 gpd.read_file / rasterio.open
        # 这些是同步 CPU+IO 操作，常常需要数秒。直接在 async def 里调用会阻塞整个
        # uvicorn 事件循环，所有其它请求停滞（审计 B1 / V2.0 计算隔离不变式）。
        # 走 run_in_executor 把工作扔到默认 threadpool。
        loop = asyncio.get_running_loop()
        try:
            if ext in RASTER_FORMATS:
                meta = await loop.run_in_executor(None, parse_raster, temp_path, upload_dir, upload_id)
            elif crs:
                # 用户声明了 CRS（CSV 场景）→ 传入解析器做可信转换/披露
                meta = await loop.run_in_executor(
                    None,
                    partial(parse_vector, temp_path, upload_dir, upload_id, crs=crs),
                )
            else:
                meta = await loop.run_in_executor(None, parse_vector, temp_path, upload_dir, upload_id)
        except ParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except (OSError, RuntimeError) as e:
            logger.error(f"文件解析异常: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="文件解析失败")

        # 解析期警告（编码回退 / CRS 假设）透传到响应
        meta_warnings = meta.get("warnings") or []
        if isinstance(meta_warnings, list):
            warnings.extend(str(w) for w in meta_warnings[:8])
        if meta.get("encoding_fallback"):
            warnings.append(
                f"ENCODING_ISSUES: 文件非 UTF-8 编码，已按 {meta.get('encoding')} 解码导入"
            )

        if ext in RASTER_FORMATS:
            # V4（审计 R6）：栅格在接入点补剖析 —— nodata/overviews/波段统计
            # 进 meta.json 与响应 meta；缺 CRS 如实披露 crs_missing（仍接受）。
            # profile_raster_file 自身已在 to_thread 且降采样有界；best-effort：
            # 剖析失败绝不阻断上传。
            response_meta_upload: Optional[Dict[str, Any]] = None
            try:
                raster_profile = await get_dataset_profiler().profile_raster_file(
                    str(temp_path), ref_id=f"upload:{upload_id}"
                )
                response_meta_upload = {}
                if raster_profile.raster is not None:
                    rp = raster_profile.raster
                    meta["nodata"] = rp.nodata
                    meta["overviews"] = rp.overviews
                    if rp.band_stats:
                        meta["band_stats"] = [bs.model_dump() for bs in rp.band_stats]
                    response_meta_upload.update(
                        nodata=rp.nodata,
                        overviews=rp.overviews,
                        band_count=rp.band_count,
                    )
                else:
                    meta["raster_profile_error"] = ";".join(
                        str(d) for d in raster_profile.diagnostics[:4]
                    )[:200]
            except Exception as e:  # noqa: BLE001 — 剖析失败不阻断上传（诚实降级）
                logger.warning("栅格剖析失败（不影响上传）: %s", e)
                meta["raster_profile_error"] = str(e)[:200]
                response_meta_upload = {}
            if meta.get("crs") in (None, "", "未知"):
                meta["crs_missing"] = True
                warnings.append(
                    "CRS_MISSING: 栅格无坐标参考系信息（已接受，如实披露；"
                    "未声明 CRS 前不做空间叠加类分析）"
                )
                response_meta_upload["crs_missing"] = True
            response_meta_upload = response_meta_upload or None
        else:
            response_meta_upload = None

        # 保存元信息
        save_meta(upload_dir, meta)

        # 写入数据库
        try:
            async with async_db_session() as db:
                if session_id:
                    from app.models.db_model import Conversation

                    conv = (
                        await db.execute(
                            select(Conversation).where(Conversation.id == session_id)
                        )
                    ).scalar_one_or_none()
                    # Same rules as get_session / materialize: missing row is a
                    # first-turn write; an existing row needs user_id or SEC-08 token.
                    if not authorize_session_write(
                        conv, _user.get("user_id"), owner_token
                    ):
                        raise HTTPException(status_code=404, detail="Session not found")
                    # V4 幂等再导入（审计 R2/R3 归一）：归属校验**之后**、同会话
                    # 范围内按内容指纹探测 —— 命中则复用既有记录（不建新目录/
                    # 新行）。跨会话绝不共享：内容指纹不是跨租户能力令牌。
                    if dedup and content_sha256:
                        dup_result = await db.execute(
                            select(UploadRecord)
                            .where(
                                UploadRecord.session_id == session_id,
                                UploadRecord.content_sha256 == content_sha256,
                            )
                            .order_by(UploadRecord.upload_time.desc())
                            .limit(1)
                        )
                        dup_record = dup_result.scalar_one_or_none()
                        if dup_record is not None:
                            raise _UploadDeduplicated({
                                "id": dup_record.id,
                                "original_name": dup_record.original_name,
                                "file_type": dup_record.file_type,
                                "format": dup_record.format,
                                "crs": dup_record.crs,
                                "geometry_type": dup_record.geometry_type,
                                "feature_count": dup_record.feature_count,
                                "bbox": dup_record.bbox,
                                "file_size": dup_record.file_size,
                            })
                record = UploadRecord(
                    filename=meta.get("output_path", str(upload_dir / filename)),
                    original_name=filename,
                    file_type=meta["file_type"],
                    format=meta["format"],
                    # CRS 诚实：解析器未确认（CSV 未声明）→ 如实 NULL。
                    # 列默认值不动（审计 §3.3 标注为范围外风险），但本路径
                    # 从不再向「未确认」的行静默写 confirmed 4326。
                    crs=meta.get("crs"),
                    geometry_type=meta.get("geometry_type"),
                    feature_count=meta.get("feature_count", 0),
                    bbox=meta.get("bbox"),
                    file_size=file_size,
                    session_id=session_id,
                    content_sha256=content_sha256,
                )
                db.add(record)
                await db.flush()
                # CRS 诚实（审计 §3.3）：列默认 'EPSG:4326' 在 ORM 里对 None
                # 也照样生效（SQLAlchemy 把 None 视为未提供 → 填默认）。解析器
                # 未确认 CRS 时必须显式回写 NULL，否则 DB 行再次谎称 confirmed
                # 4326 —— 列默认本身不动（范围外风险，审计标注）。
                if meta.get("crs") is None and record.crs is not None:
                    await db.execute(
                        sa_update(UploadRecord)
                        .where(UploadRecord.id == record.id)
                        .values(crs=None)
                    )
                await db.refresh(record)
        except _UploadDeduplicated as dup_exc:
            # 幂等命中：清理本次新建的目录（含刚写入的临时文件），返回既有记录
            shutil.rmtree(upload_dir, ignore_errors=True)
            warnings.append("内容与既有上传完全相同（sha256 一致），已复用既有记录")
            dup = dup_exc.fields
            return UploadResponse(
                id=dup["id"],
                original_name=dup["original_name"],
                file_type=dup["file_type"],
                format=dup["format"],
                crs=dup["crs"],
                geometry_type=dup["geometry_type"],
                feature_count=dup["feature_count"],
                bbox=dup["bbox"],
                file_size=dup["file_size"],
                deduplicated=True,
                ignored_files=ignored_files,
                warnings=warnings,
                message="上传成功（幂等导入：复用既有上传）",
            )
        except (OSError, RuntimeError, SQLAlchemyError) as e:
            # #546：DBAPIError 是 SQLAlchemyError 子类而非 OSError/RuntimeError ——
            # 旧代码漏捕导致 500 逃逸且目录残留。这里并入 SQLAlchemyError 一并处理。
            logger.error(f"数据库写入失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="记录保存失败")
    except HTTPException:
        # 4xx/5xx 业务失败（校验/解析/元信息/DB）：清理本次上传目录后重抛
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    except Exception:
        # 兜底：任何未映射异常（如 save_meta 的 OSError）同样不能留孤儿目录
        logger.error("上传失败，清理孤儿目录", exc_info=True)
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    # ---- V3 摄入管线增值车道（opt-in；审计 R1：不再让全部可靠性投资止于测试）----
    # 触发：register_ref=True 或 X-Session-Id 头在场。best-effort 纪律与
    # dispatch-seam 相同 —— 车道任何失败（含未预期异常）都不阻断上传本体，
    # 只如实记入 ref_registration_error。会话归属已在上面的 DB 块校验。
    session_ref: Optional[str] = None
    profile_summary: Optional[Dict[str, Any]] = None
    quality_summary: Optional[Dict[str, Any]] = None
    ref_registration_error: Optional[str] = None
    if register_ref or x_session_id is not None:
        lane_out = await _run_ingest_lane(
            session_id=session_id,
            meta=meta,
            filename=filename,
            declared_crs=crs,
        )
        session_ref = lane_out["session_ref"]
        profile_summary = lane_out["profile_summary"]
        quality_summary = lane_out["quality"]
        ref_registration_error = lane_out["error"]
        if lane_out.get("duplicate") and session_ref:
            warnings.append(f"会话中已存在相同内容的数据引用，复用 {session_ref}")

    return UploadResponse(
        id=record.id,
        original_name=record.original_name,
        file_type=record.file_type,
        format=record.format,
        crs=record.crs,
        crs_source=meta.get("crs_source"),
        geometry_type=record.geometry_type,
        feature_count=record.feature_count,
        bbox=record.bbox,
        file_size=record.file_size,
        ignored_files=ignored_files,
        warnings=warnings,
        meta=response_meta_upload,
        session_ref=session_ref,
        profile_summary=profile_summary,
        quality=quality_summary,
        ref_registration_error=ref_registration_error,
    )


async def _run_ingest_lane(
    *,
    session_id: Optional[str],
    meta: Dict[str, Any],
    filename: str,
    declared_crs: Optional[str],
) -> Dict[str, Any]:
    """把已解析的矢量 FC 送入 V3 摄入管线（store/dedup/profile/quality/register）。

    纪律：车道失败绝不 raise —— 返回有界 dict，错误落在 ``error`` 字段。
    只消费 original.geojson（本路由已做 50MB 上限与目录校验）。
    """
    out: Dict[str, Any] = {
        "session_ref": None,
        "profile_summary": None,
        "quality": None,
        "error": None,
        "duplicate": False,
    }
    if not session_id:
        out["error"] = "register_ref requested but no session_id; session registration skipped"
        return out
    if meta.get("file_type") != "vector":
        out["error"] = "session ingest lane accepts vector data only; raster skipped"
        return out

    try:
        # original.geojson 读取是同步 CPU+IO（ijson 流式）→ executor。
        output_path = meta.get("output_path")
        if not output_path or not Path(output_path).exists():
            out["error"] = "parsed geojson output missing; lane skipped"
            return out
        loop = asyncio.get_running_loop()
        features = await loop.run_in_executor(None, _load_geojson_features, Path(output_path))
        fc = {"type": "FeatureCollection", "features": features}
        # 只传「已确认」的 CRS 证据；未确认（assumed）→ 空串 = 质量检查如实
        # 报 crs_missing，绝不把假设伪装成声明。
        confirmed_crs = (
            str(meta.get("crs") or "")
            if meta.get("crs_source") in ("declared", "source")
            else (declared_crs or "")
        )
        result = await get_ingest_pipeline().ingest(
            session_id,
            fc,
            name=filename,
            crs=confirmed_crs,
            source_type="upload",
            prefix="upload",
        )
        if result.ok:
            out["session_ref"] = result.ref_id
            out["profile_summary"] = result.profile_summary
            out["quality"] = result.quality_summary
            out["duplicate"] = result.duplicate
        else:
            out["error"] = f"{result.error_code}: {result.error}"
    except Exception as e:  # noqa: BLE001 — 增值车道失败不影响上传本体
        logger.warning("会话登记车道失败（不影响上传）: %s", e)
        out["error"] = f"ingest lane failed: {e}"
    return out


# ==================== 查询接口 ====================

@router.get("/uploads", response_model=UploadListResponse)
async def list_uploads(
    _user: dict = Depends(get_current_user),
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """获取上传文件列表

    审计 S42：session_id 缺省时之前返回全局最近 100 条 —— 任何登录用户能拉到
    他人上传文件名、bbox（常含真实位置 PII）。现在要求 session_id 必填且校验归属。
    """
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id 为必填，避免跨租户泄漏",
        )
    async with async_db_session() as db:
        await _verify_session_owner(
            db, session_id, _user.get("user_id"), owner_token=owner_token
        )
        stmt = select(UploadRecord).where(UploadRecord.session_id == session_id).order_by(UploadRecord.upload_time.desc())
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()
        result = await db.execute(stmt.offset(offset).limit(limit))
        records = result.scalars().all()

    return UploadListResponse(
        total=total,
        uploads=[
            UploadResponse(
                id=r.id,
                original_name=r.original_name,
                file_type=r.file_type,
                format=r.format,
                crs=r.crs,
                geometry_type=r.geometry_type,
                feature_count=r.feature_count,
                bbox=r.bbox,
                file_size=r.file_size,
            )
            for r in records
        ],
    )


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
async def get_upload(
    upload_id: int,
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """获取单个上传文件的详情

    审计 S42：upload_id 是顺序整数易枚举；通过 record.session_id 解析归属。
    SEC-08：转发 X-Session-Token，与 POST /upload 对齐（#1109）。
    """
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        await _verify_session_owner(
            db, record.session_id, _user.get("user_id"), owner_token=owner_token
        )

    return UploadResponse(
        id=record.id,
        original_name=record.original_name,
        file_type=record.file_type,
        format=record.format,
        crs=record.crs,
        geometry_type=record.geometry_type,
        feature_count=record.feature_count,
        bbox=record.bbox,
        file_size=record.file_size,
    )


@router.get("/uploads/{upload_id}/geojson")
async def get_upload_geojson(
    upload_id: int,
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """获取上传文件的 GeoJSON 数据（用于地图渲染）。

    审计 S49：之前无大小限制 -- 任意大的 GeoJSON 被 json.load 一次性读入内存
    并流式返回，超大文件可导致内存爆。加 50MB 上限（与 MAX_VECTOR_SIZE 一致）。
    SEC-08：转发 X-Session-Token，与 POST /upload 对齐（#1109）。
    """
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        await _verify_session_owner(
            db, record.session_id, _user.get("user_id"), owner_token=owner_token
        )

    if record.file_type != "vector":
        raise HTTPException(status_code=400, detail="该文件不是矢量数据")

    geojson_path = Path(record.filename)
    if not geojson_path.exists():
        raise HTTPException(status_code=404, detail="GeoJSON 文件不存在")

    resolved = geojson_path.resolve()
    data_root = Path(settings.DATA_DIR).resolve()
    if data_root not in resolved.parents and resolved != data_root:
        raise HTTPException(status_code=400, detail="非法文件路径")

    # 审计 S49：文件大小检查 -- 防 OOM
    file_size = geojson_path.stat().st_size
    if file_size > MAX_VECTOR_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"GeoJSON 文件过大（{file_size / 1024 / 1024:.1f}MB > {MAX_VECTOR_SIZE / 1024 / 1024:.0f}MB 限制），请通过 /layers/data/{{ref_id}} 分片读取",
        )

    # 计算隔离不变式 1：ijson 解析 50MB GeoJSON 是同步 CPU 工作，直接在
    # async def 里执行会阻塞整个事件循环（#386）。照抄本文件上传路径的
    # run_in_executor 模式，把解析扔到默认 threadpool。
    loop = asyncio.get_running_loop()
    features = await loop.run_in_executor(None, _load_geojson_features, geojson_path)
    # #590：响应侧序列化同样不能由默认 JSONResponse 在事件循环上整包编码
    # —— 与 /layers/data 一致，分块 + to_thread 序列化后以 bytes 返回。
    body = await serialize_geojson({"type": "FeatureCollection", "features": features})
    return Response(content=body, media_type="application/json")


@router.delete("/uploads/{upload_id}", response_model=UploadDeleteResponse)
async def delete_upload(
    upload_id: int,
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    """删除上传记录及文件"""
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        # 审计 S42：删除前必须确认归属 —— 否则任何用户可枚举整数 id 删除他人数据。
        # SEC-08：转发 X-Session-Token，与 POST /upload 对齐（#1109）。
        await _verify_session_owner(
            db, record.session_id, _user.get("user_id"), owner_token=owner_token
        )

        file_path = Path(record.filename)
        await db.delete(record)

    # File cleanup AFTER DB commit succeeds
    upload_dir = file_path.parent
    if upload_dir.exists():
        resolved = upload_dir.resolve()
        uploads_root = Path(settings.DATA_DIR, "uploads").resolve()
        if uploads_root in resolved.parents:
            shutil.rmtree(upload_dir, ignore_errors=True)

    return UploadDeleteResponse(success=True, message="已删除")
