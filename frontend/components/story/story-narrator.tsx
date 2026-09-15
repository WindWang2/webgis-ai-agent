'use client';

import React, { useCallback, useEffect, useRef } from 'react';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';

/**
 * StoryNarrator —— 滚动驱动（Scroll-driven）叙事列（ADR-0196）。
 *
 * 契约：本组件只负责「滚动位置 → 活跃章节」的判定与呈现；相机漫游由父层
 * 响应 onActiveChange 后经 map-action 通道派发（fly_to 全参）。
 * 防回环：外部 activeId 变更（scrubber/播放）触发程序化滚动后，在
 * scrollLockMs 窗口内忽略滚动驱动；滚动驱动的变更自身不加锁。
 */

export interface NarratorCamera {
  center: [number, number];
  zoom?: number;
  pitch?: number;
  bearing?: number;
}

export interface NarratorChapter {
  id: string;
  title: string;
  /** markdown 正文（默认纯文本渲染；复杂渲染经 renderBody 注入） */
  text: string;
  arcRole?: string;
  camera?: NarratorCamera;
  widgetIds?: string[];
  durationHintS?: number;
}

export interface StoryNarratorProps {
  chapters: NarratorChapter[];
  activeId: string | null;
  onActiveChange: (id: string) => void;
  /** 程序化滚动后的滚动驱动锁窗口（ms）。 */
  scrollLockMs?: number;
  /** 自定义正文渲染（如 StoryMarkdown）。 */
  renderBody?: (chapter: NarratorChapter, index: number) => React.ReactNode;
  className?: string;
}

/** 活跃章判定纯函数：最后一个越过激活线（容器高 40%）的章节下标。 */
export function pickActiveChapter(tops: number[], baseline: number): number {
  let idx = -1;
  for (let i = 0; i < tops.length; i += 1) {
    if (tops[i] <= baseline) idx = i;
  }
  return idx;
}

export function StoryNarrator({
  chapters,
  activeId,
  onActiveChange,
  scrollLockMs = 600,
  renderBody,
  className,
}: StoryNarratorProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const lockUntilRef = useRef(0);
  const rafRef = useRef(0);
  // 上一次外部（非滚动驱动）activeId；用于区分变更来源（锁只对外部变更生效）
  const externalIdRef = useRef<string | null>(activeId);
  // 滚动驱动变更的来源标记：effect 见到它就跳过加锁（防快速连续滚动被吞）
  const scrollDrivenRef = useRef(false);
  const mountedRef = useRef(false);
  const reducedMotion = usePrefersReducedMotion();

  const measure = useCallback((): string | null => {
    const container = containerRef.current;
    if (!container || chapters.length === 0) return null;
    const articles = Array.from(
      container.querySelectorAll<HTMLElement>('[data-story-chapter]'),
    );
    if (articles.length === 0) return null;
    const baseline =
      container.getBoundingClientRect().top + container.getBoundingClientRect().height * 0.4;
    const idx = pickActiveChapter(
      articles.map((n) => n.getBoundingClientRect().top),
      baseline,
    );
    if (idx < 0) return null;
    return articles[idx]?.getAttribute('data-story-chapter') ?? null;
  }, [chapters]);

  const handleScroll = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      if (Date.now() < lockUntilRef.current) return;
      const id = measure();
      if (id && id !== externalIdRef.current) {
        scrollDrivenRef.current = true;
        onActiveChange(id);
      }
    });
  }, [measure, onActiveChange]);

  // 外部 activeId 变更（scrubber / 播放 / 初始化）→ 程序化滚动 + 驱动锁；
  // 滚动驱动的变更（scrollDrivenRef 标记）不加锁，快速连续滚动不被吞。
  useEffect(() => {
    const isExternal = activeId !== externalIdRef.current;
    externalIdRef.current = activeId;
    const fromScroll = scrollDrivenRef.current;
    scrollDrivenRef.current = false;
    if (!activeId) return;
    if (!mountedRef.current) {
      mountedRef.current = true;
    } else if (isExternal && !fromScroll) {
      lockUntilRef.current = Date.now() + scrollLockMs;
    }
    const node = Array.from(
      containerRef.current?.querySelectorAll<HTMLElement>('[data-story-chapter]') ?? [],
    ).find((n) => n.getAttribute('data-story-chapter') === activeId);
    node?.scrollIntoView?.({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  }, [activeId, reducedMotion, scrollLockMs]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      data-story-narrator=""
      onScroll={handleScroll}
      className={`overflow-y-auto overflow-x-hidden flex-1 ${className ?? ''}`}
    >
      <div className="p-8 pb-32 flex flex-col gap-12">
        {chapters.map((ch, index) => {
          const isActive = ch.id === activeId;
          return (
            <article
              key={ch.id}
              data-story-chapter={ch.id}
              data-story-active={isActive ? 'true' : undefined}
              className={`prose prose-agent prose-headings:text-status-info prose-a:text-status-info max-w-none transition-opacity duration-700
                ${isActive ? 'story-message-active' : 'story-message-idle opacity-80'}`}
            >
              {ch.title ? (
                <p className="text-meta font-medium tracking-wide text-ink-muted uppercase">
                  ◈ {index + 1}. {ch.title}
                  {ch.durationHintS ? (
                    <span className="ml-2 normal-case text-ink-disabled">
                      · {Math.round(ch.durationHintS)}s
                    </span>
                  ) : null}
                </p>
              ) : null}
              {renderBody ? (
                renderBody(ch, index)
              ) : (
                <div className="narrative whitespace-pre-wrap text-body text-ink-secondary">
                  {ch.text}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
