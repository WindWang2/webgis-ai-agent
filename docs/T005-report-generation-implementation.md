# T005 报告生成与预览功能 - 实现文档
## 已实现功能
### 1. 后端API接口
**路由前缀:** `/api/v1/reports`
| 接口 | 方法 | 功能 |
|------|------|------|
| `/generate` | POST | 生成空间分析报告 |
| `/{report_id}` | GET | 获取报告状态和信息 |
| `/{report_id}/download` | GET | 下载生成好的报告文件 |
### 2. 核心能力
- ✅ 支持两种导出格式: PDF / HTML
- ✅ 自动生成包含以下内容的报告:
  - 任务基本信息（ID、类型、创建时间、处理时长）
  - 分析参数配置
  - 结果统计数据（可视化卡片展示）
  - 结果摘要JSON
  - 页脚版权信息
- ✅ 与现有任务系统集成，支持从已完成任务生成报告
- ✅ 错误处理和状态查询
### 3. 技术实现
- 报告生成服务: `app/services/report_service.py`
- API路由: `app/api/routes/report.py`
- PDF生成: WeasyPrint (HTML → PDF)
- 模板引擎: Jinja2
### 4. 集成到对话流程
- 更新了AI聊天回复，支持报告生成相关咨询
- 当用户询问"生成报告"、"导出结果"时，自动返回使用说明
- 帮助菜单中已添加报告生成功能说明
## 前端集成指南
### 安装依赖
```bash
npm install axios
```
### 示例React组件
```tsx
import { useState } from 'react';
import axios from 'axios';
interface ReportButtonProps {
  taskId: number;
}
export const ReportGenerator: React.FC<ReportButtonProps> = ({ taskId }) => {
  const [loading, setLoading] = useState(false);
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [format, setFormat] = useState<'pdf' | 'html'>('pdf');
  
  const generateReport = async () => {
    try {
      setLoading(true);
      const response = await axios.post('/api/v1/reports/generate', {
        task_id: taskId,
        format: format,
        include_map_screenshot: true
      });
      
      const { report_id, download_url } = response.data.data;
      
      // 轮询等待报告生成完成（可选）
      let status = 'pending';
      while (status === 'pending') {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const statusRes = await axios.get(`/api/v1/reports/${report_id}`);
        status = statusRes.data.data.status;
        
        if (status === 'completed') {
          setReportUrl(statusRes.data.data.download_url);
        }
      }
      
    } catch (error) {
      console.error('报告生成失败:', error);
      alert('报告生成失败，请重试');
    } finally {
      setLoading(false);
    }
  };
  
  return (
    <div className="report-generator">
      <select 
        value={format} 
        onChange={(e) => setFormat(e.target.value as 'pdf' | 'html')}
        disabled={loading}
      >
        <option value="pdf">PDF 格式</option>
        <option value="html">HTML 格式</option>
      </select>
      
      <button 
        onClick={generateReport}
        disabled={loading}
        className="bg-blue-600 text-white px-4 py-2 rounded"
      >
        {loading ? '生成中...' : '📄 生成报告'}
      </button>
      
      {reportUrl && (
        <a 
          href={reportUrl} 
          target="_blank" 
          rel="noopener noreferrer"
          className="ml-2 text-blue-600 underline"
        >
          下载报告
        </a>
      )}
    </div>
  );
};
```
### Chat界面集成
在分析任务完成的消息中添加报告生成按钮:
```tsx
// 在Chat消息组件中，当消息是分析完成类型时
if (message.type === 'analysis_complete') {
  return (
    <div className="ai-message">
      <div className="message-content">
        <p>✅ 分析已完成！</p>
        <p>任务ID: {message.task_id}</p>
        <ReportGenerator taskId={message.task_id} />
      </div>
    </div>
  );
}
```
## 部署说明
1. 安装新依赖: `pip install -r requirements.txt`
2. 重启后端服务
3. 前端集成上述组件
4. 测试报告生成功能
## 扩展功能
- 支持自定义报告模板
- 支持添加自定义水印、logo
- 支持更多导出格式（Word、Excel）
- 支持邮件发送报告
