/**
 * SituationStatusBar —— 情境状态条（ADR-0283 D5 / 任务书 E4）。
 *
 * 一行六个诚实状态位：当前分析范围 / 选中对象（含来源）/ 结果来源 /
 * 操作执行中 / 等待地图验证 / 失败可重试。数据全部来自服务端 situation/
 * turn-chain 只读投影（刷新/重连即时恢复，localStorage 非权威）。
 *
 * a11y：role="status" + aria-live="polite"（变更播报摘要一轮一次而非逐帧）；
 * 主题：V4 语义 token（bg-surface-panel/text-ink/border-edge）暗亮同源。
 */
'use client';

import React, { useEffect, useMemo, useRef } from 'react';
import { useT } from '@/lib/i18n/useT';
import {
  refreshSituationProjection,
  useSituationProjection,
} from '@/lib/interaction/use-situation-projection';
import {
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { getSituationProjection } from '@/lib/interaction/use-situation-projection';

interface StatusChip {
  key: string;
  tone: 'neutral' | 'accent' | 'warn';
  label: string;
  title?: string;
}

function useRefreshOnSession(): void {
  const versionRef = useRef(0);
  useEffect(() => {
    const sync = () => {
      const cursor = getMapSpecSessionCursor();
      if (!cursor.sessionId) return;
      // review P2-5：会话切换（投影 store 还停在旧会话）也必须刷新——
      // 否则状态条永久显示上一会话的 scope/selection。
      const staleSession = getSituationProjection().sessionId;
      if (versionRef.current === 0 || staleSession !== cursor.sessionId) {
        versionRef.current += 1;
        void refreshSituationProjection(cursor.sessionId);
      }
    };
    sync();
    // 会话建立/切换由 session-cursor live 通道驱动（本组件自己的投影
    // store 订阅不会在会话建立时发信号）。
    const unsubscribe = subscribeMapSpecLive(sync);
    // 重连/可见性恢复：窗口重新可见时刷新一次（服务端事实源）。
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        const sid = getMapSpecSessionCursor().sessionId;
        if (sid) void refreshSituationProjection(sid);
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      unsubscribe();
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
}

export function SituationStatusBar(): React.ReactElement {
  const t = useT('workbench');
  const projection = useSituationProjection();
  useRefreshOnSession();

  const chips = useMemo<StatusChip[]>(() => {
    const out: StatusChip[] = [];
    const view = projection.view;
    if (projection.status === 'error' || !view) {
      out.push({
        key: 'scope', tone: 'neutral',
        label: projection.status === 'error'
          ? t('situationBar.unavailable')
          : t('situationBar.loading'),
      });
      return out;
    }
    // 1) 分析范围
    const scope = view.claimed_scope;
    if (scope?.kind === 'none' || !scope) {
      out.push({ key: 'scope', tone: 'neutral', label: t('situationBar.scopeNone') });
    } else if (scope.kind === 'aoi') {
      out.push({ key: 'scope', tone: 'accent', label: t('situationBar.scopeAoi') });
    } else {
      const count = scope.feature_ids?.length ?? 0;
      out.push({
        key: 'scope', tone: 'accent',
        label: t('situationBar.scopeCount', { count }),
        title: scope.dataset_ref ?? undefined,
      });
    }
    // 2) 选中对象（含来源）
    const selection = view.interaction_snapshot?.selection as
      | { layer_id?: string; selected_ids?: string[]; feature_id?: string }
      | null | undefined;
    const chartSelection = view.interaction_snapshot?.chart_selection as
      | { categories?: string[] }
      | null | undefined;
    if (chartSelection?.categories?.length) {
      out.push({
        key: 'selection', tone: 'accent',
        label: t('situationBar.selectionChart', {
          count: chartSelection.categories.length,
        }),
      });
    } else if (selection?.selected_ids?.length) {
      out.push({
        key: 'selection', tone: 'accent',
        label: t('situationBar.selectionIds', { count: selection.selected_ids.length }),
        title: selection.layer_id,
      });
    } else if (selection?.feature_id) {
      out.push({
        key: 'selection', tone: 'accent',
        label: t('situationBar.selectionOne'),
        title: selection.layer_id,
      });
    } else {
      out.push({ key: 'selection', tone: 'neutral', label: t('situationBar.selectionNone') });
    }
    // 3) 结果来源（最近已观测操作）
    const chain = projection.turnChain;
    if (chain?.counts?.executed_observed) {
      out.push({
        key: 'provenance', tone: 'accent',
        label: t('situationBar.resultsObserved', {
          count: chain.counts.executed_observed,
        }),
      });
    }
    // 4) 操作执行中
    const pending = view.situation?.interaction?.pending_mutations?.value;
    if (Array.isArray(pending) && pending.length > 0) {
      out.push({
        key: 'running', tone: 'accent',
        label: t('situationBar.running', { count: pending.length }),
      });
    }
    // 5) 等待地图验证
    const behind = view.inconsistencies.some(
      (i) => i.code === 'observed_behind_desired' || i.code === 'observation_missing',
    );
    if (behind) {
      out.push({ key: 'verifying', tone: 'warn', label: t('situationBar.verifying') });
    }
    // 6) 失败可重试（未落地的用户尝试 / pending 回执）
    const attempted = view.interaction_snapshot?.attempted_not_applied?.length ?? 0;
    const agentPending = chain?.counts?.agent_pending ?? 0;
    if (attempted > 0 || agentPending > 0) {
      out.push({
        key: 'retryable', tone: 'warn',
        label: t('situationBar.retryable', {
          count: Math.max(attempted, agentPending),
        }),
      });
    }
    return out;
  }, [projection, t]);

  const toneClass = (tone: StatusChip['tone']): string =>
    tone === 'warn'
      ? 'bg-status-warn-soft text-status-warn border-status-warn-border'
      : tone === 'accent'
        ? 'bg-status-accent-soft text-status-accent border-status-accent-border'
        : 'bg-surface-sunken text-ink-muted border-edge';

  const summary = chips.map((c) => c.label).join(' · ');

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={t('situationBar.ariaLabel')}
      data-testid="situation-status-bar"
      data-situation-status={projection.status}
      data-situation-seq={String(projection.fetchedAtSeq)}
      className="pointer-events-auto flex max-w-full flex-wrap items-center gap-1.5 rounded-md border border-edge bg-surface-panel/95 px-2 py-1 text-micro shadow-elevation-1 backdrop-blur"
    >
      <span className="font-medium text-ink">{t('situationBar.title')}</span>
      {chips.map((chip) => (
        <span
          key={chip.key}
          title={chip.title}
          data-testid={`situation-chip-${chip.key}`}
          className={`inline-flex items-center rounded border px-1.5 py-0.5 ${toneClass(chip.tone)}`}
        >
          {chip.label}
        </span>
      ))}
      <span className="sr-only">{summary}</span>
    </div>
  );
}

