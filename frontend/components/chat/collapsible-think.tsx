'use client';

import { useId, useState } from 'react';
import { ChevronDown, ChevronRight, Brain } from 'lucide-react';

interface CollapsibleThinkProps {
  content: string;
}

export function CollapsibleThink({ content }: CollapsibleThinkProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  // 审计 findings.md a11y Low：toggle 缺 aria-expanded + aria-controls 关联。
  // V4 修复：原先是常量 'think-content' —— 一屏多条消息时 id 重复，
  // aria-controls 指向的是第一条，关联即失效。useId 保证每实例唯一。
  // 必须在早返回之前调用（hooks 顺序规则）。
  const panelId = useId();

  if (!content) return null;

  return (
    <div className="mb-2">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        aria-expanded={isExpanded}
        aria-controls={panelId}
        className="flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink-secondary"
      >
        {isExpanded ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
        <Brain size={12} className="text-status-accent" aria-hidden />
        <span className="eyebrow">思考过程</span>
      </button>

      {isExpanded && (
        <div
          id={panelId}
          className="mt-1 rounded-sm border-l-2 border-l-status-accent-border bg-surface-sunken px-2 py-1.5 text-meta italic text-ink-secondary"
        >
          <div className="whitespace-pre-wrap">{content}</div>
        </div>
      )}
    </div>
  );
}
