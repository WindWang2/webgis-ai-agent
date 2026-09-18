'use client';

/**
 * audit ISSUE-031（#1348）：路由级错误兜底。
 * 组件级 ErrorBoundary 已覆盖面板/地图，此文件兜底 layout/page 层级
 * 未被边界捕获的渲染异常——reset() 局部重挂载，不丢会话状态。
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="text-title font-medium text-ink">页面遇到错误</div>
      <div className="max-w-[360px] text-body text-ink-muted">
        {error?.message ?? '渲染异常'}
        {error?.digest ? ` (${error.digest})` : ''}
      </div>
      <button
        type="button"
        onClick={reset}
        className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-1.5 text-body font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
      >
        重试
      </button>
    </div>
  );
}
