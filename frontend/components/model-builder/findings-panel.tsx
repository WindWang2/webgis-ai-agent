'use client';

/**
 * FindingsPanel — 验证发现列表 + 发布入口。
 *
 * 契约：validate/publish 结果的 typed findings（contracts.ModelFinding.
 * to_bounded 投影）；detail_zh/detail_en 按 locale 取一；node_id 可点击
 * （宿主联动画布选中）；发布仅在 valid=true 时可用；发布 422 MODEL_INVALID
 * 的 findings 由宿主直接落本面板（err.findings）。
 */

import { useState } from 'react';
import { ListChecks } from 'lucide-react';
import clsx from 'clsx';
import { useT, useLocale } from '@/lib/i18n/useT';
import { InlineNotice } from '@/components/shared/inline-notice';
import { EmptyState } from '@/components/shared/empty-state';
import type { ModelFinding } from '@/lib/api/model-authoring';

export type PublishChange = 'major' | 'minor' | 'patch';

export interface FindingsPanelProps {
  findings: ModelFinding[];
  /** null = 尚未验证过。 */
  valid: boolean | null;
  fingerprint: string;
  validating: boolean;
  publishing: boolean;
  /** 发布成功结果（新版本号）。 */
  publishedVersion: string | null;
  publishError: string | null;
  onValidate: () => void;
  onPublish: (change: PublishChange) => void;
  /** finding.node_id 点击 → 画布选中联动。 */
  onJumpToNode: (nodeId: string) => void;
}

/** severity → 色块 token（error 红 / warning 琥珀，与 InlineNotice 同族）。 */
function severityDotClass(severity: string): string {
  return severity === 'error' ? 'bg-status-critical' : 'bg-status-warning';
}

export function FindingsPanel({
  findings,
  valid,
  fingerprint,
  validating,
  publishing,
  publishedVersion,
  publishError,
  onValidate,
  onPublish,
  onJumpToNode,
}: FindingsPanelProps) {
  const t = useT('modelBuilder');
  const locale = useLocale();
  const [change, setChange] = useState<PublishChange>('minor');

  const detail = (f: ModelFinding) => (locale === 'zh-CN' ? f.detail_zh : f.detail_en) || f.detail;

  return (
    <section aria-label={t('aria.findings')} className="space-y-2" data-testid="findings-panel">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          data-testid="validate-btn"
          onClick={onValidate}
          disabled={validating}
          className="rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink hover:bg-surface-hover disabled:opacity-50"
        >
          {validating ? t('toolbar.validating') : t('toolbar.validate')}
        </button>
        <select
          aria-label={t('findings.changeLevel')}
          value={change}
          onChange={(e) => setChange(e.target.value as PublishChange)}
          className="rounded-sm border border-edge-subtle bg-surface-raised px-1 py-1 text-meta text-ink"
        >
          <option value="major">{t('toolbar.major')}</option>
          <option value="minor">{t('toolbar.minor')}</option>
          <option value="patch">{t('toolbar.patch')}</option>
        </select>
        <button
          type="button"
          data-testid="publish-btn"
          onClick={() => onPublish(change)}
          disabled={valid !== true || publishing}
          title={valid !== true ? t('findings.publishBlocked') : undefined}
          className="rounded-sm bg-status-accent px-2 py-1 text-meta font-medium text-ink-on-accent hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {publishing ? t('findings.publishing') : t('toolbar.publish')}
        </button>
      </div>

      {publishedVersion && (
        <div data-testid="publish-result">
          <InlineNotice variant="success">
            {t('findings.published', { version: publishedVersion })}
          </InlineNotice>
        </div>
      )}
      {publishError && <InlineNotice variant="error">{publishError}</InlineNotice>}
      {valid === true && findings.length === 0 && (
        <InlineNotice variant="success">{t('findings.validNotice')}</InlineNotice>
      )}
      {valid === false && (
        <InlineNotice variant="error">{t('findings.invalidNotice', { count: findings.length })}</InlineNotice>
      )}
      {fingerprint && valid !== null && (
        <p className="text-micro text-ink-muted">{t('findings.fingerprint', { fingerprint })}</p>
      )}

      {valid === null && findings.length === 0 ? (
        <EmptyState icon={ListChecks} title={t('findings.empty')} />
      ) : (
        <ul className="space-y-1">
          {findings.map((f, i) => (
            <li
              key={`${f.code}-${f.node_id}-${f.param}-${i}`}
              className="flex items-start gap-1.5 rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-meta"
            >
              <span aria-hidden className={clsx('mt-1 h-2 w-2 shrink-0 rounded-pill', severityDotClass(f.severity))} />
              <div className="min-w-0 flex-1">
                <p className="text-ink">
                  <span className="font-medium">{f.code}</span>
                  <span className="text-ink-muted">
                    {' · '}
                    {f.severity === 'error' ? t('findings.severityError') : t('findings.severityWarning')}
                  </span>
                </p>
                <p className="text-ink-secondary">{detail(f)}</p>
                {f.detail && detail(f) !== f.detail && (
                  <p className="truncate text-micro text-ink-muted" title={f.detail}>{f.detail}</p>
                )}
                {f.node_id && (
                  <button
                    type="button"
                    onClick={() => onJumpToNode(f.node_id)}
                    className="text-micro text-status-accent hover:underline"
                  >
                    {t('findings.jumpToNode', { node: f.node_id })}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
