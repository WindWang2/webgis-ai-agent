'use client';

/**
 * QualityPanel — 质量审计与修复（ADR-0143 P5）。
 *
 * 后端契约（勘察报告 §2.6）：audit 与 repair 都要求 GeoJSON 请求体，而
 * project.py 没有数据集要素端点——GeoJSON 由前端聚合 data-fabric catalog
 * preview 获得（source_ref → 有界样例，§2.9）。repair 端点无 dry-run 参数：
 * 审计报告即预检依据，执行前列出将应用的操作并要求两段确认（任务书
 * 「dry-run 结果树」按此降级，PR 协调点有记录）。
 * C 线 QualityReport 实体合并后，结果视图经 useProjectQuality 适配层增强。
 */

import { useState } from 'react';
import { Database, ShieldCheck, Wand2 } from 'lucide-react';

import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import {
  useProjectDatasets,
  useProjectQuality,
} from '@/lib/hooks/use-project-assets';
import { fetchCatalogItemPreview } from '@/lib/api/project-assets';
import { isAbortError, parseApiErrorDetail } from '@/lib/workflow/recovery';
import { shortId } from '@/lib/workflow/recovery';

const KNOWN_OPERATIONS = ['make_valid', 'remove_empty'] as const;

export interface QualityPanelProps {
  projectId: string;
  authed: boolean;
  /** 修复回执的产物 → 产物中心定位（P7 交叉导航）。 */
  onLocateArtifact?: (artifactId: string) => void;
}

function toFeatureCollection(features: Array<Record<string, unknown>>): Record<string, unknown> {
  return { type: 'FeatureCollection', features };
}

export function QualityPanel({ projectId, authed, onLocateArtifact }: QualityPanelProps) {
  const ds = useProjectDatasets(projectId);
  const q = useProjectQuality(projectId);
  const addToast = useToastStore((s) => s.addToast);
  const [selectedId, setSelectedId] = useState('');
  const [ops, setOps] = useState<string[]>([...KNOWN_OPERATIONS]);
  const [issueCodes, setIssueCodes] = useState<string[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const selected = ds.datasets.find((d) => d.id === selectedId) ?? null;
  const reportCodes = q.report
    ? Array.from(new Set(q.report.issues.map((i) => i.code)))
    : [];

  const handleAudit = async () => {
    if (!selected) return;
    setPreviewError(null);
    if (!selected.source_ref) {
      setPreviewError('该数据集无 source_ref，无法取回要素进行审计。');
      return;
    }
    let features: Array<Record<string, unknown>>;
    try {
      const preview = await fetchCatalogItemPreview(selected.source_ref, { limit: 100 });
      features = preview.features;
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setPreviewError(parseApiErrorDetail(err, '取回样例要素失败（数据目录预览）'));
      return;
    }
    if (features.length === 0) {
      setPreviewError('来源样例为空，无可审计要素。');
      return;
    }
    setIssueCodes([]);
    const report = await q.audit(toFeatureCollection(features), {
      datasetId: selected.id,
    });
    if (report) {
      addToast(
        `审计完成：${report.overall_status === 'passed' ? '通过' : `${report.issues.length} 条问题`}`,
        report.overall_status === 'blocking' ? 'error' : 'success',
      );
    }
  };

  const handleRepair = async () => {
    if (!selected) return;
    const result = await q.runRepair({
      operations: ops.length > 0 ? ops : undefined,
      issue_codes: issueCodes.length > 0 ? issueCodes : undefined,
      dataset_id: selected.id,
      source_ref: selected.source_ref ?? undefined,
    });
    if (result) {
      addToast(
        `修复完成：${result.operations_applied.join('、') || '无操作'} · ${result.feature_count_before}→${result.feature_count} 要素`,
        'success',
      );
    }
  };

  return (
    <section aria-labelledby="quality-heading" className="space-y-2">
      <h3 id="quality-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
        <ShieldCheck size={14} className="text-ink-muted" aria-hidden /> 质量审计与修复
      </h3>

      {!authed && <InlineNotice variant="warning">审计与修复需要登录账号。</InlineNotice>}
      {ds.error && <InlineNotice variant="error">{ds.error}</InlineNotice>}
      {q.error && <InlineNotice variant="error">{q.error}</InlineNotice>}
      {previewError && <InlineNotice variant="error">{previewError}</InlineNotice>}

      <div className="space-y-1.5">
        <label className="block space-y-1">
          <span className="block text-meta font-medium text-ink-secondary">审计范围（数据集）</span>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
          >
            <option value="">选择数据集…</option>
            {ds.datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => {
            void handleAudit();
          }}
          disabled={!authed || q.busy || !selectedId}
          className="w-full rounded-sm bg-status-accent py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {q.phase === 'auditing' ? '审计中…' : '运行审计（样例 ≤100 要素）'}
        </button>
        <p className="text-micro text-ink-muted">
          要素来源：数据目录有界样例（前端聚合）。全量审计待后端提供服务端范围参数（协调点）。
        </p>
      </div>

      {q.busy && q.phase === 'repairing' && <LoadingState label="修复执行中…" />}

      {q.report && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2">
          <div className="flex items-center justify-between">
            <StatusBadge status={q.report.overall_status} />
            <span className="text-micro text-ink-muted">{q.report.total_features} 要素</span>
          </div>
          {typeof q.report.issue_summary === 'object' && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-micro text-ink-secondary">
              {Object.entries(q.report.issue_summary).map(([level, count]) => (
                <span key={level}>
                  {level}: <span className="font-mono">{count}</span>
                </span>
              ))}
            </div>
          )}
          {q.report.truncated && (
            <InlineNotice variant="warning">
              结果已截断：另有 {q.report.truncated_count} 条未展示。
            </InlineNotice>
          )}
          {q.report.issues.length > 0 && (
            <details open={q.report.overall_status === 'blocking'}>
              <summary className="cursor-pointer select-none text-micro text-ink-secondary">
                规则命中（{q.report.issues.length}）
              </summary>
              <ul className="mt-1 max-h-40 space-y-1 overflow-auto">
                {q.report.issues.slice(0, 100).map((issue, i) => (
                  <li key={`${issue.code}-${i}`} className="rounded-sm bg-surface-sunken px-1.5 py-1 text-micro">
                    <span className="mr-1 font-mono text-ink-muted">{issue.level}</span>
                    <span className="font-mono">{issue.code}</span>
                    <span className="block text-ink-secondary">{issue.message}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="space-y-1.5 border-t border-edge-subtle pt-2">
            <p className="flex items-center gap-1.5 text-meta font-medium text-ink-secondary">
              <Wand2 size={12} aria-hidden /> 修复（执行前列出操作，需确认）
            </p>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {KNOWN_OPERATIONS.map((op) => (
                <label key={op} className="flex items-center gap-1 text-micro text-ink-secondary">
                  <input
                    type="checkbox"
                    checked={ops.includes(op)}
                    onChange={(e) =>
                      setOps((prev) => (e.target.checked ? [...prev, op] : prev.filter((o) => o !== op)))
                    }
                  />
                  {op}
                </label>
              ))}
            </div>
            {reportCodes.length > 0 && (
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                <span className="text-micro text-ink-muted">按命中代码修复:</span>
                {reportCodes.map((code) => (
                  <label key={code} className="flex items-center gap-1 text-micro text-ink-secondary">
                    <input
                      type="checkbox"
                      checked={issueCodes.includes(code)}
                      onChange={(e) =>
                        setIssueCodes((prev) =>
                          e.target.checked ? [...prev, code] : prev.filter((c) => c !== code),
                        )
                      }
                    />
                    <span className="font-mono">{code}</span>
                  </label>
                ))}
              </div>
            )}
            <ConfirmAction
              label="执行修复"
              confirmLabel="确认执行修复？将写回修复结果"
              onConfirm={() => {
                void handleRepair();
              }}
              disabled={!authed || q.busy || !selected}
            />
          </div>
        </div>
      )}

      {q.repair && (
        <div className="space-y-1 rounded-md border border-status-success-border bg-status-success-soft px-panel py-2 text-micro">
          <p className="font-medium text-ink">修复回执</p>
          <p className="text-ink-secondary">
            操作 {q.repair.operations_applied.join('、') || '（无）'} · 要素 {q.repair.feature_count_before}→
            {q.repair.feature_count} · 日志 {q.repair.logs_count} 条
          </p>
          <p className="text-ink-secondary">
            血缘: {q.repair.lineage_status}
            {q.repair.lineage_artifact_id ? ` · ${shortId(q.repair.lineage_artifact_id, 12)}` : ''}
            {q.repair.repaired_ref ? ` · 修复引用 ${shortId(q.repair.repaired_ref, 16)}` : ''}
          </p>
          {q.repair.ref_registration_error && (
            <p className="text-status-critical">引用注册失败: {q.repair.ref_registration_error}</p>
          )}
          {onLocateArtifact && q.repair.lineage_artifact_id && (
            <button
              type="button"
              onClick={() => onLocateArtifact(q.repair!.lineage_artifact_id as string)}
              className="text-status-accent underline-offset-2 hover:underline"
            >
              在产物血缘中定位 →
            </button>
          )}
        </div>
      )}

      {ds.datasets.length === 0 && !ds.loading && (
        <EmptyState icon={Database} title="项目暂无数据集" description="先在数据集页签挂载数据" />
      )}
    </section>
  );
}
