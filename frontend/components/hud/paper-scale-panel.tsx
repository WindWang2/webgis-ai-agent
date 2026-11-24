'use client';

import { useT } from '@/lib/i18n/useT';
/**
 * GOAL 04 WP5 —— 「纸面比例尺制图综合」面板（出版审阅入口）。
 *
 * - 展示既有冻结计划（layer.scale_profile.paper_plans）：目标比例尺 /
 *   用途 / QA 判定 / 综合前后要素数 / 披露；
 * - 表单：比例尺文本（1:50万）+ 页面（A4横版）+ 用途 → POST preview
 *   （零突变）→ QA 摘要与管线可见 → 冻结（commit，QA blocking>0 后端
 *   拒绝 —— fail-closed 门在前端如实呈现，不静默）；
 * - commit 走既有 REST → service → CAS 链（与 Agent 同一服务面），
 *   成功后 refreshMapSpecAfterExternalMutation 刷新 committed spec；
 * - 后端未确认时绝不显示「已冻结」；错误结构化呈现（不吞）。
 */
import {
  useCallback, useMemo, useState, useSyncExternalStore,
} from 'react';
import { apiFetch } from '@/lib/api/transport';
import {
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { refreshMapSpecAfterExternalMutation } from '@/lib/mapspec/user-mutation';

const PURPOSES = ['overview', 'reference', 'planning', 'navigation',
  'thematic_exhibition'] as const;

interface FrozenPlanView {
  fingerprint: string;
  denominator: number;
  purpose: string;
  status: string;
  originalCount: number;
  artifactCount: number;
  qaBlocking: number;
  artifactRef: string;
}

interface PlanBlock {
  status?: string;
  fingerprint?: string;
  artifact_ref?: string;
  contract?: { target_denominator?: number; purpose?: string };
  stats?: { original_count?: number; artifact_count?: number };
  qa_summary?: { blocking?: number };
}

export function PaperScalePanel({ layerId }: { layerId: string }) {
  const t = useT('map.paperScale');
  const [denominatorText, setDenominatorText] = useState('1:50万');
  const [pageText, setPageText] = useState('A4横版');
  const [purpose, setPurpose] = useState<string>('overview');
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  // 真实 committed spec：冻结计划清单来自实际 MapSpec（不自造）。
  const committedSpec = useSyncExternalStore(
    subscribeMapSpecLive, getCommittedMapSpec);
  const frozenPlans = useMemo<FrozenPlanView[]>(() => {
    const layers = (committedSpec as {
      layers?: Array<Record<string, unknown>>;
    } | null)?.layers;
    if (!Array.isArray(layers)) return [];
    const layer = layers.find((row) => row && row.id === layerId);
    const plans = ((
      (layer?.scale_profile ?? {}) as { paper_plans?: Record<string, PlanBlock> }
    ).paper_plans) ?? {};
    return Object.entries(plans).map(([fingerprint, plan]) => ({
      fingerprint,
      denominator: plan.contract?.target_denominator ?? 0,
      purpose: plan.contract?.purpose ?? '',
      status: plan.status ?? '',
      originalCount: plan.stats?.original_count ?? 0,
      artifactCount: plan.stats?.artifact_count ?? 0,
      qaBlocking: plan.qa_summary?.blocking ?? 0,
      artifactRef: plan.artifact_ref ?? '',
    }));
  }, [committedSpec, layerId]);

  const post = useCallback(async (mode: 'preview' | 'commit') => {
    const { sessionId, revision } = getMapSpecSessionCursor();
    if (!sessionId || !layerId || busy) return null;
    setBusy(true);
    setMessage('');
    try {
      const data = await apiFetch<Record<string, unknown>>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/paper-scale`,
        {
          method: 'POST',
          body: {
            layer: layerId,
            denominator_text: denominatorText,
            page_text: pageText,
            purpose,
            mode,
            expected_revision: revision,
            operation_id: `psp:${layerId}:${mode}:${Date.now()}`,
          },
          label: 'Paper scale generalization',
        },
      );
      return data;
    } catch (err) {
      setMessage(t('failed', {
        p0: err instanceof Error ? err.message : String(err),
      }));
      return null;
    } finally {
      setBusy(false);
    }
  }, [busy, layerId, denominatorText, pageText, purpose, t]);

  const doPreview = useCallback(async () => {
    const data = await post('preview');
    if (data) {
      setPreview(data);
      const blocking = (data?.qa_summary as { blocking?: number })?.blocking;
      setMessage(blocking && blocking > 0
        ? t('qaBlocked', { p0: String(blocking) })
        : t('previewOk'));
    }
  }, [post, t]);

  const doCommit = useCallback(async () => {
    const data = await post('commit');
    if (!data) return;
    const status = typeof data?.status === 'string' ? data.status : '';
    if (status === 'committed') {
      setPreview(null);
      setMessage(t('frozen', { p0: String(data?.mapspec_revision ?? '') }));
      const { sessionId } = getMapSpecSessionCursor();
      if (sessionId) {
        await refreshMapSpecAfterExternalMutation(
          sessionId,
          typeof data?.mapspec_revision === 'number'
            ? data.mapspec_revision : undefined,
        );
      }
    } else if (status === 'qa_blocked') {
      setPreview(data);
      setMessage(t('qaRefused'));
    } else {
      setMessage(t('notApplied', { p0: status || t('unknownReceipt') }));
    }
  }, [post, t]);

  return (
    <section
      aria-label={t('title')}
      data-testid="paper-scale-panel"
      className="rounded border border-edge-subtle bg-surface-sunken p-3 space-y-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-ink-muted uppercase tracking-wider">
          {t('title')}
        </span>
        <span className="text-micro px-1.5 py-0.5 rounded-full border
          bg-surface-panel text-ink-muted border-edge-subtle">
          {t('displayOnly')}
        </span>
      </div>

      {frozenPlans.length > 0 && (
        <ul className="space-y-1" data-testid="paper-plans">
          {frozenPlans.map((plan) => (
            <li key={plan.fingerprint}
              className="text-caption text-ink rounded border
                border-edge-subtle bg-surface-panel px-2 py-1">
              1:{plan.denominator.toLocaleString()} · {plan.purpose}
              {' · '}
              {t('counts', { p0: String(plan.artifactCount),
                p1: String(plan.originalCount) })}
              {plan.qaBlocking > 0 ? ` · ${t('qaFail')}` : ''}
            </li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-3 gap-2">
        <input
          aria-label={t('denominator')}
          data-testid="paper-denominator"
          value={denominatorText}
          onChange={(e) => setDenominatorText(e.target.value)}
          className="text-caption rounded border border-edge-subtle
            bg-surface-panel px-2 py-1 text-ink"
        />
        <input
          aria-label={t('page')}
          data-testid="paper-page"
          value={pageText}
          onChange={(e) => setPageText(e.target.value)}
          className="text-caption rounded border border-edge-subtle
            bg-surface-panel px-2 py-1 text-ink"
        />
        <select
          aria-label={t('purpose')}
          data-testid="paper-purpose"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          className="text-caption rounded border border-edge-subtle
            bg-surface-panel px-2 py-1 text-ink"
        >
          {PURPOSES.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          data-testid="paper-preview"
          disabled={busy}
          onClick={() => { void doPreview(); }}
          className="text-caption rounded border border-edge-subtle
            bg-surface-panel px-2 py-1 text-ink disabled:opacity-50"
        >
          {t('preview')}
        </button>
        <button
          type="button"
          data-testid="paper-commit"
          disabled={busy}
          onClick={() => { void doCommit(); }}
          className="text-caption rounded border border-edge-subtle
            bg-surface-panel px-2 py-1 text-ink disabled:opacity-50"
        >
          {t('freeze')}
        </button>
      </div>

      {preview != null && (
        <div className="text-caption text-ink-muted" data-testid="paper-qa">
          {t('qaSummary', {
            p0: String((preview.qa_summary as
              { total?: number })?.total ?? 0),
            p1: String((preview.qa_summary as
              { blocking?: number })?.blocking ?? 0),
          })}
          {' · '}
          {t('counts', {
            p0: String((preview.stats as
              { artifact_count?: number })?.artifact_count ?? 0),
            p1: String((preview.stats as
              { original_count?: number })?.original_count ?? 0),
          })}
        </div>
      )}
      {message && (
        <div className="text-caption text-ink" data-testid="paper-message">
          {message}
        </div>
      )}
    </section>
  );
}
