'use client';

import React from 'react';
import { Pause, Play, SkipBack, SkipForward } from 'lucide-react';

/**
 * 章节 scrubber（ADR-0147）：拖动定位 + 键盘步进。
 *
 * 原生 input[type=range] 承载键盘语义（←/→ 步进、Home/End 跳头尾），
 * aria-valuetext 报告章节标题；章节点位渲染为可点击刻度。
 */
export function ChapterScrubber({
  total,
  activeIndex,
  titles,
  playing,
  onSeek,
  onTogglePlay,
  onPrev,
  onNext,
}: {
  total: number;
  activeIndex: number;
  titles: string[];
  playing: boolean;
  onSeek: (index: number) => void;
  onTogglePlay: () => void;
  onPrev: () => void;
  onNext: () => void;
}): React.ReactElement {
  const safeIndex = Math.min(Math.max(0, activeIndex), Math.max(0, total - 1));
  return (
    <div className="flex items-center gap-2 px-1 py-2" data-testid="story-scrubber">
      <button
        type="button"
        aria-label="上一章节"
        onClick={onPrev}
        disabled={total === 0 || safeIndex === 0}
        className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink disabled:opacity-40"
      >
        <SkipBack size={13} aria-hidden />
      </button>
      <button
        type="button"
        aria-label={playing ? '暂停' : '播放'}
        title={playing ? '暂停播放' : '播放故事'}
        onClick={onTogglePlay}
        disabled={total === 0}
        className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-info disabled:opacity-40"
      >
        {playing ? <Pause size={13} aria-hidden /> : <Play size={13} aria-hidden />}
      </button>
      <button
        type="button"
        aria-label="下一章节"
        onClick={onNext}
        disabled={total === 0 || safeIndex >= total - 1}
        className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink disabled:opacity-40"
      >
        <SkipForward size={13} aria-hidden />
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(0, total - 1)}
        step={1}
        value={safeIndex}
        onChange={(e) => onSeek(Number(e.target.value))}
        aria-label="章节进度"
        aria-valuetext={titles[safeIndex] ?? undefined}
        className="h-1 w-full accent-[var(--agent-accent)]"
        data-testid="story-scrubber-range"
      />
      <span className="w-14 shrink-0 text-right font-mono text-caption text-ink-muted" aria-hidden>
        {total === 0 ? '0/0' : `${safeIndex + 1}/${total}`}
      </span>
    </div>
  );
}
