# T005 报告生成与预览功能 - 设计文档
## 任务概述
实现WebGIS AI Agent的报告生成与预览功能，支持用户完成空间分析后一键生成分析报告
## 技术栈
- 前端: Next.14 + TypeScript  
- 后端: FastAPI
- PDF生成: weasyprint / pdfkit
- HTML模板: Jinja2

## 功能拆解

### 1. 后端API设计
```
POST /api/v1/reports/generate
  - task_id: 分析任务ID
  - format: pdf/html
  - include_map_screenshot: bool
  - 返回: report_id + download_url
  
GET /api/v1/reports/{report_id}
  - 返回报告详情和状态
  
GET /api/v1/reports/{report_id}/download
  - 返回文件流
```

### 2. 报告内容结构
- 报告标题和分析类型
- 分析参数配置  
- 结果摘要（统计数据）
- 地图截图（可选）
- 详细结果表格
- 时间戳和处理时长

### 3. 前端界面
- 报告预览弹窗/页面
- 格式化选择（PDF/HTML）
- 一键下载按钮
- 嵌入在Chat面板的消息流中

### 4. 与Chat集成
- 分析完成后显示"生成报告"按钮
- 点击触发报告生成API
- 生成完毕推送通知并提供下载链接

## 实施计划
1. 创建报告生成服务 (backend)
2. 新增Report API路由
3. 创建前端报告组件和页面
4. 集成到Chat流程
5. 测试验证