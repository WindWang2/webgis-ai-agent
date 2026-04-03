# T005 报告功能前端集成指南
## 后端API使用指南

### 1. 报告生成接口
**POST** `/api/v1/reports/generate`
```json
{
  "task_id": 123,
  "format": "pdf", // 支持: pdf/html/markdown/md
  "include_map_screenshot": true
}
```
**返回**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "report_id": "a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "format": "pdf",
    "download_url": "/api/v1/reports/a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx/download",
    "status": "completed"
  }
}
```

### 2. 获取报告状态
**GET** `/api/v1/reports/{report_id}`

### 3. 下载报告
**GET** `/api/v1/reports/{report_id}/download`

### 4. 创建分享链接
**POST** `/api/v1/reports/{report_id}/share?ttl_days=7`
**返回**:
```json
{
  "code": 0,
  "data": {
    "share_code": "abc123XYZ456",
    "share_url": "/api/v1/reports/shared/abc123XYZ456",
    "expire_at": 1744444800,
    "ttl_days": 7
  }
}
```

### 5. 访问分享报告
**GET** `/api/v1/reports/shared/{share_code}`
（无需登录即可访问，HTML格式直接预览，其他格式自动下载）

---

## React 集成示例组件
### ReportGeneratorButton.tsx
```tsx
import { useState } from 'react';
import axios from 'axios';

interface ReportGeneratorProps {
  taskId: number;
  onSuccess?: (reportInfo: any) => void;
}

export const ReportGenerator: React.FC<ReportGeneratorProps> = ({ taskId, onSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<any>(null);
  const [format, setFormat] = useState<'pdf' | 'html' | 'markdown'>('pdf');
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  // 生成报告
  const generateReport = async () => {
    try {
      setLoading(true);
      const response = await axios.post('/api/v1/reports/generate', {
        task_id: taskId,
        format: format,
        include_map_screenshot: true
      });
      
      const reportData = response.data.data;
      setReport(reportData);
      
      if (onSuccess) onSuccess(reportData);
      
    } catch (error) {
      console.error('报告生成失败:', error);
      alert('报告生成失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  // 生成分享链接
  const generateShareLink = async () => {
    if (!report) return;
    
    try {
      const response = await axios.post(`/api/v1/reports/${report.report_id}/share`, {
        ttl_days: 7
      });
      
      const { share_url } = response.data.data;
      setShareUrl(window.location.origin + share_url);
      
      // 复制到剪贴板
      await navigator.clipboard.writeText(shareUrl);
      alert('分享链接已复制到剪贴板');
      
    } catch (error) {
      console.error('生成分享链接失败:', error);
      alert('生成分享链接失败');
    }
  };

  return (
    <div className="report-generator p-4 bg-white rounded-lg shadow">
      <h3 className="text-lg font-semibold mb-4">📄 生成分析报告</h3>
      
      <div className="flex items-center gap-3 mb-4">
        <label className="text-sm font-medium">导出格式:</label>
        <select 
          value={format} 
          onChange={(e) => setFormat(e.target.value as any)}
          disabled={loading}
          className="border rounded px-3 py-1.5 text-sm"
        >
          <option value="pdf">PDF 文档</option>
          <option value="html">HTML 网页</option>
          <option value="markdown">Markdown 格式</option>
        </select>
      </div>

      <div className="flex gap-2">
        <button 
          onClick={generateReport}
          disabled={loading}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '生成中...' : '生成报告'}
        </button>

        {report && (
          <a 
            href={report.download_url} 
            target="_blank" 
            rel="noopener noreferrer"
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
          >
            下载报告
          </a>
        )}

        {report && (
          <button 
            onClick={generateShareLink}
            className="bg-purple-600 text-white px-4 py-2 rounded hover:bg-purple-700"
          >
            生成分享链接
          </button>
        )}
      </div>

      {shareUrl && (
        <div className="mt-3 p-2 bg-gray-50 border rounded">
          <p className="text-sm text-gray-600 mb-1">分享链接 (7天有效):</p>
          <a href={shareUrl} target="_blank" rel="noopener noreferrer" className="text-blue-600 text-sm underline">
            {shareUrl}
          </a>
        </div>
      )}
    </div>
  );
};
```

### 对话消息集成示例
在分析完成的消息卡片中添加报告生成按钮：
```tsx
// ChatMessage.tsx
if (message.type === 'analysis_complete') {
  return (
    <div className="ai-message p-4 rounded-lg bg-blue-50">
      <div className="mb-2">
        <p className="font-medium text-green-600">✅ 空间分析已完成！</p>
        <p className="text-sm text-gray-600">任务ID: {message.task_id}</p>
        <p className="text-sm text-gray-600">处理时长: {message.duration}秒</p>
      </div>
      
      <ReportGenerator taskId={message.task_id} />
    </div>
  );
}
```

---

## 报告预览页面实现建议
### 路由: `/reports/[id]`
页面功能：
1. 加载报告详情
2. 支持在线预览（HTML直接渲染，PDF使用viewer.js，Markdown使用markdown-it渲染）
3. 包含地图截图展示区域
4. 分析结果可视化卡片
5. 数据表格展示
6. 下载和分享按钮

---

## 已实现功能总结
✅ 后端报告生成API (PDF/HTML/Markdown)
✅ 报告状态查询和下载
✅ 报告分享链接生成（有效期7天）
✅ 公开分享访问接口
✅ 完整的错误处理和参数验证
✅ 与现有任务系统无缝集成
