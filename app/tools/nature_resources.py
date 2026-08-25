"""
自然资源监测工具包 - V3.0 Phase 1
提供 NDVI 植被分析及资产管理功能
"""
import logging
from typing import Optional
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.spatial_tasks import run_ndvi_analysis
from app.services.jobs.submit import submit_durable_job
from app.tools._utils import db_session
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

class NDVIArgs(BaseModel):
    raster_path: str = Field(..., description="遥感影像文件路径 (可从之前上传或分析结果中获取)")
    nir_band: Optional[int] = Field(None, description="近红外波段索引 (1-based)。若不指定将尝试自动探测。")
    red_band: Optional[int] = Field(None, description="红光波段索引 (1-based)。若不指定将尝试自动探测。")
    session_id: Optional[str] = Field(None, description="会话 ID")

class AssetManageArgs(BaseModel):
    asset_id: int = Field(..., description="分析资产记录 ID")
    action: str = Field(..., description="维护动作：'rename' (重命名) 或 'delete' (删除)")
    new_name: Optional[str] = Field(None, description="当动作为 rename 时必填")

def register_nature_resource_tools(registry: ToolRegistry):
    """注册自然资源监测相关工具"""

    @tool(registry, name="analyze_vegetation_index",
          tier=2, domains=["raster"],
          description=(
              "本地 TIFF 的 NDVI 计算 (Celery 异步)：用户上传遥感影像后调用，自动探测 RGBN/Sentinel-2 波段，"
              "持久化为分析资产并入库。"
              "\n何时用：用户已通过 /upload 上传 .tif/.tiff 后请求『算下 NDVI』；要把结果保存供后续 zonal_stats。"
              "\n何时不用：(1) 在线 bbox 计算 — 用 compute_ndvi（无需上传）；"
              "(2) 双时相变化 — 用 detect_vegetation_change；"
              "(3) 不知道波段顺序 — 工具会自动探测，但 3 波段 RGB 无 NIR 时会失败。"
              "\n关键约束：raster_path 必须是 list_uploaded_data 返回过的路径；任务异步，返回 task_id 后需轮询。"
          ),
          # #996: 工具体经 submit_durable_job 内部投递 Celery
          # （run_ndvi_analysis.apply_async）——重工具显式标 heavy；提交路径
          # 本身只做 DB 写 + broker 入队，60s 预算绰绰有余。
          cost="heavy", timeout=60.0)
    def analyze_vegetation_index(raster_path: str, nir_band: Optional[int] = None, red_band: Optional[int] = None, session_id: Optional[str] = None) -> dict:
        # ADR-0052: 重计算走 durable job —— 返回 job_id 让用户能在任务中心看到进度
        # 并随时取消；幂等键防止双击/重连提交两次同样的分析。
        return submit_durable_job(
            celery_task=run_ndvi_analysis,
            task_type="ndvi",
            display_name="NDVI 植被指数分析",
            params={
                "raster_path": raster_path,
                "nir_band": nir_band,
                "red_band": red_band,
            },
            task_args=(raster_path, nir_band, red_band, session_id),
            session_id=session_id,
        )

    @tool(registry, name="list_analysis_assets",
          tier=2, domains=["raster"],
          description='获取当前系统中保存的所有遥感分析产物（如 NDVI、NDWI 结果文件）列表。用于回答用户"我之前生成了什么"或进行资产回顾。')
    def list_analysis_assets(session_id: Optional[str] = None) -> dict:
        from app.models.upload import UploadRecord

        # #543：与会话工具同款语义 —— 无解析会话（匿名/上下文缺失/伪造 id）
        # 一律拒绝，绝不跨会话返回全库资产列表（旧代码 if session_id: 是
        # 条件过滤，None-session 时直接返回全局 top-100 资产名/路径/bbox）。
        resolved_session = _resolve_session_id(session_id)
        if not resolved_session:
            return {
                "error": "未检测到有效的会话上下文，无法列出分析资产",
                "assets": [],
                "count": 0,
            }

        with db_session() as db:
            records = (
                db.query(UploadRecord)
                .filter(
                    UploadRecord.geometry_type == "raster_analysis",
                    UploadRecord.session_id == resolved_session,
                )
                .order_by(UploadRecord.upload_time.desc())
                .limit(100)
                .all()
            )
            assets = [{
                "id": r.id,
                "name": r.original_name,
                "path": r.filename,
                "time": r.upload_time.isoformat(),
                "bbox": r.bbox
            } for r in records]

            return {
                "success": True,
                "assets": assets,
                "count": len(assets),
                "system_message": "这是目前的分析资产列表。你可以直接告诉用户这些成果，或建议将其加载到地图上。"
            }

    @tool(registry, name="manage_analysis_asset",
          tier=3, domains=["raster"],
          description=(
              "维护遥感分析资产：重命名或永久删除 NDVI/NDWI 等分析产物（含物理文件）。"
              "\n何时用：用户明确说『把 XX 资产删掉』『把那个 NDVI 改名为 春季』；"
              "在 list_analysis_assets 列表里发现重复/废弃产物时的清理。"
              "\n何时不用：(1) 想看资产列表 - 用 list_analysis_assets (只读)；"
              "(2) 想删除上传文件而非分析资产 - 用 upload 路由 (这个只管 geometry_type='raster_analysis' 的 UploadRecord)；"
              "(3) 用户未明确授权删除 - 不要主动调，特别是 action='delete' 不可逆。"
              "\n关键约束：action ∈ {rename, delete}；rename 必须给 new_name；delete 同时移除磁盘 TIFF。"
              "\n安全：tier=3（destructive）-- 仅在用户明确要求时调用。asset 必须属于当前 session。"
          ))
    def manage_analysis_asset(asset_id: int, action: str, new_name: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """审计 S43：之前任何 LLM 上下文都能按顺序整数 asset_id 删除/重命名他人资产。

        修法：(1) 升到 tier=3，PR 2 的 /chat/tools/execute 已要求 confirm_destructive；
        (2) 加 session_id 参数（registry 自动注入），校验 record.session_id 匹配，
        防 LLM 跨 session 操作他人资产。完整 user_id 校验需要 tool dispatch 重构。
        """
        from app.models.upload import UploadRecord
        import os
        from app.core.config import settings

        with db_session() as db:
            # #543: 查询同时限定 geometry_type（本工具契约只管 raster_analysis）——
            # 之前仅按自增 id 查找，任何上传记录都能被改名/删除。
            record = db.query(UploadRecord).filter(
                UploadRecord.id == asset_id,
                UploadRecord.geometry_type == "raster_analysis",
            ).first()
            if not record:
                return {"error": "未找到对应的分析资产记录"}

            # 审计 S43 + #543：asset 必须属于当前 session（防跨 session IDOR）。
            # 会话校验无条件化：record.session_id 为 NULL 的旧匿名记录同样拒绝
            # —— 与 upload 路由层 _verify_session_owner（session_id 缺省即 404）
            # 的守卫层级对齐，NULL 记录的删除必须走上传路由。
            resolved_session = _resolve_session_id(session_id)
            if record.session_id is None:
                return {"error": "该资产未关联会话，无法通过工具管理（请通过上传接口处理）"}
            if not resolved_session or record.session_id != resolved_session:
                return {"error": "该资产不属于当前会话，无权操作"}

            if action == "rename" and new_name:
                old_name = record.original_name
                record.original_name = new_name
                return {"success": True, "message": f"资产已从「{old_name}」重命名为「{new_name}」"}

            elif action == "delete":
                # 删除物理文件
                try:
                    from app.utils.path import validate_data_path
                    full_path = validate_data_path(record.filename, settings.DATA_DIR)
                except ValueError as e:
                    return {"error": f"文件路径校验失败: {e}"}
                if os.path.exists(full_path):
                    os.remove(full_path)

                name = record.original_name
                db.delete(record)
                return {"success": True, "message": f"资产「{name}」及物理文件已永久删除"}

            return {"error": f"不支持的动作或缺少必要参数: {action}"}
