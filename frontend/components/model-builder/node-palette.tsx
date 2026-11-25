'use client';

/**
 * NodePalette — 能力目录侧栏（作者面只读投影 → 节点添加入口）。
 *
 * 数据：GET /model-authoring/capabilities 一次性 fetch（宿主注入，无轮询）。
 * 分区：数据输入（DATA_ROLES 逐角色）/ 转换算子 / 分析能力 / 制图 / 输出。
 * 每项按钮带 aria-label 且键盘可达；点击回调宿主添加节点（自动找空位）。
 */

import { memo } from 'react';
import { Plus } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import type { ModelCapabilities } from '@/lib/api/model-authoring';

export type PaletteAddPayload =
  | { kind: 'data_input'; role: string }
  | { kind: 'transform'; transform_op: string }
  | { kind: 'analysis'; capability: string }
  | { kind: 'cartography' }
  | { kind: 'output' };

export interface NodePaletteProps {
  capabilities: ModelCapabilities | null;
  loading: boolean;
  error: string | null;
  onAdd: (payload: PaletteAddPayload) => void;
  onRetry: () => void;
}

const SECTION =
  'space-y-1';
const ITEM =
  'touch-target flex w-full items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-left text-meta text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink';

const Item = memo(function PaletteItem({
  label,
  ariaLabel,
  dotClass,
  onClick,
}: {
  label: string;
  ariaLabel: string;
  dotClass: string;
  onClick: () => void;
}) {
  return (
    <button type="button" aria-label={ariaLabel} onClick={onClick} className={ITEM}>
      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-pill ${dotClass}`} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <Plus size={12} aria-hidden className="shrink-0 text-ink-muted" />
    </button>
  );
});

export function NodePalette({ capabilities, loading, error, onAdd, onRetry }: NodePaletteProps) {
  const t = useT('modelBuilder');

  if (loading) return <LoadingState label={t('palette.loading')} />;
  if (error || !capabilities) {
    return (
      <div className="space-y-2">
        <InlineNotice variant="error">
          {t('palette.loadFailed', { detail: error ?? '' })}
        </InlineNotice>
        <button type="button" onClick={onRetry} className="text-meta text-status-accent hover:underline">
          {t('palette.retry')}
        </button>
      </div>
    );
  }

  const heading = 'text-meta font-semibold text-ink-secondary';

  return (
    <nav aria-label={t('aria.palette')} className="space-y-3">
      <section className={SECTION}>
        <h3 className={heading}>{t('palette.sectionDataInput')}</h3>
        {capabilities.data_roles.map((role) => (
          <Item
            key={`role-${role}`}
            label={role}
            ariaLabel={t('aria.paletteItem', { name: role })}
            dotClass="bg-status-accent"
            onClick={() => onAdd({ kind: 'data_input', role })}
          />
        ))}
      </section>
      <section className={SECTION}>
        <h3 className={heading}>{t('palette.sectionTransform')}</h3>
        {capabilities.transform_ops.map((op) => (
          <Item
            key={`op-${op}`}
            label={op}
            ariaLabel={t('aria.paletteItem', { name: op })}
            dotClass="bg-status-info"
            onClick={() => onAdd({ kind: 'transform', transform_op: op })}
          />
        ))}
      </section>
      <section className={SECTION}>
        <h3 className={heading}>{t('palette.sectionAnalysis')}</h3>
        {capabilities.capabilities.map((cap) => (
          <Item
            key={`cap-${cap.capability}`}
            label={cap.capability}
            ariaLabel={t('aria.paletteItem', { name: cap.capability })}
            dotClass="bg-status-success"
            onClick={() => onAdd({ kind: 'analysis', capability: cap.capability })}
          />
        ))}
      </section>
      <section className={SECTION}>
        <h3 className={heading}>{t('palette.sectionCartography')}</h3>
        <Item
          label={t('node.kindCartography')}
          ariaLabel={t('aria.paletteItem', { name: t('node.kindCartography') })}
          dotClass="bg-status-warning"
          onClick={() => onAdd({ kind: 'cartography' })}
        />
      </section>
      <section className={SECTION}>
        <h3 className={heading}>{t('palette.sectionOutput')}</h3>
        <Item
          label={t('node.kindOutput')}
          ariaLabel={t('aria.paletteItem', { name: t('node.kindOutput') })}
          dotClass="bg-status-accent"
          onClick={() => onAdd({ kind: 'output' })}
        />
      </section>
    </nav>
  );
}
