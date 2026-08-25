'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { LegendSpec } from '@/lib/map-kit/types';
import { ThematicLegend } from './thematic-legend';

export interface LegendStackEntry {
  id: string;
  /** 图层名(eyebrow 行显示;长 id 图层如 result-chatcmpl-* 只占一行) */
  name: string;
  legendSpec: LegendSpec;
  onFilterChange?: (ranges: number[][]) => void;
  /** 聚焦高亮(map-panel 的 focusLayerId 呼应) */
  flashing?: boolean;
}

/**
 * 专题图例栈（原 map-panel 内联块）。
 *
 * 多层同屏的收折契约（2026-08-25 用户反馈"行政区面板把内容遮盖"）：
 * 会话常有多层 legend_spec（区县统计 + 密度 + 结果层），此前每层一张完整
 * 图例卡从 top:48px 堆到底，整条左列盖住地图内容。现在默认只展开最新一
 * 层（数组末位、贴底 —— 离视口中心最远），其余收折成一行窄条，点击
 * eyebrow 行切换展开；用户展开过的层跨重渲染保持其选择。
 */
export function LegendStack({ entries }: { entries: LegendStackEntry[] }) {
  // 用户显式选择：id → 展开?未操作过的层走自动态（仅最新展开）。
  const [expandedOverride, setExpandedOverride] = useState<Record<string, boolean>>({});

  if (entries.length === 0) return null;

  const isExpanded = (id: string, idx: number) =>
    expandedOverride[id] ?? idx === entries.length - 1;

  const toggle = (id: string, idx: number) => {
    setExpandedOverride((prev) => ({
      ...prev,
      [id]: !(prev[id] ?? idx === entries.length - 1),
    }));
  };

  return (
    <div
      className="absolute z-30 flex max-w-[268px] flex-col gap-2 overflow-y-auto pr-1 transition-[bottom,left] duration-300"
      style={{
        left: 'var(--map-chrome-left, 16px)',
        bottom: 'var(--map-chrome-bottom, 10px)',
        top: '48px',
        justifyContent: 'flex-end',
      }}
    >
      {entries.map((entry, idx) => {
        const expanded = isExpanded(entry.id, idx);
        return (
          <div
            key={entry.id}
            className={`rounded-chrome ${entry.flashing ? "ring-2 ring-status-accent-vivid" : ""}`}
          >
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => toggle(entry.id, idx)}
              className="eyebrow mb-1 flex w-full max-w-[252px] items-center gap-1 px-1 text-left"
              title={entry.name}
            >
              {expanded
                ? <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
                : <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />}
              <span className="truncate">{entry.name}</span>
            </button>
            {expanded && (
              <ThematicLegend spec={entry.legendSpec} onFilterChange={entry.onFilterChange} />
            )}
          </div>
        );
      })}
    </div>
  );
}
