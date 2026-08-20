"""
自然资源监测报告生成系统
将多个分析资产（NDVI、变化检测、地形分析等）整合为标准化监测报告
"""
import html as html_mod
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from app.tools.registry import ToolRegistry, tool
from app.core.config import settings
from app.tools._utils import db_session
from app.tools.upload_tools import _resolve_session_id
from app.models.upload import UploadRecord

logger = logging.getLogger(__name__)

REPORT_OUTPUT_DIR = os.path.join(settings.DATA_DIR, "monitoring_reports")

#: 单次报告可引用的资产上限 —— 防 LLM 一次拉取海量 UploadRecord 详情（审计 #543）。
MAX_REPORT_ASSETS = 100


def _get_template_name(template_key: str) -> str:
    """映射模板键到模板文件名"""
    mapping = {
        "natural_resources": "monitoring_report.html",
        "vegetation": "monitoring_report_vegetation.html",
        "water": "monitoring_report_water.html",
        "fire": "monitoring_report_fire.html",
    }
    return mapping.get(template_key, "monitoring_report.html")


def _load_assets(asset_ids: List[int], session_id: Optional[str] = None) -> List[dict]:
    """从数据库加载分析资产详情（#543：查询必须限定到调用方会话）。

    UploadRecord.id 是顺序自增整数、易枚举；session_id 是唯一的归属令牌。
    与 list_uploaded_data / get_upload_info 同款语义：
      - 先经 _resolve_session_id 用运行时已验证的会话 id 校验/覆盖调用方传入值；
      - 解析后无会话 id（匿名/上下文缺失）→ 直接返回空，绝不跨会话读取；
      - 仅返回本会话的 raster_analysis 资产（与 list_analysis_assets 契约一致）。
    """
    session_id = _resolve_session_id(session_id)
    if not session_id:
        return []
    with db_session() as db:
        records = db.query(UploadRecord).filter(
            UploadRecord.id.in_(asset_ids),
            UploadRecord.session_id == session_id,
            UploadRecord.geometry_type == "raster_analysis",
        ).all()
        return [
            {
                "id": r.id,
                "name": r.original_name,
                "type": r.geometry_type,
                "format": r.format,
                "bbox": r.bbox,
                "time": r.upload_time.isoformat() if r.upload_time else None,
                "file_size_kb": round(r.file_size / 1024, 1) if r.file_size else 0,
            }
            for r in records
        ]


def _render_monitoring_report_html(
    title: str,
    region_name: str,
    period: str,
    assets: List[dict],
    summary_text: Optional[str],
    conclusions: Optional[str],
    template_name: str,
) -> str:
    """渲染监测报告 HTML"""
    try:
        import jinja2
        template_path = os.path.join(os.path.dirname(__file__), "..", "services", "templates")
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_path),
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )
        template = env.get_template(template_name)
    except ImportError:
        # jinja2 未安装 —— 回到内联 fallback（先捕获 ImportError，
        # 再捕获 jinja2.TemplateNotFound，避免 jinja2 名未绑定时的 NameError）。
        return _fallback_monitoring_html(title, region_name, period, assets, summary_text, conclusions)
    except jinja2.TemplateNotFound:
        # 模板文件缺失 —— 同样走 fallback。
        return _fallback_monitoring_html(title, region_name, period, assets, summary_text, conclusions)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 按类型分组资产
    asset_groups = {}
    for a in assets:
        gtype = a.get("type", "other")
        asset_groups.setdefault(gtype, []).append(a)

    return template.render(
        title=title,
        region_name=region_name,
        period=period,
        generated_at=now,
        assets=assets,
        asset_groups=asset_groups,
        summary=summary_text or "",
        conclusions=conclusions or "",
        asset_count=len(assets),
    )


def _fallback_monitoring_html(
    title: str,
    region_name: str,
    period: str,
    assets: List[dict],
    summary_text: Optional[str],
    conclusions: Optional[str],
) -> str:
    """当模板文件缺失时的极简 fallback HTML"""
    esc = html_mod.escape
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>{esc(title)}</title>",
        "<style>body{font-family:sans-serif;max-width:900px;margin:40px auto;padding:20px;line-height:1.6;color:#333}",
        "h1{color:#1e40af;border-bottom:3px solid #3b82f6;padding-bottom:10px}",
        "h2{color:#1e40af;margin-top:30px;border-left:4px solid #3b82f6;padding-left:12px}",
        ".meta{color:#64748b;font-size:14px;margin:10px 0}",
        ".asset-card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:12px 0}",
        ".asset-name{font-weight:600;color:#1e293b}",
        ".asset-meta{color:#64748b;font-size:13px}",
        ".summary{background:#ecfdf5;border-left:4px solid #059669;padding:16px;margin:16px 0}",
        ".conclusions{background:#eff6ff;border-left:4px solid #3b82f6;padding:16px;margin:16px 0}",
        "</style></head><body>",
        f"<h1>{esc(title)}</h1>",
        f"<div class='meta'>监测区域：{esc(region_name)} | 监测时期：{esc(period)} | 生成时间：{now}</div>",
    ]

    if summary_text:
        parts.append(f"<h2>执行摘要</h2><div class='summary'>{esc(summary_text)}</div>")

    if assets:
        parts.append(f"<h2>分析资产 ({len(assets)} 项)</h2>")
        for a in assets:
            parts.append(
                f"<div class='asset-card'>"
                f"<div class='asset-name'>{esc(a.get('name', '未命名'))}</div>"
                f"<div class='asset-meta'>ID: {a.get('id')} | 格式: {a.get('format')} | "
                f"大小: {a.get('file_size_kb', 0)} KB | 时间: {a.get('time', 'N/A')}</div>"
                f"</div>"
            )

    if conclusions:
        parts.append(f"<h2>结论与建议</h2><div class='conclusions'>{esc(conclusions)}</div>")

    parts.append(f"<hr><p style='text-align:center;color:#94a3b8;font-size:12px'>由 WebGIS AI Agent 自动生成 · {now}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _html_to_pdf(html_content: str, output_path: str) -> None:
    """HTML 转 PDF"""
    try:
        import weasyprint
        weasyprint.HTML(string=html_content).write_pdf(output_path)
    except ImportError:
        raise ImportError("WeasyPrint 未安装，无法生成 PDF。请运行: pip install weasyprint")


def register_monitoring_report_tools(registry: ToolRegistry):
    """注册监测报告生成工具"""

    @tool(registry, tier=2, domains=["report"], name="generate_monitoring_report",
          description=(
              "生成标准化的自然资源监测报告。将多个分析资产（如 NDVI 分析、变化检测、"
              "地形分析等）整合为一份包含封面、执行摘要、专题图、统计分析和结论建议的"
              "完整报告。支持 HTML/PDF/Markdown 三种输出格式。适用于定期监测汇报、"
              "项目验收、领导汇报等场景。"
          ),
          param_descriptions={
              "title": "报告标题",
              "region_name": "监测区域名称",
              "period": "监测时期描述",
              "analysis_assets": "分析资产 ID 列表（整数数组）",
              "report_template": "报告模板类型",
              "format": "输出格式: html, pdf, markdown",
              "summary_text": "AI 撰写的执行摘要（可选）",
              "conclusions": "结论与建议（可选）",
          })
    def generate_monitoring_report(
        title: str,
        region_name: str,
        period: str,
        analysis_assets: List[int],
        report_template: str = "natural_resources",
        format: str = "html",
        summary_text: Optional[str] = None,
        conclusions: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        try:
            os.makedirs(REPORT_OUTPUT_DIR, exist_ok=True)

            # #543: 单次报告引用资产数设上限 —— 防 LLM 借工具枚举任意 UploadRecord。
            if len(analysis_assets) > MAX_REPORT_ASSETS:
                return {"error": f"分析资产数量超过上限（{MAX_REPORT_ASSETS}），请分批生成报告"}

            # 加载资产详情 — session_id 由 registry 注入（或经 _resolve_session_id 校验），
            # _load_assets 仅返回本会话的资产，跨会话 ID 返回空（#543）。
            assets = _load_assets(analysis_assets, session_id=session_id)
            if not assets:
                return {"error": "未找到指定的分析资产，请确认 asset_id 正确且资产存在于资产库中"}

            # 生成报告 ID 和文件名
            report_id = str(uuid.uuid4())
            ext = {"pdf": "pdf", "html": "html", "markdown": "md", "md": "md"}.get(format.lower(), "html")
            file_name = f"monitoring_report_{report_id}.{ext}"
            file_path = os.path.join(REPORT_OUTPUT_DIR, file_name)

            # 渲染报告
            template_name = _get_template_name(report_template)

            if format.lower() in ("markdown", "md"):
                # Markdown 格式
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                lines = [
                    f"# {title}",
                    "",
                    f"> **监测区域**: {region_name}  |  **监测时期**: {period}  |  **生成时间**: {now}",
                    "",
                ]
                if summary_text:
                    lines.extend(["## 执行摘要", "", summary_text, ""])
                lines.extend(["## 分析资产", ""])
                for a in assets:
                    lines.append(f"- **{a['name']}** (ID: {a['id']}, 格式: {a['format']}, 大小: {a['file_size_kb']} KB)")
                lines.append("")
                if conclusions:
                    lines.extend(["## 结论与建议", "", conclusions, ""])
                lines.append(f"---\n*由 WebGIS AI Agent 自动生成 · {now}*")

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))

            else:
                # HTML / PDF 格式
                html_content = _render_monitoring_report_html(
                    title=title,
                    region_name=region_name,
                    period=period,
                    assets=assets,
                    summary_text=summary_text,
                    conclusions=conclusions,
                    template_name=template_name,
                )

                if format.lower() == "pdf":
                    _html_to_pdf(html_content, file_path)
                else:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(html_content)

            file_size = os.path.getsize(file_path)

            return {
                "success": True,
                "type": "monitoring_report",
                "report_id": report_id,
                "title": title,
                "region": region_name,
                "period": period,
                "format": format.lower(),
                "file_size_kb": round(file_size / 1024, 1),
                "file_path": file_path,
                "asset_count": len(assets),
                "assets_included": [{"id": a["id"], "name": a["name"]} for a in assets],
                "message": (
                    f"监测报告「{title}」已生成完毕（{format.upper()} 格式，"
                    f"{round(file_size / 1024, 1)} KB）。报告包含 {len(assets)} 项分析资产。"
                ),
            }

        except ImportError as e:
            return {"error": f"缺少依赖: {e}"}
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error(f"Monitoring report generation failed: {e}", exc_info=True)
            return {"error": f"报告生成失败: {str(e)}"}
