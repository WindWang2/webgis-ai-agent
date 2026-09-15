'use client';

import React, { useCallback, useEffect, useRef } from 'react';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';

/**
 * StoryNarrator —— 滚动驱动（Scroll-driven）叙事列（ADR-0196）。
 *
 * 契约：本组件只负责「滚动位置 → 活跃章节」的判定与呈现；相机漫游由父层
 * 响应 onActiveChange 后经 map-action 通道派发（fly_to 全参）。
 * 防回环：外部 activeId 变更（scrubber/播放）触发程序化滚动后，在
 * scrollLockMs 窗口内滚动驱动让路；滚动驱动的变更自身不加锁（快速连续
 * 滚动不被吞）。锁窗口内的滚动事件不再丢弃 —— 记 pending，锁到期补测
 * 一次（平滑滚动长距离时窗口会溢出，旧实现从此永久失同步）。
 */

export interface NarratorCamera {
  center: [number, number];
  zoom?: number;
  pitch?: number;
  bearing?: number;
  /** 章内归一进度（取样来源关键帧的 t；视图适配层取 t 最大者）。 */
  t?: number;
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
  /** 滚动区域的无障碍名称（i18n 由父层注入，ADR-0144 禁裸 CJK）。 */
  ariaLabel?: string;
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
  scrollLockMs = 800,
  ariaLabel,
  renderBody,
  className,
}: StoryNarratorProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const lockUntilRef = useRef(0);
  const rafRef = useRef(0);
  const pendingTimerRef = useRef(0);
  // 上一次外部（非滚动驱动）activeId；用于区分变更来源（锁只对外部变更生效）
  const externalIdRef = useRef<string | null>(activeId);
  // 滚动驱动变更的来源标记：effect 见到它就跳过加锁（防快速连续滚动被吞）
  const scrollDrivenRef = useRef(false);
  const mountedRef = useRef(false);
  // 章节节点缓存（滚动帧内不做 querySelectorAll；chapters 变更时刷新）
  const articlesRef = useRef<HTMLElement[]>([]);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    articlesRef.current = Array.from(
      container.querySelectorAll<HTMLElement>('[data-story-chapter]'),
    );
  }, [chapters]);

  const measure = useCallback((): string | null => {
    const container = containerRef.current;
    if (!container || chapters.length === 0) return null;
    let articles = articlesRef.current;
    if (articles.length === 0) {
      articles = Array.from(
        container.querySelectorAll<HTMLElement>('[data-story-chapter]'),
      );
      articlesRef.current = articles;
    }
    if (articles.length === 0) return null;
    const containerRect = container.getBoundingClientRect();
    const baseline = containerRect.top + containerRect.height * 0.4;
    const idx = pickActiveChapter(
      articles.map((n) => n.getBoundingClientRect().top),
      baseline,
    );
    if (idx < 0) return null;
    return articles[idx]?.getAttribute('data-story-chapter') ?? null;
  }, [chapters]);

  const runMeasure = useCallback(() => {
    const id = measure();
    if (id && id !== externalIdRef.current) {
      scrollDrivenRef.current = true;
      onActiveChange(id);
    }
  }, [measure, onActiveChange]);
  const runMeasureRef = useRef(runMeasure);
  runMeasureRef.current = runMeasure;

  // 锁窗口内丢进的滚动 → 锁到期补测一次（单飞；防平滑滚动尾帧/用户回滚失同步）
  const schedulePending = useCallback(() => {
    if (pendingTimerRef.current) return;
    const delay = Math.max(0, lockUntilRef.current - Date.now()) + 16;
    pendingTimerRef.current = window.setTimeout(() => {
      pendingTimerRef.current = 0;
      runMeasureRef.current();
    }, delay);
  }, []);

  const handleScroll = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      if (Date.now() < lockUntilRef.current) {
        schedulePending();
        return;
      }
      runMeasureRef.current();
    });
  }, [schedulePending]);

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
    const node = articlesRef.current.find(
      (n) => n.getAttribute('data-story-chapter') === activeId,
    );
    node?.scrollIntoView?.({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  }, [activeId, reducedMotion, scrollLockMs]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (pendingTimerRef.current) window.clearTimeout(pendingTimerRef.current);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      data-story-narrator=""
      onScroll={handleScroll}
      tabIndex={0}
      role="region"
      aria-label={ariaLabel}
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
              aria-current={isActive ? 'true' : undefined}
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
