'use client';

import React from 'react';
import { describeApiError } from '@/lib/api/transport';
import { useToastStore } from '@/components/ui/toast';
import ReactMarkdown, { type UrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { downloadWithAuth, isProtectedDownloadUrl } from '@/lib/api/authenticated-download';
import { devOnly } from '@/lib/utils/logger';
import { AuthImage } from './auth-image';

interface MiniMdProps {
  text: string;
}

// 审计 F36：默认 urlTransform 已经过滤 javascript:，但 data: 在某些浏览器
// 仍可执行（image/svg+xml）。显式白名单只允许 http/https/mailto/相对路径。
// FE-06：导出供其它 ReactMarkdown 消费者复用（如 sidebar/chat-tab 的
// MiniMd），避免第二个 ReactMarkdown 实例绕过防护。
export const safeUrlTransform: UrlTransform = (url) => {
  if (!url) return url;
  const trimmed = url.trim();
  // 相对路径 / 同源锚点 / 标准协议放行
  if (
    trimmed.startsWith('/') ||
    trimmed.startsWith('#') ||
    trimmed.startsWith('http://') ||
    trimmed.startsWith('https://') ||
    trimmed.startsWith('mailto:') ||
    trimmed.startsWith('tel:')
  ) {
    return trimmed;
  }
  // 其他（javascript:, data:, file:, vbscript: 等）一律拒绝
  return '';
};

export default function MiniMd({ text }: MiniMdProps) {
  return (
    <div className="prose-agent text-body leading-[1.7] text-ink-secondary max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrlTransform}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
          // 可访问性（V4）：聊天气泡里的 markdown 标题从 h4 起跳（h1→h4、h2→h5、h3→h6），
          // 避免把 h1/h2 注入页面大纲 —— 应用自身的顶层标题就是 h2。视觉字号不变。
          h1: ({ children }) => (
            <h4 className="text-heading font-bold text-ink mt-3 mb-1.5 first:mt-0">{children}</h4>
          ),
          h2: ({ children }) => (
            <h5 className="text-title font-semibold text-ink mt-2.5 mb-1 first:mt-0">{children}</h5>
          ),
          h3: ({ children }) => (
            <h6 className="text-body font-semibold text-ink-secondary mt-2 mb-1 first:mt-0">{children}</h6>
          ),
          ul: ({ children }) => <ul className="list-disc list-outside ml-4 mb-2 space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-outside ml-4 mb-2 space-y-0.5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-status-accent-border pl-3 my-2 text-ink-muted italic">
              {children}
            </blockquote>
          ),
          code: ({ children, className }) => {
            const isBlock = className?.includes('language-');
            if (isBlock) {
              return (
                <pre className="my-2 p-3 bg-surface-sunken border border-edge-subtle rounded-md overflow-x-auto text-body leading-relaxed">
                  <code>{children}</code>
                </pre>
              );
            }
            return (
              <code className="rounded-sm bg-status-accent-soft px-1 py-0.5 font-mono text-body text-status-accent">
                {children}
              </code>
            );
          },
          a: ({ href, children }) => {
            // 审计 F36：纵然顶层 urlTransform={safeUrlTransform} 已过滤，
            // 这里再显式应用一次作为纵深防御 —— 避免未来 ReactMarkdown
            // 版本变更 component props 传递顺序时绕过过滤。
            const safeHref = safeUrlTransform(href ?? '', 'href', {
              type: 'element',
              tagName: 'a',
              properties: {},
              children: [],
            });
            // #515: 导出/报告下载链接是受保护路由，裸 <a target=_blank>
            // 打开的新标签页无法携带 Bearer → 恒 401。拦截点击，改走
            // transport 的鉴权 blob 下载。
            const protectedHref = typeof safeHref === 'string' && isProtectedDownloadUrl(safeHref)
              ? safeHref
              : null;
            if (protectedHref) {
              return (
                <a
                  href={safeHref ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-status-accent underline hover:text-status-accent"
                  onClick={(e) => {
                    e.preventDefault();
                    downloadWithAuth(protectedHref).catch((err) => {
                      // #738: surface auth/network download failures.
                      devOnly.warn('[MiniMd] 鉴权下载失败:', err);
                      useToastStore.getState().addToast(
                        `下载失败：${describeApiError(err, '网络错误或链接已失效')}`,
                        'error',
                      );
                    });
                  }}
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={safeHref ?? undefined} target="_blank" rel="noopener noreferrer" className="text-status-accent underline hover:text-status-accent">
                {children}
              </a>
            );
          },
          img: ({ src, alt }) => (
            <AuthImage
              src={typeof src === 'string' ? src : ''}
              alt={alt ?? ''}
              className="max-w-full h-auto my-2 rounded-md"
            />
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2 rounded-md border border-edge-subtle">
              <table className="w-full text-body">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="px-2.5 py-1.5 bg-status-accent-soft text-left font-semibold text-ink-secondary border-b border-edge-subtle">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-2.5 py-1.5 border-b border-edge-subtle text-ink-secondary">{children}</td>
          ),
          hr: () => <hr className="my-3 border-edge-subtle" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
