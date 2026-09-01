'use client';

import React, { useId, useState, useEffect } from 'react';
import { ChevronRight, Brain, Clock, Loader2, Sparkles } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';

export interface CollapsibleThinkProps {
  /** 思考过程正文 */
  content: string;
  /** 是否正在流式生成或处于思考中 */
  isStreaming?: boolean;
  /** 是否处于思考状态 */
  isThinking?: boolean;
  /** 思考耗时 (毫秒) */
  durationMs?: number;
  /** 思考 token 数量 */
  tokenCount?: number;
  /** 默认是否展开 */
  defaultExpanded?: boolean;
}

function formatThinkDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Collapsible reasoning trail (<think> blocks) with smooth accordion animation,
 * live active pulsing indicator, duration/token counter, and subtle surface styling.
 */
export function CollapsibleThink({
  content,
  isStreaming = false,
  isThinking = false,
  durationMs,
  tokenCount,
  defaultExpanded = false,
}: CollapsibleThinkProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [userToggled, setUserToggled] = useState(false);
  const panelId = useId();

  const active = isStreaming || isThinking;

  // Auto-expand on active streaming unless user explicitly toggled it
  useEffect(() => {
    if (active && !userToggled) {
      setIsExpanded(true);
    }
  }, [active, userToggled]);

  if (!content && !active) return null;

  const toggle = () => {
    setUserToggled(true);
    setIsExpanded((prev) => !prev);
  };

  return (
    <div className="mb-2.5" data-testid="collapsible-think">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={isExpanded}
        aria-controls={panelId}
        className={`flex items-center gap-1.5 rounded-sm px-2 py-1 text-caption font-medium transition-all cursor-pointer ${
          active
            ? 'bg-status-accent-soft text-status-accent border border-status-accent-border/50'
            : 'text-ink-muted hover:bg-surface-hover hover:text-ink-secondary border border-transparent'
        }`}
        data-testid="think-toggle-button"
      >
        <ChevronRight
          size={12}
          className={`shrink-0 transition-transform duration-200 ${
            isExpanded ? 'rotate-90 text-ink-secondary' : 'text-ink-disabled'
          }`}
          aria-hidden
        />

        {active ? (
          <Loader2 size={12} className="text-status-accent animate-spin shrink-0" aria-hidden />
        ) : (
          <Brain size={12} className="text-status-accent shrink-0" aria-hidden />
        )}

        <span className="font-medium">
          {active ? '深度思考中...' : '思考过程'}
        </span>

        {/* Elapsed duration badge */}
        {durationMs !== undefined && durationMs > 0 && !active && (
          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-micro bg-surface-sunken text-ink-muted font-mono ml-1">
            <Clock size={9} aria-hidden />
            {formatThinkDuration(durationMs)}
          </span>
        )}

        {/* Token count badge */}
        {tokenCount !== undefined && tokenCount > 0 && !active && (
          <span className="px-1.5 py-0.5 rounded text-micro bg-surface-sunken text-ink-muted font-mono ml-0.5">
            {tokenCount} tokens
          </span>
        )}

        {active && (
          <span className="inline-flex items-center gap-1 text-micro text-status-accent animate-pulse font-mono ml-1">
            <Sparkles size={10} aria-hidden />
            思考中
          </span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            id={panelId}
            role="region"
            aria-label="思考过程详情"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="mt-1.5 rounded-md border border-edge-subtle border-l-2 border-l-status-accent bg-surface-sunken/60 px-3 py-2 text-meta text-ink-secondary leading-relaxed font-sans shadow-sm">
              <div className="whitespace-pre-wrap">{content || '正在生成思考链...'}</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default CollapsibleThink;
