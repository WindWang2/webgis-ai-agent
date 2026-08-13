'use client';

import { Activity, RefreshCw } from 'lucide-react';
import type { DataSource } from '@/lib/api/data-fabric';
import { StatusBadge } from '@/components/shared/status-badge';
import { ConfirmAction } from '@/components/shared/confirm-action';

/**
 * 数据源状态 → 共享 StatusBadge 映射（原 light-only 手写徽标收敛）。
 *
 * 这里只做后端词 → 徽标词的翻译，绝不定义第二套状态色。审计发现的冲突是
 * 「active」这个词被两处赋予了不同颜色：徽标里 active 表示"进行中"（info 蓝
 * + pulse），而数据源的 active 表示"已启用且可用"，本质是 success。
 * 解决方式是让卡片放弃复用这个撞名的键 —— healthy 与 active 都翻译成
 * ok（success 绿），degraded → stale（amber），其余 → error。
 * 这样 pulse 蓝始终只代表"有事情正在跑"。
 */
function toStatusBadgeProps(status: string): { status: string; label: string } {
  if (status === 'healthy' || status === 'active') return { status: 'ok', label: '正常' };
  if (status === 'degraded') return { status: 'stale', label: '降级' };
  return { status: 'error', label: '离线' };
}

export interface SourceItemCardProps {
  source: DataSource;
  onProbe: (sourceId: string) => void;
  onSync: (sourceId: string) => void;
  onDelete: (sourceId: string) => void;
}

/** 已注册数据源卡片：名称 + 状态徽标 + Endpoint + 探查/同步/两段式删除。 */
export function SourceItemCard({ source, onProbe, onSync, onDelete }: SourceItemCardProps) {
  const badge = toStatusBadgeProps(source.status);
  return (
    /* 与 layers-tab 行同款交互配方：hover 底色 + 左侧 accent 指示条位
       （border-l-2 border-l-transparent）；垂直内边距收一步更密。 */
    <div className="rounded-md border border-l-2 border-edge-subtle border-l-transparent bg-surface-overlay px-panel py-2 transition-colors hover:bg-surface-hover">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-ink">{source.name}</span>
        <StatusBadge status={badge.status} label={badge.label} />
      </div>
      <div className="mt-1 truncate font-mono text-caption text-ink-muted">
        {source.endpoint_url}
      </div>
      <div className="mt-2 flex items-center gap-2 border-t border-edge-subtle pt-2 text-caption">
        <button
          type="button"
          onClick={() => onProbe(source.id)}
          className="flex items-center gap-1 rounded-sm text-ink-secondary transition-colors hover:text-ink"
        >
          <Activity size={12} aria-hidden />
          <span>探查</span>
        </button>
        <button
          type="button"
          onClick={() => onSync(source.id)}
          className="flex items-center gap-1 rounded-sm text-ink-secondary transition-colors hover:text-ink"
        >
          <RefreshCw size={12} aria-hidden />
          <span>同步</span>
        </button>
        <ConfirmAction
          label="删除"
          confirmLabel="确认删除？"
          className="ml-auto"
          onConfirm={() => onDelete(source.id)}
        />
      </div>
    </div>
  );
}
