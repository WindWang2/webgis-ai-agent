"""
报告生成服务 - 支持将空间分析结果导出为PDF/HTML/Markdown格式
使用Jinja2模板渲染HTML，WeasyPrint转换为PDF
支持Markdown导出，便于文档编辑和版本控制
"""
import os
from datetime import datetime
from typing import Dict, Any, Optional
import jinja2
try:
    import weasyprint
except ImportError:
    weasyprint = None
import json
import logging

logger = logging.getLogger(__name__)

class ReportService:
    def __init__(self):
        # 初始化Jinja2模板环境
        template_path = os.path.join(os.path.dirname(__file__), "templates")
        self.template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_path),
            autoescape=True
        )
    
    async def generate_report(
        self,
        task: Any,
        report_id: str,
        output_path: str,
        format: str = "pdf",
        include_screenshot: bool = True
    ) -> bool:
        """
        生成分析报告
        
        Args:
            task: 任务对象（来自TaskService）
            report_id: 报告唯一ID
            output_path: 输出文件路径
            format: pdf/html/markdown/md
            include_screenshot: 是否包含地图截图
            
        Returns:
            生成是否成功
        """
        try:
            # 准备报告数据
            report_data = self._prepare_report_data(task, include_screenshot)
            
            if format in ["markdown", "md"]:
                # 生成Markdown格式
                md_content = self._render_markdown_template(report_data)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                return True
            else:
                # 渲染HTML模板，用于PDF/HTML格式
                html_content = self._render_html_template(report_data)
                
                if format == "html":
                    # 直接保存HTML
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    return True
                elif format == "pdf":
                    # 转换HTML为PDF
                    self._html_to_pdf(html_content, output_path)
                    return True
                else:
                    logger.error(f"不支持的报告格式: {format}")
                    return False
                
        except Exception as e:
            logger.error(f"报告生成失败: {e}", exc_info=True)
            return False
    
    def _prepare_report_data(self, task: Any, include_screenshot: bool) -> Dict[str, Any]:
        """准备报告所需的数据"""
        # 分析结果摘要
        result_summary = json.loads(task.result_summary) if task.result_summary else {}
        
        # 处理统计数据
        stats = result_summary.get("stats", {})
        parameters = task.parameters if task.parameters else {}
        
        return {
            "report_title": f"空间分析报告: {task.task_type}",
            "task_info": {
                "id": task.id,
                "type": task.task_type,
                "status": task.status,
                "created_at": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "completed_at": task.completed_at.strftime("%Y-%m-%d %H:%M:%S") if task.completed_at else None,
                "duration": (task.completed_at - task.created_at).total_seconds() if task.completed_at else 0
            },
            "parameters": parameters,
            "summary": result_summary,
            "statistics": stats,
            "include_screenshot": include_screenshot,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def _render_html_template(self, data: Dict[str, Any]) -> str:
        """使用Jinja2渲染HTML模板"""
        try:
            template = self.template_env.get_template("report_default.html")
            return template.render(**data)
        except jinja2.TemplateNotFound:
            # 如果模板不存在，使用默认内置模板
            return self._get_default_html_template(data)
    
    def _get_default_html_template(self, data: Dict[str, Any]) -> str:
        """内置默认HTML模板，当文件模板不存在时使用"""
        return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{data['report_title']}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; padding: 40px; line-height: 1.6; color: #333; }}
        .header {{ text-align: center; margin-bottom: 40px; padding-bottom: 20px; border-bottom: 2px solid #2563eb; }}
        .header h1 {{ color: #1e40af; margin-bottom: 10px; }}
        .section {{ margin-bottom: 30px; }}
        .section h2 {{ color: #1e40af; margin-bottom: 15px; font-size: 1.3rem; border-left: 4px solid #2563eb; padding-left: 10px; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin-bottom: 20px; }}
        .info-item {{ background: #f8fafc; padding: 12px; border-radius: 6px; }}
        .info-label {{ font-weight: 600; color: #475569; margin-bottom: 3px; }}
        .info-value {{ color: #1e293b; }}
        .table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        .table th, .table td {{ padding: 10px; text-align: left; border: 1px solid #e2e8f0; }}
        .table th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-top: 15px; }}
        .stat-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-label {{ font-size: 0.9rem; opacity: 0.9; margin-bottom: 5px; }}
        .stat-value {{ font-size: 1.8rem; font-weight: 700; }}
        .footer {{ text-align: center; margin-top: 50px; padding-top: 20px; border-top: 1px solid #e2e8f0; color: #64748b; font-size: 0.9rem; }}
        @media print {{
            body {{ padding: 0; }}
            .header {{ border-bottom-color: #94a3b8; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{data['report_title']}</h1>
        <p>生成时间: {data['generated_at']}</p>
    </div>

    <div class="section">
        <h2>任务信息</h2>
        <div class="info-grid">
            <div class="info-item">
                <div class="info-label">任务ID</div>
                <div class="info-value">{data['task_info']['id']}</div>
            </div>
            <div class="info-item">
                <div class="info-label">分析类型</div>
                <div class="info-value">{data['task_info']['type']}</div>
            </div>
            <div class="info-item">
                <div class="info-label">创建时间</div>
                <div class="info-value">{data['task_info']['created_at']}</div>
            </div>
            <div class="info-item">
                <div class="info-label">完成时间</div>
                <div class="info-value">{data['task_info']['completed_at'] or 'N/A'}</div>
            </div>
            <div class="info-item">
                <div class="info-label">处理时长</div>
                <div class="info-value">{data['task_info']['duration']:.2f} 秒</div>
            </div>
            <div class="info-item">
                <div class="info-label">状态</div>
                <div class="info-value">{data['task_info']['status']}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>分析参数</h2>
        {self._render_parameters(data['parameters'])}
    </div>

    <div class="section">
        <h2>统计结果</h2>
        {self._render_statistics(data['statistics'])}
    </div>

    <div class="section">
        <h2>结果摘要</h2>
        <pre style="background: #f8fafc; padding: 15px; border-radius: 6px; overflow-x: auto;">{json.dumps(data['summary'], indent=2, ensure_ascii=False)}</pre>
    </div>

    <div class="footer">
        <p>WebGIS AI Agent 自动生成报告</p>
    </div>
</body>
</html>
        """
    
    def _render_parameters(self, parameters: Dict[str, Any]) -> str:
        """渲染参数部分"""
        if not parameters:
            return "<p>无参数</p>"
        
        html = "<div class='info-grid'>"
        for key, value in parameters.items():
            html += f"""
            <div class="info-item">
                <div class="info-label">{key}</div>
                <div class="info-value">{value}</div>
            </div>
            """
        html += "</div>"
        return html
    
    def _render_statistics(self, stats: Dict[str, Any]) -> str:
        """渲染统计结果部分"""
        if not stats:
            return "<p>无统计数据</p>"
        
        html = "<div class='stats-grid'>"
        for key, value in stats.items():
            if isinstance(value, (int, float)):
                html += f"""
                <div class="stat-card">
                    <div class="stat-label">{key}</div>
                    <div class="stat-value">{value}</div>
                </div>
                """
        
        # 剩余非数值统计
        html += "</div><table class='table'><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>"
        for key, value in stats.items():
            if not isinstance(value, (int, float)):
                html += f"<tr><td>{key}</td><td>{value}</td></tr>"
        
        html += "</tbody></table>"
        return html
    
    def _html_to_pdf(self, html_content: str, output_path: str) -> None:
        """将HTML内容转换为PDF"""
        if weasyprint is None:
            raise ImportError("WeasyPrint not installed. Install with: pip install weasyprint")
        weasyprint.HTML(string=html_content).write_pdf(output_path)
    
    def _render_markdown_template(self, data: Dict[str, Any]) -> str:
        """生成Markdown格式报告"""
        md_lines = []

        # 标题
        md_lines.append(f"# {data['report_title']}")
        md_lines.append("")
        md_lines.append(f"> 生成时间: {data['generated_at']}")
        md_lines.append("")

        # 任务信息
        md_lines.append("## 任务信息")
        md_lines.append("")
        md_lines.append(f"| 项目 | 值 |")
        md_lines.append(f"| --- | --- |")
        task_info = data['task_info']
        md_lines.append(f"| 任务ID | `{task_info['id']}` |")
        md_lines.append(f"| 分析类型 | {task_info['type']} |")
        md_lines.append(f"| 状态 | {task_info['status']} |")
        md_lines.append(f"| 创建时间 | {task_info['created_at']} |")
        completed_at = task_info['completed_at'] or 'N/A'
        md_lines.append(f"| 完成时间 | {completed_at} |")
        md_lines.append(f"| 处理时长 | {task_info['duration']:.2f} 秒 |")
        md_lines.append("")

        # 分析参数
        md_lines.append("## 分析参数")
        md_lines.append("")
        parameters = data.get('parameters', {})
        if parameters:
            md_lines.append(f"| 参数名 | 参数值 |")
            md_lines.append(f"| --- | --- |")
            for key, value in parameters.items():
                md_lines.append(f"| {key} | `{value}` |")
        else:
            md_lines.append("*无参数*")
        md_lines.append("")

        # 统计结果
        md_lines.append("## 统计结果")
        md_lines.append("")
        stats = data.get('statistics', {})
        if stats:
            # 分离数值和非数值
            numeric_stats = {k: v for k, v in stats.items() if isinstance(v, (int, float))}
            other_stats = {k: v for k, v in stats.items() if not isinstance(v, (int, float))}

            if numeric_stats:
                md_lines.append("### 关键指标")
                md_lines.append("")
                for key, value in numeric_stats.items():
                    md_lines.append(f"- **{key}**: {value}")
                md_lines.append("")

            if other_stats:
                md_lines.append("### 详细数据")
                md_lines.append("")
                md_lines.append(f"| 指标 | 值 |")
                md_lines.append(f"| --- | --- |")
                for key, value in other_stats.items():
                    md_lines.append(f"| {key} | {value} |")
        else:
            md_lines.append("*无统计数据*")
        md_lines.append("")

        # 结果摘要
        md_lines.append("## 结果摘要")
        md_lines.append("")
        summary = data.get('summary', {})
        md_lines.append("```json")
        md_lines.append(json.dumps(summary, indent=2, ensure_ascii=False))
        md_lines.append("```")
        md_lines.append("")

        # 页脚
        md_lines.append("---")
        md_lines.append("")
        md_lines.append(f"*由 WebGIS AI Agent 自动生成 · {data['generated_at']}*")

        return "\n".join(md_lines)

__all__ = ["ReportService"]
