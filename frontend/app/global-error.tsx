'use client';

/**
 * audit ISSUE-031（#1348）：根级错误兜底。
 * root layout 自身抛错时 Next 丢弃整个文档树——此文件必须自带
 * html/body。样式用内联最小集（globals 可能已不可达）。
 */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          fontFamily: 'system-ui, sans-serif',
          background: '#0f1524',
          color: '#e6eaf2',
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 600 }}>应用遇到严重错误</div>
        <button
          type="button"
          onClick={reset}
          style={{
            padding: '6px 14px',
            borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.2)',
            background: 'transparent',
            color: 'inherit',
            cursor: 'pointer',
          }}
        >
          重试
        </button>
      </body>
    </html>
  );
}
