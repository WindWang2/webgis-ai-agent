'use client';

/**
 * ArtifactCenter — 产物中心（ADR-0143 P3）。
 *
 * 后端契约（勘察报告 §2.3 / §2.10）：artifacts 族仅 list / lineage / pin /
 * unpin / clone 五端点——
 * - 无下载端点：下载中心降级为「引用与校验和复制」，协调点声明在 PR；
 * - 无 revisions 列表端点：pin 回执记录 revision_no / content_sha256 单点，
 *   版本台账由 map-products 面板承接（P7 导航整合）；
 * - 列表行不含 pinned 字段：固定态为本会话操作后的易失镜像。
 */

import { useEffect, useRef, useState } from 'react';
import {
  Package,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Pin,
  PinOff,
  Copy,
  GitBranch,
  Check,
} from 'lucide-react';

import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { useToastStore } from '@/components/ui/toast';
import { useProjectArtifacts } from '@/lib/hooks/use-project-assets';
import type { ArtifactSummary } from '@/lib/api/project-assets';
import { formatCrs, shortId } from '@/lib/workflow/recovery';
import { formatIso } from './format';
import { LineageGraphView } from './lineage-graph';

export interface ArtifactCenterProps {
  projectId: string;
  authed: boolean;
  /** 血缘图节点定位（P7 交叉导航回调）。 */
  onLocateArtifact?: (artifactId: string) => void;
  /** 交叉导航聚焦：外部（质量回执/gc 计划）要求展开并加载该产物血缘。 */
  focusArtifactId?: string | null;
  /** 跳到 Map Product 版本台账（P7：版本维度对比由台账面板承接）。 */
  onViewVersionLedger?: () => void;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function ArtifactCenter({
  projectId,
  authed,
  onLocateArtifact,
  focusArtifactId,
  onViewVersionLedger,
}: ArtifactCenterProps) {
  const ac = useProjectArtifacts(projectId);
  const addToast = useToastStore((s) => s.addToast);
  const [expandedId, setExpandedId] = useState('');
  const [copiedId, setCopiedId] = useState('');
  const [sortNewest, setSortNewest] = useState(true);
  const lastFocused = useRef('');

  // 交叉导航：聚焦指定产物（展开 + 拉血缘）。lastFocused 防重复聚焦；
  // loadLineage 经 ref 快照调用，避免 effect 跟随血缘缓存刷新重跑。
  const loadLineageRef = useRef(ac.loadLineage);
  loadLineageRef.current = ac.loadLineage;
  useEffect(() => {
    if (!focusArtifactId || focusArtifactId === lastFocused.current) return;
    lastFocused.current = focusArtifactId;
    setExpandedId(focusArtifactId);
    void loadLineageRef.current(focusArtifactId);
  }, [focusArtifactId]);

  const types = Array.from(new Set(ac.artifacts.map((a) => a.artifact_type))).sort();
  const sorted = [...ac.artifacts].sort((a, b) => {
    const da = new Date(a.created_at).getTime() || 0;
    const db = new Date(b.created_at).getTime() || 0;
    return sortNewest ? db - da : da - db;
  });

  const handlePin = async (a: ArtifactSummary) => {
    const pinned = ac.pinnedLocal[a.id];
    const ok = pinned ? await ac.unpin(a.id) : await ac.pin(a.id);
    if (ok) addToast(pinned ? `已取消固定 ${a.name}` : `已固定 ${a.name}`, 'success');
  };

  const handleClone = async (a: ArtifactSummary) => {
    const result = await ac.clone(a.id);
    if (result) {
      addToast(`已克隆为产物 ${shortId(result.artifact_id, 12)}`, 'success');
    }
  };

  const handleCopyRef = async (a: ArtifactSummary) => {
    const sha = ac.pinReceipts[a.id]?.content_sha256;
    const text = sha ? `artifact:${a.id}\nsha256:${sha}` : `artifact:${a.id}`;
    if (await copyText(text)) {
      setCopiedId(a.id);
      setTimeout(() => setCopiedId(''), 2000);
      addToast('产物引用已复制', 'success');
    }
  };

  const handleLineage = async (a: ArtifactSummary) => {
    if (expandedId !== a.id) setExpandedId(a.id);
    await ac.loadLineage(a.id);
  };

  return (
    <section aria-labelledby="art-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 id="art-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
          <Package size={14} className="text-ink-muted" aria-hidden /> 产物 ({ac.total})
        </h3>
        <span className="flex items-center gap-1">
          <select
            aria-label="按时间排序产物"
            value={sortNewest ? 'newest' : 'oldest'}
            onChange={(e) => setSortNewest(e.target.value === 'newest')}
            className="rounded-sm border border-edge-subtle bg-surface-sunken px-1 py-0.5 text-micro text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
          >
            <option value="newest">最新优先</option>
            <option value="oldest">最早优先</option>
          </select>
          {types.length > 0 && (
            <select
              aria-label="按类型筛选产物"
              value={ac.typeFilter}
              onChange={(e) => ac.setTypeFilter(e.target.value)}
              className="rounded-sm border border-edge-subtle bg-surface-sunken px-1 py-0.5 text-micro text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
            >
              <option value="">全部类型</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
          <IconButton
            label="刷新产物"
            icon={RefreshCw}
            iconSize={13}
            disabled={ac.loading}
            onClick={() => {
              void ac.reload({ forceRefresh: true });
            }}
          />
        </span>
      </div>

      <InlineNotice variant="info">
        后端暂无产物下载端点——此处提供引用/校验和复制；下载与版本对比能力见 PR 协调点。
        {onViewVersionLedger && (
          <>
            {' '}
            <button
              type="button"
              onClick={onViewVersionLedger}
              className="text-status-accent underline-offset-2 hover:underline"
            >
              查看 Map Product 版本台账 →
            </button>
          </>
        )}
      </InlineNotice>

      {ac.error && <InlineNotice variant="error">{ac.error}</InlineNotice>}

      {ac.loading && ac.artifacts.length === 0 ? (
        <LoadingState label="加载产物…" />
      ) : ac.artifacts.length === 0 ? (
        <EmptyState icon={Package} title="暂无产物" description="运行工作流或提升运行产物后出现在此" />
      ) : (
        <div className="space-y-1.5">
          {sorted.map((a) => {
            const expanded = expandedId === a.id;
            const pinned = ac.pinnedLocal[a.id] === true;
            const receipt = ac.pinReceipts[a.id];
            const lineageState = ac.lineage[a.id];
            return (
              <div key={a.id} className="rounded-md border border-edge-subtle bg-surface-raised">
                <div className="flex items-center justify-between gap-2 px-panel py-2">
                  <button
                    type="button"
                    onClick={() => setExpandedId(expanded ? '' : a.id)}
                    aria-expanded={expanded}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    {expanded ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
                    )}
                    <span className="min-w-0">
                      <span className="flex items-center gap-1">
                        {pinned && <Pin size={10} aria-label="已固定" className="shrink-0 text-status-accent" />}
                        <span className="truncate text-meta font-medium text-ink">{a.name}</span>
                      </span>
                      <span className="block text-micro text-ink-muted">
                        {a.artifact_type}
                        {a.format ? ` • ${a.format}` : ''} • {formatCrs(a.crs)}
                      </span>
                    </span>
                  </button>
                  <span className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        void handlePin(a);
                      }}
                      disabled={!authed || ac.busyId === a.id}
                      aria-pressed={pinned}
                      title={authed ? (pinned ? '取消固定' : '固定（防回收）') : '需要登录账号'}
                      className="rounded-sm p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink disabled:opacity-50"
                    >
                      {pinned ? <PinOff size={13} aria-hidden /> : <Pin size={13} aria-hidden />}
                    </button>
                    <ConfirmAction
                      label="克隆"
                      confirmLabel="确认克隆？"
                      onConfirm={() => {
                        void handleClone(a);
                      }}
                      disabled={!authed || ac.busyId === a.id}
                      title={authed ? '指针克隆（零复制）' : '需要登录账号'}
                    />
                  </span>
                </div>

                {expanded && (
                  <div className="space-y-2 border-t border-edge-subtle px-panel py-2">
                    <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-micro text-ink-secondary">
                      <dt>ID</dt>
                      <dd className="truncate font-mono" title={a.id}>{shortId(a.id, 16)}</dd>
                      <dt>创建于</dt>
                      <dd>{formatIso(a.created_at)}</dd>
                      {receipt?.revision_no != null && (
                        <>
                          <dt>固定版本</dt>
                          <dd>r{receipt.revision_no}</dd>
                        </>
                      )}
                      {receipt?.content_sha256 && (
                        <>
                          <dt>sha256</dt>
                          <dd className="truncate font-mono" title={receipt.content_sha256}>
                            {shortId(receipt.content_sha256, 20)}
                          </dd>
                        </>
                      )}
                    </dl>

                    <div className="flex flex-wrap gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          void handleCopyRef(a);
                        }}
                        className="flex items-center gap-1 rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken"
                      >
                        {copiedId === a.id ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
                        {copiedId === a.id ? '已复制' : '复制引用'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          void handleLineage(a);
                        }}
                        className="flex items-center gap-1 rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken"
                      >
                        <GitBranch size={11} aria-hidden /> 血缘图
                      </button>
                      {onLocateArtifact && (
                        <button
                          type="button"
                          onClick={() => onLocateArtifact(a.id)}
                          className="rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken"
                        >
                          在血缘中定位
                        </button>
                      )}
                    </div>

                    {ac.cloneResult?.source_artifact_id === a.id && (
                      <InlineNotice variant="success">
                        克隆成功：新产物 {shortId(ac.cloneResult.artifact_id, 12)}
                        {ac.cloneResult.content_location
                          ? ` · 位置 ${shortId(ac.cloneResult.content_location, 24)}`
                          : ''}
                      </InlineNotice>
                    )}

                    {lineageState === 'loading' && <LoadingState label="加载血缘…" />}
                    {lineageState === 'error' && <InlineNotice variant="error">血缘加载失败</InlineNotice>}
                    {lineageState === 'empty' && (
                      <p className="text-micro text-ink-muted">该产物暂无血缘记录（孤立产物）。</p>
                    )}
                    {lineageState && lineageState !== 'loading' && lineageState !== 'error' && lineageState !== 'empty' && (
                      <LineageGraphView graph={lineageState} onNodeClick={onLocateArtifact} />
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {ac.hasMore && (
            <button
              type="button"
              onClick={() => {
                void ac.loadMore();
              }}
              className="w-full rounded-sm border border-edge-subtle py-1 text-micro text-ink-secondary hover:bg-surface-sunken"
            >
              加载更多（已加载 {ac.artifacts.length}/{ac.total}）
            </button>
          )}
        </div>
      )}
    </section>
  );
}
