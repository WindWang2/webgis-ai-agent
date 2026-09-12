'use client';

import { InlineNotice } from '@/components/shared/inline-notice';
import type { RunComparison, WorkflowRunSummary } from '@/lib/api/project';
import { runFingerprintsEqual, shortId, summarizeCompare } from '@/lib/workflow/recovery';
import { useT } from '@/lib/i18n/useT';

export interface ComparePanelProps {
  runs: WorkflowRunSummary[];
  selectedRunId: string;
  peerId: string;
  onPeerChange: (id: string) => void;
  onCompare: () => void;
  result: RunComparison | null;
  busy?: boolean;
  error?: string | null;
}

export function ComparePanel({
  runs,
  selectedRunId,
  peerId,
  onPeerChange,
  onCompare,
  result,
  busy,
  error,
}: ComparePanelProps) {
const t = useT();
  const peers = runs.filter((r) => r.id !== selectedRunId);
  const same = result ? runFingerprintsEqual(result) : null;
  const rows = result ? summarizeCompare(result) : [];

  return (
    <section aria-labelledby="wf-compare-heading" className="space-y-2">
      <h3 id="wf-compare-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
        {t('sidebar.wf.compare')}
      </h3>
      {peers.length === 0 ? (
        <p className="text-[11px] text-[var(--theme-text-muted)]">{t('sidebar.wf.noOtherRuns')}</p>
      ) : (
        <div className="flex gap-1.5">
          <label className="sr-only" htmlFor="wf-compare-peer">
            {t('sidebar.wf.compareRun')}
          </label>
          <select
            id="wf-compare-peer"
            value={peerId}
            onChange={(e) => onPeerChange(e.target.value)}
            className="min-w-0 flex-1 rounded border px-2 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
            style={{
              backgroundColor: 'var(--theme-bg-input)',
              borderColor: 'var(--theme-border)',
              color: 'var(--theme-text-primary)',
            }}
          >
            <option value="">{t('sidebar.wf.pickRun')}</option>
            {peers.map((r) => (
              <option key={r.id} value={r.id}>
                {shortId(r.id, 10)} · {r.status}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onCompare}
            disabled={!peerId || busy}
            className="shrink-0 rounded px-2 py-1 text-[11px] font-medium text-white disabled:opacity-40"
            style={{ background: 'var(--agent-accent, #16a34a)' }}
          >
            {busy ? '对比中…' : '对比'}
          </button>
        </div>
      )}
      {error && <InlineNotice variant="warning">{error}</InlineNotice>}

      {result && (
        <div className="space-y-1.5">
          {same === true ? (
            <InlineNotice variant="success">{t('sidebar.wf.sameFingerprint')}</InlineNotice>
          ) : (
            <InlineNotice variant="info">{t('sidebar.wf.diffFingerprint')}</InlineNotice>
          )}
          {rows.length === 0 && same !== true ? (
            <p className="text-[11px] text-[var(--theme-text-muted)]">{t('sidebar.wf.noDiffFields')}</p>
          ) : (
            <ul className="space-y-1">
              {rows.map((row) => (
                <li
                  key={row.key}
                  className="rounded border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-2 py-1.5"
                >
                  <div className="text-[11px] font-medium text-[var(--theme-text-primary)]">{row.label}</div>
                  <div className="break-all text-[10px] text-[var(--theme-text-muted)]">{row.detail}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default ComparePanel;
