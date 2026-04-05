"use client";

/**
 * T005 报告生成组件
 * 提供报告生成、状态轮询、下载和分享功能
 */

import { useState } from "react";
import { FileText, Download, Share2, Loader2, CheckCircle, XCircle } from "lucide-react";
import type { ReportFormat, ReportInfo } from "@/lib/types/report";
import { generateReport, pollReportStatus, getReportDownloadUrl, createShareLink } from "@/lib/api/report";

interface ReportGeneratorProps {
  taskId: number | null;
  onReportGenerated?: (report: ReportInfo) => void;
}

export function ReportGenerator({ taskId, onReportGenerated }: ReportGeneratorProps) {
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ReportInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  const handleGenerateReport = async () => {
    if (!taskId) {
      setError("请先完成一项分析任务");
      return;
    }

    setLoading(true);
    setError(null);
    setReport(null);
    setShareUrl(null);

    try {
      // 1. 生成报告
      const response = await generateReport(taskId, format, true);
      const reportInfo = response.data;
      
      // 2. 轮询等待完成
      const finalReport = await pollReportStatus(reportInfo.report_id, 30, 1000);
      
      setReport(finalReport);
      
      if (finalReport.status === "completed" && onReportGenerated) {
        onReportGenerated(finalReport);
      } else if (finalReport.status === "failed") {
        setError(finalReport.error_message || "报告生成失败");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "报告生成失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  const handleShare = async () => {
    if (!report) return;

    try {
      const response = await createShareLink(report.report_id, 7);
      const { share_url } = response.data;
      const fullUrl = window.location.origin + share_url;
      
      setShareUrl(fullUrl);
      
      // 复制到剪贴板
      await navigator.clipboard.writeText(fullUrl);
      alert("分享链接已复制到剪贴板");
    } catch {
      alert("生成分享链接失败");
    }
  };

  return (
    <div className="space-y-4">
      {/* 格式选择 */}
      <div>
        <label className="text-sm font-medium text-foreground mb-2 block">
          导出格式
        </label>
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value as ReportFormat)}
          disabled={loading}
          className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
        >
          <option value="pdf">PDF 文档</option>
          <option value="html">HTML 网页</option>
          <option value="markdown">Markdown 格式</option>
        </select>
      </div>

      {/* 生成按钮 */}
      <button
        onClick={handleGenerateReport}
        disabled={loading || !taskId}
        className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-2.5 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            生成中...
          </>
        ) : (
          <>
            <FileText className="h-4 w-4" />
            生成报告
          </>
        )}
      </button>

      {/* 错误信息 */}
      {error && (
        <div className="flex items-start gap-2 p-3 bg-destructive/10 text-destructive rounded-md text-sm">
          <XCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* 成功信息和操作 */}
      {report && report.status === "completed" && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 p-3 bg-green-500/10 text-green-600 rounded-md text-sm">
            <CheckCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium">报告生成成功</p>
              {report.file_size && (
                <p className="text-xs mt-1">文件大小: {(report.file_size / 1024).toFixed(2)} KB</p>
              )}
            </div>
          </div>

          <div className="flex gap-2">
            <a
              href={getReportDownloadUrl(report.report_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 flex items-center justify-center gap-2 rounded-lg bg-green-600 text-white py-2 text-sm font-medium hover:bg-green-700 transition-colors"
            >
              <Download className="h-4 w-4" />
              下载报告
            </a>

            <button
              onClick={handleShare}
              className="flex-1 flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white py-2 text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              <Share2 className="h-4 w-4" />
              分享链接
            </button>
          </div>

          {shareUrl && (
            <div className="p-2 bg-muted rounded text-xs">
              <p className="font-medium mb-1">分享链接：</p>
              <p className="text-muted-foreground break-all">{shareUrl}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
