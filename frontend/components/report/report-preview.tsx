"use client";

/**
 * 报告预览组件
 * 用于在 iframe 中预览 HTML 格式的报告，或提供其他格式的下载链接
 */

import { useState, useEffect } from "react";
import { FileText, ExternalLink, Loader2 } from "lucide-react";
import type { ReportInfo } from "@/lib/types/report";
import { getReportDownloadUrl, getSharedReportUrl } from "@/lib/api/report";

interface ReportPreviewProps {
  report: ReportInfo | null;
  shareCode?: string;
}

export function ReportPreview({ report, shareCode }: ReportPreviewProps) {
  const [loading, setLoading] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (shareCode) {
      setPreviewUrl(getSharedReportUrl(shareCode));
    } else if (report && report.status === "completed") {
      if (report.format === "html") {
        setPreviewUrl(getReportDownloadUrl(report.id));
      } else {
        setPreviewUrl(null);
      }
    } else {
      setPreviewUrl(null);
    }
  }, [report, shareCode]);

  if (!report && !shareCode) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
        <FileText className="h-12 w-12 mb-3 opacity-50" />
        <p className="text-sm font-medium">暂无报告</p>
        <p className="text-xs mt-1">生成报告后将在此处预览</p>
      </div>
    );
  }

  if (report && report.status !== "completed") {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mb-3" />
        <p className="text-sm">报告生成中...</p>
      </div>
    );
  }

  if (report && report.format !== "html") {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
        <FileText className="h-12 w-12 mb-3" />
        <p className="text-sm font-medium">报告已生成</p>
        <p className="text-xs mt-2">当前格式不支持在线预览</p>
        <a
          href={getReportDownloadUrl(report.id)}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 flex items-center gap-2 text-primary hover:underline text-sm"
        >
          <ExternalLink className="h-4 w-4" />
          下载查看
        </a>
      </div>
    );
  }

  if (!previewUrl) {
    return null;
  }

  return (
    <div className="relative h-full">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      )}
      <iframe
        src={previewUrl}
        className="w-full h-full border-0"
        onLoad={() => setLoading(false)}
        onLoadStart={() => setLoading(true)}
        title="报告预览"
      />
    </div>
  );
}
