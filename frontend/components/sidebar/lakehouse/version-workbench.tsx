'use client';

import { useCallback, useMemo, useState } from 'react';
import { ArrowLeftRight, CloudUpload, RotateCcw, Undo2 } from 'lucide-react';
import {
  lakehouseApi,
  type DatasetVersionResolveResult,
} from '@/lib/api/lakehouse';
import { useToastStore } from '@/components/ui/toast';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { STitle } from '@/components/shared/section-title';

export interface VersionWorkbenchProps {
  ownerType: 'session' | 'project';
  sessionId: string;
  projectId: string;
  ownerToken?: string | null;
}

type Selection = { datasetId: string; versionId: string; label: string } | null;

/**
 * 版本工作台（P6）：publish / revoke（确认对话框 + 权限不足错误态）+
 * 快照对比（双栏字段 diff + 地图双屏）。
 *
 * publish 是 session → project 零字节发布（幂等）；unknown/forbidden 披露
 * 直接呈现。revoke 是 tombstone（引用仍可解析）。快照对比拉两个版本解析
 * 响应做本地 diff（后端无 diff 端点 —— 协调点）。
 */
export function VersionWorkbench({ ownerType, sessionId, projectId, ownerToken }: VersionWorkbenchProps) {
  const [publishing, setPublishing] = useState(false);
  const [publishReport, setPublishReport] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'publish' | 'revoke' | null>(null);
  const [objectIdsInput, setObjectIdsInput] = useState('');
  const [left, setLeft] = useState<Selection>(null);
  const [right, setRight] = useState<Selection>(null);
  const [leftResolved, setLeftResolved] = useState<DatasetVersionResolveResult | null>(null);
  const [rightResolved, setRightResolved] = useState<DatasetVersionResolveResult | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [swipe, setSwipe] = useState(50);
  const [mapDual, setMapDual] = useState(false);

  const addToast = useToastStore((s) => s.addToast);

  const objectIds = useMemo(
    () => objectIdsInput.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
    [objectIdsInput],
  );

  const doPublish = useCallback(async () => {
    if (!projectId) {
      addToast('请先在目录页选择项目域并填写项目 ID', 'error');
      return;
    }
    setPublishing(true);
    setPublishReport(null);
    try {
      const res = await lakehouseApi.publish(
        { session_id: sessionId, project_id: projectId, object_ids: objectIds },
        { ownerToken },
      );
      setPublishReport(
        `发布 ${res.published.length} · 去重 ${res.published.filter((p) => p.deduped).length} · 未知 ${res.unknown.length} · 无权 ${res.forbidden.length}`,
      );
      addToast('发布完成（幂等）', 'success');
    } catch (e) {
      // 权限不足 / 项目不存在（404 fail-closed）以持久报告呈现，不只是 toast。
      setPublishReport(e instanceof Error ? e.message : '发布失败');
    } finally {
      setPublishing(false);
      setConfirmAction(null);
    }
  }, [projectId, sessionId, objectIds, ownerToken, addToast]);

  const doRevoke = useCallback(async () => {
    if (!projectId) {
      addToast('撤销需要项目 ID（project 域操作）', 'error');
      return;
    }
    setPublishing(true);
    setPublishReport(null);
    try {
      const res = await lakehouseApi.revoke({ project_id: projectId, object_ids: objectIds }, { ownerToken });
      setPublishReport(`已撤销 ${res.revoked.length} · 未知 ${res.unknown.length}（tombstone：既有引用仍可解析）`);
    } catch (e) {
      setPublishReport(e instanceof Error ? e.message : '撤销失败');
    } finally {
      setPublishing(false);
      setConfirmAction(null);
    }
  }, [projectId, objectIds, ownerToken, addToast]);

  /** 快照对比：拉两个版本解析响应，双栏字段 diff。 */
  const loadDiff = useCallback(async () => {
    if (!left || !right) return;
    setDiffLoading(true);
    setDiffError(null);
    try {
      const [l, r] = await Promise.all([
        lakehouseApi.resolveDatasetVersion(left.datasetId, left.versionId, sessionId, { ownerToken }),
        lakehouseApi.resolveDatasetVersion(right.datasetId, right.versionId, sessionId, { ownerToken }),
      ]);
      setLeftResolved(l);
      setRightResolved(r);
      setMapDual(true);
    } catch (e) {
      setDiffError(e instanceof Error ? e.message : '版本解析失败');
    } finally {
      setDiffLoading(false);
    }
  }, [left, right, sessionId, ownerToken]);

  const mountDualPane = useCallback(() => {
    addToast('双屏对比：位图级 swipe 需窗口数据（查询页播放器同款管线）；当前为元数据 diff + 范围层', 'info');
  }, [addToast]);

  const diffRows = useMemo(() => buildDiffRows(leftResolved, rightResolved), [leftResolved, rightResolved]);

  if (ownerType !== 'session' && !projectId) {
    return <EmptyState icon={ArrowLeftRight} title="发布是跨域动作" description="目录页切换到项目域并填写项目 ID 后，publish/revoke 可用。" />;
  }

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-panel py-2" data-testid="lakehouse-version-workbench">
      <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
        <STitle title="发布 / 撤销" sub="session → project 零字节发布（幂等；tombstone 撤销）" />
        <textarea
          value={objectIdsInput}
          onChange={(e) => setObjectIdsInput(e.target.value)}
          placeholder="object id 列表（逗号或换行分隔，≤200）"
          aria-label="对象 ID 列表"
          rows={2}
          className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 font-mono text-micro text-ink"
        />
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            disabled={publishing || objectIds.length === 0}
            onClick={() => setConfirmAction('publish')}
            data-testid="lakehouse-publish"
            className="flex flex-1 items-center justify-center gap-1.5 rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-40"
          >
            <CloudUpload size={12} aria-hidden />
            发布 {objectIds.length} 项
          </button>
          <button
            type="button"
            disabled={publishing || objectIds.length === 0}
            onClick={() => setConfirmAction('revoke')}
            data-testid="lakehouse-revoke"
            className="flex flex-1 items-center justify-center gap-1.5 rounded-sm bg-surface-sunken px-2.5 py-1.5 text-caption text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-40"
          >
            <Undo2 size={12} aria-hidden />
            撤销
          </button>
        </div>
        {publishReport && (
          <InlineNotice variant="info" className="mt-2">
            {publishReport}
          </InlineNotice>
        )}
      </div>

      {/* 快照对比：双版本选择 + 字段 diff + 地图双屏（swipe 语义保留） */}
      <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
        <STitle title="快照对比" sub="拉取两个版本做本地 diff（后端无 diff 端点 —— 协调点）" />
        <SnapshotPicker side="left" value={left} onChange={setLeft} sessionId={sessionId} ownerToken={ownerToken} />
        <SnapshotPicker side="right" value={right} onChange={setRight} sessionId={sessionId} ownerToken={ownerToken} />
        <button
          type="button"
          disabled={!left || !right || diffLoading}
          onClick={() => void loadDiff()}
          className="mt-2 w-full rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-40"
          data-testid="lakehouse-diff-run"
        >
          {diffLoading ? '对比中…' : '运行对比'}
        </button>
        {diffError && (
          <InlineNotice variant="error" className="mt-2">
            {diffError}
          </InlineNotice>
        )}
        {leftResolved && rightResolved && (
          <div className="mt-3" data-testid="lakehouse-diff-view">
            <div className="flex items-center justify-between text-micro text-ink-muted">
              <span className="font-mono">{leftResolved.version_id.slice(0, 12)}</span>
              <RotateCcw size={10} aria-hidden />
              <span className="font-mono">{rightResolved.version_id.slice(0, 12)}</span>
            </div>
            <ul className="mt-1 space-y-0.5">
              {diffRows.map((row) => (
                <li
                  key={row.key}
                  className={`flex items-center justify-between gap-2 rounded-sm px-1.5 py-1 text-micro ${
                    row.changed ? 'bg-status-warning/10 text-status-warning' : 'text-ink-secondary'
                  }`}
                >
                  <span className="font-mono">{row.key}</span>
                  <span className="flex min-w-0 items-center gap-1">
                    <span className="truncate">{row.left}</span>
                    <ArrowLeftRight size={9} aria-hidden className="shrink-0" />
                    <span className="truncate">{row.right}</span>
                  </span>
                </li>
              ))}
            </ul>
            {/* 地图双屏：swipe 分割把手（键盘 ±5%，APG slider） */}
            {mapDual && (
              <div className="mt-2" data-testid="lakehouse-diff-map">
                <div className="relative h-24 overflow-hidden rounded-sm border border-edge-subtle bg-surface-sunken">
                  <div
                    className="absolute inset-y-0 left-0 bg-status-accent/15"
                    style={{ width: `${swipe}%` }}
                  />
                  <div
                    className="absolute inset-y-0 w-0.5 bg-status-accent"
                    style={{ left: `${swipe}%` }}
                  />
                  <span className="absolute left-1 top-1 text-micro text-ink-secondary">A</span>
                  <span className="absolute right-1 top-1 text-micro text-ink-secondary">B</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={swipe}
                    onChange={(e) => setSwipe(Number(e.target.value))}
                    aria-label="双屏对比分割位置"
                    aria-valuetext={`${swipe}%`}
                    className="absolute inset-x-0 bottom-0 accent-[var(--agent-accent)]"
                  />
                </div>
                <button
                  type="button"
                  onClick={mountDualPane}
                  className="mt-1 w-full rounded-sm bg-surface-sunken px-2 py-1 text-caption text-ink-secondary transition-colors hover:bg-surface-hover"
                >
                  提示：位图级对比需窗口数据（见查询页播放器）
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {confirmAction === 'publish' && (
        <ConfirmDialog
          open
          title="发布到项目"
          description={`将 ${objectIds.length} 个对象零字节发布到 ${projectId || '（未填项目）'}？（幂等；owner 链校验）`}
          confirmLabel="发布"
          onConfirm={() => void doPublish()}
          onCancel={() => setConfirmAction(null)}
        />
      )}
      {confirmAction === 'revoke' && (
        <ConfirmDialog
          open
          title="撤销项目内发布"
          description={`撤销 ${objectIds.length} 个对象在 ${projectId || '（未填项目）'} 的发布？（tombstone —— 既有引用仍可解析）`}
          confirmLabel="撤销发布"
          onConfirm={() => void doRevoke()}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </div>
  );
}

interface SnapshotPickerProps {
  side: 'left' | 'right';
  value: Selection;
  onChange: (s: Selection) => void;
  sessionId: string;
  ownerToken?: string | null;
}

/** 版本选择器：dataset id + version id 直填（版本历史列表在数据集页）。 */
function SnapshotPicker({ side, value, onChange }: SnapshotPickerProps) {
  const [datasetId, setDatasetId] = useState(value?.datasetId ?? '');
  const [versionId, setVersionId] = useState(value?.versionId ?? '');
  const label = side === 'left' ? 'A 版本' : 'B 版本';
  return (
    <div className="mt-2 flex items-center gap-1.5">
      <span className="w-10 shrink-0 text-caption text-ink-muted">{label}</span>
      <input
        type="text"
        value={datasetId}
        onChange={(e) => {
          setDatasetId(e.target.value);
          onChange(null);
        }}
        placeholder="dataset id"
        aria-label={`${label} dataset id`}
        className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-1 font-mono text-micro text-ink"
      />
      <input
        type="text"
        value={versionId}
        onChange={(e) => {
          setVersionId(e.target.value);
          onChange(
            e.target.value && datasetId
              ? { datasetId, versionId: e.target.value, label }
              : null,
          );
        }}
        placeholder="version id"
        aria-label={`${label} version id`}
        className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-1 font-mono text-micro text-ink"
      />
    </div>
  );
}

interface DiffRow {
  key: string;
  left: string;
  right: string;
  changed: boolean;
}

/** 双栏字段 diff：版本行键 + 合成 manifest 可用性（值直排展示）。 */
function buildDiffRows(
  l: DatasetVersionResolveResult | null,
  r: DatasetVersionResolveResult | null,
): DiffRow[] {
  if (!l || !r) return [];
  const row = (key: string, lv: unknown, rv: unknown): DiffRow => {
    const left = lv == null ? '—' : String(lv);
    const right = rv == null ? '—' : String(rv);
    return { key, left, right, changed: left !== right };
  };
  const short = (v: string | null) => (v ? v.slice(0, 12) : null);
  return [
    row('version', short(l.version_id), short(r.version_id)),
    row('action', l.action, r.action),
    row('branch', l.branch, r.branch),
    row('data_object', short(l.data_object_id), short(r.data_object_id)),
    row('content_sha', short(l.content_sha256), short(r.content_sha256)),
    row('byte_size', l.byte_size, r.byte_size),
    row('parent', short(l.parent_version_id), short(r.parent_version_id)),
    row('content_available', l.content_available, r.content_available),
    row('manifest', l.manifest ? '可解析' : '不可解析', r.manifest ? '可解析' : '不可解析'),
    row('commit.kind', l.commit?.kind ?? null, r.commit?.kind ?? null),
  ];
}
