# T005 报告生成与预览功能 - 延展实现计划
## 任务概述
基于已有的报告生成后端服务，继续完成：
1. 报告导出增加 Markdown 格式支持
2. 开发报告预览界面（前端）
3. 实现报告分享链接生成功能  
4. 集成到现有对话系统和地图组件
5. 提交PR到develop分支

## 当前已实现功能回顾
- ✅ 后端API：`/api/v1/reports/generate`、`/api/v1/reports/{report_id}/download`
- ✅ PDF/HTML格式生成（WeasyPrint + Jinja2）
- ✅ 基本报告内容结构

## 任务拆分

### T005-E1: Markdown 格式导出支持
**预估时间**: 30分钟
**验收标准**: POST /api/v1/reports/generate 支持 format=markdown，返回 .md 文件下载
```
Backend改动:
1. report_service.py: 增加 markdown_render() 方法
2. report. py: 在 format 判断中增加 "markdown"
```

### T005-E2: 报告预览前端界面
**预估时间**: 2小时
**验收标准**: 
- 访问 /reports/[report_id] 显示报告预览页面
- 支持 PDF/HTML/Markdown 三种格式渲染
- 包含地图截图、分析结果、数据表格展示区域

**前端改动:
1. 创建 pages/reports/[id].tsx 预览页面
2. 组件: MapScreenshotViewer, AnalysisResultCard, DataTableView
3. 添加全局样式或使用Tailwind
```

### T005-E3: 报告分享链接功能
**预估时间**: 1小时
**验收标准**: 
- 预览页面点击"生成分享链接"按钮
- 返回可访问的公开链接（UUID-based）
- 无需登录即可查看报告

**后端改动:
1. 增加 /api/v1/reports/{report_id}/share POST接口
2. 创建分享码，存储到Redis，TTL 7天
3. 公开访问路由 /shared/reports/{share_code}
```

### T005-E4: 对话系统集成
**预估时间**: 1.5小时
**验收标准**: 
- 分析完成消息显示"生成报告"按钮
- 生成完成推送下载链接

**待确认: 是否有现有对话UI组件？**

### T005-E5: 最终提交PR
**预估时间**: 30分钟
```
1. 确认所有测试通过
2. 提交 commit: feat(T005): add report preview & sharing
3. 创建 PR 到 develop 分支
```

## 技术实现细节

### E1: Markdown支持
```python
# report_service.py 增加方法
def generate_markdown(self, task, report_data):
    """生成Markdown格式报告"""
    md_content = f"# {report_data['report_title']}\n\n"
    md_content += f"**任务ID**: {report_data['task_info']['id']}\n\n"
    md_content += f"**分析类型**: {report_data['task_info']['type']}\n\n"
    # ... 其他内容
    return md_content

# report. py 的 generate_report 修改 format 判断
elif format == "markdown":
    md_content = report_svc.generate_markdown(task, report_data)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)
```

### E2: 前端预览页面
```tsx
// app/reports/[id]/page.tsx
import { GetReportData } from '@/app/models/schema'

export default async function ReportPreview({ params }) {
  const reportData = await fetch(`/api/v1/reports/${params.id}`).then(r => r.json())
  
  return (
    <div className="report-preview">
      <Header />
      <MapSection screenshotUrl={reportData.mapScreenshot} />
      <ResultsSection data={reportData.summary} />
      <DataTable rows={reportData.dataTable} />
      <ShareButton reportId={params.id} />
    </div>
  )
}
```

### E3: 分享链接
```python
# redis key: "report_share:{share_code}" -> report_file_path
# TTL: 7 * 24 * 3600

@router.post("/{report_id}/share")
async def create_share_link(report_id: str):
    share_code = generate_uuid()
    redis.setex(f"report_share:{share_code}", 7*24*3600, report_id)
    return {"share_url": f"/shared/reports/{share_code}"}

@app.get("/shared/reports/{share_code}")
async def public_report_view(share_code: str):
    report_id = redis.get(f"report_share:{share_code}")
    # 返回报告预览页面
```

## 执行顺序
E1 → E2 → E3 → E4 → E5

## 依赖
- Redis (分享链接TTL)
- WeasyPrint (PDF生成，已有)

---
**创建时间**: 2026-04-04
**状态**: 待开始