"use client";

/**
 * 报告预览组件
 * 用于在 iframe 中预览 HTML 格式的报告，或提供其他格式的下载链接
 */

import { useState, useEffect } from "react";
import { FileText, ExternalLink, Loader2 } from "lucide-react";
import type { ReportInfo } from "@/lib/types/report";
import { getReportDownloadUrl, getSharedReportUrl } from "@/lib/api/report";
import { apiFetchBlob } from "@/lib/api/transport";
import { toApiPath } from "@/lib/api/first-party";
import { downloadWithAuth } from "@/lib/api/authenticated-download";
import { devOnly } from "@/lib/utils/logger";

interface ReportPreviewProps {
  report: ReportInfo | null;
  shareCode?: string;
}

export function ReportPreview({ report, shareCode }: ReportPreviewProps) {
  const [loading, setLoading] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    const cleanup = () => {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    };

    if (shareCode) {
      // 分享链路走公共通道（无鉴权），iframe 可直接加载。
      setPreviewUrl(getSharedReportUrl(shareCode));
    } else if (report && report.status === "completed") {
      if (report.format === "html") {
        // #515: /api/v1/reports/{id}/download 只认 Bearer，iframe 的
        // 原生请求无法携带 header → 恒 401 白屏。先经 transport 取
        // blob（含鉴权/401 刷新），再以 objectURL 喂给 iframe。
        // buildRequest 会前置 API_BASE —— 必须传 origin-relative path，
        // 绝对 URL 会双前缀（API_BASE + 绝对 URL）导致 404/白屏。
        setLoading(true);
        apiFetchBlob(toApiPath(getReportDownloadUrl(report.id)))
          .then(({ blob }) => {
            if (cancelled) return;
            objectUrl = URL.createObjectURL(blob);
            setPreviewUrl(objectUrl);
          })
          .catch((err) => {
            devOnly.warn('[ReportPreview] 报告预览加载失败:', err);
            if (!cancelled) setPreviewUrl(null);
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      } else {
        setPreviewUrl(null);
      }
    } else {
      setPreviewUrl(null);
    }
    return () => {
      cancelled = true;
      cleanup();
    };
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
          onClick={(e) => {
            // #515: 下载端点只认 Bearer，裸 <a> 导航无法携带 → 401。
            e.preventDefault();
            downloadWithAuth(getReportDownloadUrl(report.id)).catch((err) => {
              devOnly.warn('[ReportPreview] 报告下载失败:', err);
            });
          }}
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
