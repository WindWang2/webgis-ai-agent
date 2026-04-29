'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MiniMdProps {
  text: string;
}

export default function MiniMd({ text }: MiniMdProps) {
  return (
    <div className="prose-agent text-[12.5px] leading-[1.7] text-slate-600 max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
          h1: ({ children }) => (
            <h1 className="text-[15px] font-bold text-slate-800 mt-3 mb-1.5 first:mt-0">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-[13.5px] font-semibold text-slate-800 mt-2.5 mb-1 first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-[12.5px] font-semibold text-slate-700 mt-2 mb-1 first:mt-0">{children}</h3>
          ),
          ul: ({ children }) => <ul className="list-disc list-outside ml-4 mb-2 space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-outside ml-4 mb-2 space-y-0.5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-green-300 pl-3 my-2 text-slate-500 italic">
              {children}
            </blockquote>
          ),
          code: ({ children, className }) => {
            const isBlock = className?.includes('language-');
            if (isBlock) {
              return (
                <pre className="my-2 p-3 bg-slate-50 border border-slate-200/80 rounded-lg overflow-x-auto text-[11.5px] leading-relaxed">
                  <code>{children}</code>
                </pre>
              );
            }
            return (
              <code className="rounded bg-green-50 px-1 py-0.5 font-mono text-[11.5px] text-green-700">
                {children}
              </code>
            );
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-green-600 underline hover:text-green-700">
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2 rounded-lg border border-slate-200/80">
              <table className="w-full text-[12px]">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="px-2.5 py-1.5 bg-green-50/50 text-left font-semibold text-slate-700 border-b border-slate-200/80">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-2.5 py-1.5 border-b border-slate-100 text-slate-600">{children}</td>
          ),
          hr: () => <hr className="my-3 border-slate-200/60" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
