'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircle2,
  ChevronRight,
  GitBranch,
  ListTree,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';
import {
  lakehouseApi,
  type GCPlan,
  type LineageView,
  type ScrubReport,
} from '@/lib/api/lakehouse';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { STitle } from '@/components/shared/section-title';

export interface OpsPanelProps {
  sessionId: string;
  ownerToken?: string | null;
  /** 目录/详情动线：预填血缘检视对象。 */
  lineageTarget: string | null;
  onTargetConsumed: () => void;
}

/**
 * 运维面板（P8，只读纪律）：对象 verify/scrub（DR 完整性）+ 血缘祖先链 +
 * GC dry-run 计划（admin 面；候选/保护计数树状只读展示）。
 *
 * **执行动作不在本线**：gc/execute 与 retention/execute 归 C 线闭环与 F 线
 * UI —— 本面板只读展示计划与证据（含 403 权限不足的诚实错误态）。
 */
export function OpsPanel({ sessionId, ownerToken, lineageTarget, onTargetConsumed }: OpsPanelProps) {
  const [objectId, setObjectId] = useState(lineageTarget ?? '');
  const [lineage, setLineage] = useState<LineageView | null>(null);
  const [scrub, setScrub] = useState<ScrubReport | null>(null);
  const [gcPlan, setGcPlan] = useState<GCPlan | null>(null);
  const [gcError, setGcError] = useState<string | null>(null);
  const [gcLoading, setGcLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  // 目录动线带进来的对象：自动加载血缘。
  useEffect(() => {
    if (lineageTarget) {
      setObjectId(lineageTarget);
      onTargetConsumed();
    }
  }, [lineageTarget, onTargetConsumed]);

  const inspect = useCallback(async () => {
    if (!objectId.trim() || !sessionId) return;
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setBusy(true);
    setError(null);
    setLineage(null);
    setScrub(null);
    try {
      const [lv, sr] = await Promise.all([
        lakehouseApi.getObjectLineage(objectId.trim(), sessionId, { ownerToken, signal: controller.signal }),
        lakehouseApi.scrubObject(objectId.trim(), { session_id: sessionId, mode: 'sample' }, { ownerToken, signal: controller.signal }),
      ]);
      if (seq !== reqRef.current.seq) return;
      setLineage(lv);
      setScrub(sr);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setError(e instanceof Error ? e.message : '检视失败（对象不存在或无权访问）');
    } finally {
      if (seq === reqRef.current.seq) setBusy(false);
    }
  }, [objectId, sessionId, ownerToken]);

  const loadGcPlan = useCallback(async () => {
    setGcLoading(true);
    setGcError(null);
    try {
      const plan = await lakehouseApi.planGc({ session_id: sessionId, grace_hours: 72 }, { ownerToken });
      setGcPlan(plan);
    } catch (e) {
      // 非 admin → 403（admin 专用端点的诚实错误态）。
      setGcError(e instanceof Error ? e.message : 'GC 计划获取失败（admin 专用）');
    } finally {
      setGcLoading(false);
    }
  }, [sessionId, ownerToken]);

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-panel py-2" data-testid="lakehouse-ops">
      <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
        <STitle title="对象检视" sub="verify / scrub 采样校验 + 血缘祖先链（只读）" />
        <div className="flex gap-1.5">
          <input
            type="text"
            value={objectId}
            onChange={(e) => setObjectId(e.target.value)}
            placeholder="data object id（64hex）"
            aria-label="对象 ID"
            className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 font-mono text-micro text-ink"
          />
          <button
            type="button"
            onClick={() => void inspect()}
            disabled={busy || !objectId.trim()}
            data-testid="lakehouse-ops-inspect"
            className="shrink-0 rounded-sm bg-status-accent px-2.5 py-1 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-40"
          >
            {busy ? '检视中…' : '检视'}
          </button>
        </div>
        {error && (
          <InlineNotice variant="error" className="mt-2">
            {error}
          </InlineNotice>
        )}
      </div>

      {scrub && (
        <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2" data-testid="lakehouse-scrub-report">
          <STitle title={`DR scrub · ${scrub.mode}`} />
          <div className="flex items-center gap-2 text-caption">
            {scrub.state === 'verified' ? (
              <CheckCircle2 size={14} className="text-status-success" aria-hidden />
            ) : (
              <XCircle size={14} className="text-status-danger" aria-hidden />
            )}
            <span className={scrub.state === 'verified' ? 'text-status-success' : 'text-status-danger'}>
              {scrub.state}
            </span>
            <span className="text-ink-muted">
              块 {scrub.chunks_checked}/{scrub.chunks_total} · etag {scrub.etag_checked ? '开' : '关'}
            </span>
          </div>
          {(scrub.missing.length > 0 || scrub.corrupt.length > 0 || scrub.etag_mismatch.length > 0) && (
            <ul className="mt-1 space-y-0.5 text-micro text-status-danger">
              {scrub.missing.length > 0 && <li>缺失 {scrub.missing.length} 块</li>}
              {scrub.corrupt.length > 0 && <li>损坏 {scrub.corrupt.length} 块</li>}
              {scrub.etag_mismatch.length > 0 && <li>etag 不符 {scrub.etag_mismatch.length} 块</li>}
            </ul>
          )}
        </div>
      )}

      {lineage && (
        <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2" data-testid="lakehouse-lineage">
          <STitle title="血缘链" sub={lineage.truncated ? '已截断（深度/节点双闸）' : `${lineage.ancestors.length} 个上游`} />
          <ol className="space-y-0.5">
            {lineage.ancestors.map((node) => (
              <li
                key={node.id}
                className="flex items-center gap-1.5 rounded-sm bg-surface-sunken px-1.5 py-1 text-micro"
                style={{ marginLeft: Math.min(node.depth - 1, 4) * 12 }}
              >
                <ChevronRight size={10} aria-hidden className="shrink-0 text-ink-muted" />
                <GitBranch size={10} aria-hidden className="shrink-0 text-ink-muted" />
                <span className="truncate font-mono text-ink-secondary">{node.id.slice(0, 12)}</span>
                <span className="ml-auto shrink-0 rounded-sm bg-surface-panel px-1 text-ink-muted">
                  {node.kind}
                </span>
              </li>
            ))}
          </ol>
          <p className="mt-1 text-micro text-ink-muted">
            root：<span className="font-mono">{lineage.root.slice(0, 12)}</span>
          </p>
        </div>
      )}

      {/* GC dry-run：只读展示（执行归 C/F 线） */}
      <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
        <STitle title="GC 计划（dry-run）" sub="admin 专用；本面板只读 —— 执行动作归 C 线闭环" />
        <button
          type="button"
          onClick={() => void loadGcPlan()}
          disabled={gcLoading || !sessionId}
          data-testid="lakehouse-gc-plan"
          className="flex w-full items-center justify-center gap-1.5 rounded-sm bg-surface-sunken px-2.5 py-1.5 text-caption text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-40"
        >
          <ListTree size={12} aria-hidden />
          {gcLoading ? '生成中…' : '生成 dry-run 计划'}
        </button>
        {gcError && (
          <InlineNotice variant="warning" className="mt-2">
            {gcError}
          </InlineNotice>
        )}
        {gcPlan && (
          <div className="mt-2 space-y-1 text-caption" data-testid="lakehouse-gc-plan-view">
            <div className="flex justify-between gap-2">
              <span className="flex items-center gap-1 text-ink-muted">
                <ShieldCheck size={11} aria-hidden />
                扫描 manifest
              </span>
              <span className="font-mono text-ink">{gcPlan.scanned_manifests}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">候选（宽限 {gcPlan.grace_hours}h 外）</span>
              <span className="font-mono text-ink">{gcPlan.candidates.length}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">受保护 blob</span>
              <span className="font-mono text-ink">{gcPlan.protected_count}</span>
            </div>
            {gcPlan.candidates.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {gcPlan.candidates.slice(0, 8).map((c) => (
                  <li key={c} className="flex items-center gap-1 truncate font-mono text-micro text-ink-muted">
                    <Trash2 size={9} aria-hidden />
                    {c.slice(0, 16)}…
                  </li>
                ))}
                {gcPlan.candidates.length > 8 && (
                  <li className="text-micro text-ink-muted">…共 {gcPlan.candidates.length} 个候选</li>
                )}
              </ul>
            )}
            <p className="text-micro text-ink-muted">
              token：<span className="font-mono">{gcPlan.token.slice(0, 12)}…</span>（execute 时重验；漂移 → 409）
            </p>
          </div>
        )}
      </div>

      {!sessionId && <EmptyState icon={ListTree} title="暂无活跃会话" description="运维检视按会话域授权。" />}
    </div>
  );
}
