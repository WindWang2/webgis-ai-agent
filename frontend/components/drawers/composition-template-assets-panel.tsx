'use client';

/**
 * Composition Template Assets Panel — G04 窄入口.
 *
 * 用户工作流（任务书 WP-C10）：
 *   1. 浏览可见组合模板资产（内置 4 族 + 私人/org/项目 DB 资产）；
 *   2. 「保存为我的模板」把当前会话版式落库（save-as-my-template）；
 *   3. 应用：经既有 Agent 工具通道（webgis_apply_composition 的
 *      template_asset_id 参数）—— 前端不建第二写入路径。
 *
 * 设计纪律：只做窄入口（列表 + 保存 + 应用分发），不做巨型面板；兼容
 * 元数据放详情（展开行），选择器优先显示兼容而非百项字段。
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw, Save, Sparkles, X } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import {
  compositionTemplateAssetsApi,
  type CompositionTemplateAssetSummary,
  type CompositionAssetVisibility,
} from '@/lib/api/composition-template-assets';
import { isApiError } from '@/lib/api/transport';
import { InlineNotice } from '@/components/shared/inline-notice';
import { EmptyState } from '@/components/shared/empty-state';

export interface CompositionTemplateAssetsPanelProps {
  open: boolean;
  onClose: () => void;
  /** 当前会话 id（保存为我的模板用；缺省时保存按钮禁用）。 */
  sessionId?: string;
  /** 应用资产的分发回调（父组件经 Agent 工具通道提交）。 */
  onApplyAsset?: (asset: CompositionTemplateAssetSummary) => void | Promise<void>;
}

type NoticeTone = 'info' | 'error';

export function CompositionTemplateAssetsPanel({
  open,
  onClose,
  sessionId,
  onApplyAsset,
}: CompositionTemplateAssetsPanelProps) {
  const t = useT('compositionAssets');
  const [assets, setAssets] = useState<CompositionTemplateAssetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [visibility, setVisibility] = useState<CompositionAssetVisibility>('private');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [projectId, setProjectId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      const res = await compositionTemplateAssetsApi.list({ forceRefresh: true });
      setAssets(res.assets);
      if (res.reason_codes.length > 0) {
        setNotice({ tone: 'info', text: t('notice.dbUnavailable') });
      }
    } catch (err) {
      setNotice({
        tone: 'error',
        text: isApiError(err) ? err.message : t('notice.loadFailed'),
      });
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const builtins = useMemo(() => assets.filter((a) => a.builtin), [assets]);
  const mine = useMemo(() => assets.filter((a) => !a.builtin), [assets]);

  const handleSave = useCallback(async () => {
    if (!sessionId) return;
    setSaving(true);
    setNotice(null);
    try {
      const res = await compositionTemplateAssetsApi.create({
        session_id: sessionId,
        visibility,
        project_id: visibility === 'project' ? projectId : undefined,
      });
      if (res.validation && res.validation.length > 0) {
        setNotice({ tone: 'error', text: t('notice.publishGateFailed') });
      } else {
        setNotice({ tone: 'info', text: t('notice.saved', { id: res.asset_id }) });
        await load();
      }
    } catch (err) {
      setNotice({
        tone: 'error',
        text: isApiError(err) ? err.message : t('notice.saveFailed'),
      });
    } finally {
      setSaving(false);
    }
  }, [sessionId, visibility, projectId, t, load]);

  if (!open) return null;

  const renderRow = (asset: CompositionTemplateAssetSummary) => {
    const expanded = expandedId === asset.asset_id;
    return (
      <li
        key={asset.asset_id}
        className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-surface)] px-3 py-2"
        data-testid={`asset-row-${asset.asset_id}`}
      >
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            className="flex-1 text-left"
            onClick={() => setExpandedId(expanded ? null : asset.asset_id)}
            aria-expanded={expanded}
          >
            <span className="text-sm font-medium text-[var(--theme-text)]">
              {asset.name || asset.asset_id}
            </span>
            {asset.builtin && (
              <span className="ml-2 rounded bg-[var(--theme-accent-soft,#e0e7ff)] px-1.5 py-0.5 text-[10px] text-[var(--theme-accent)]">
                {t('badge.builtin')}
              </span>
            )}
            <span className="ml-2 text-[11px] text-[var(--theme-text-muted)]">
              r{asset.revision} · {asset.status}
            </span>
          </button>
          <button
            type="button"
            className="rounded px-2 py-1 text-xs text-[var(--theme-accent)] hover:bg-[var(--theme-hover)] disabled:opacity-40"
            disabled={!onApplyAsset}
            onClick={() => void onApplyAsset?.(asset)}
          >
            {t('action.apply')}
          </button>
        </div>
        {expanded && (
          <dl className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-[var(--theme-text-muted)]">
            <div>
              <dt className="inline">{t('detail.assetId')}: </dt>
              <dd className="inline font-mono">{asset.asset_id}</dd>
            </div>
            <div>
              <dt className="inline">{t('detail.fingerprint')}: </dt>
              <dd className="inline font-mono">{asset.fingerprint.slice(0, 24)}</dd>
            </div>
            {asset.visibility && (
              <div>
                <dt className="inline">{t('detail.visibility')}: </dt>
                <dd className="inline">{asset.visibility}</dd>
              </div>
            )}
            <div>
              <dt className="inline">{t('detail.models')}: </dt>
              <dd className="inline">
                {(asset.compatible_map_models ?? []).join(', ') || '—'}
              </dd>
            </div>
          </dl>
        )}
      </li>
    );
  };

  return (
    <aside
      role="dialog"
      aria-modal="true"
      aria-label={t('title')}
      className="fixed right-0 top-0 z-40 flex h-full w-80 flex-col gap-3 border-l border-[var(--theme-border)] bg-[var(--theme-bg)] p-4"
      data-testid="composition-template-assets-panel"
    >
      <header className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--theme-text)]">
          <Sparkles className="h-4 w-4" />
          {t('title')}
        </h2>
        <button type="button" onClick={onClose} aria-label={t('action.close')}>
          <X className="h-4 w-4" />
        </button>
      </header>

      {notice && (
        <InlineNotice variant={notice.tone === 'error' ? 'error' : 'info'}>
          {notice.text}
        </InlineNotice>
      )}

      <section className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--theme-text-muted)] hover:bg-[var(--theme-hover)]"
          disabled={loading}
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          {t('action.refresh')}
        </button>
        <select
          value={visibility}
          onChange={(e) => setVisibility(e.target.value as CompositionAssetVisibility)}
          aria-label={t('save.visibility')}
          className="rounded border border-[var(--theme-border)] bg-[var(--theme-surface)] px-1 py-0.5 text-xs"
        >
          <option value="private">{t('save.private')}</option>
          <option value="org">{t('save.org')}</option>
          <option value="project">{t('save.project')}</option>
        </select>
        {visibility === 'project' && (
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder={t('save.projectIdPlaceholder')}
            aria-label={t('save.projectId')}
            data-testid="project-id-input"
            className="w-24 rounded border border-[var(--theme-border)] bg-[var(--theme-surface)] px-1 py-0.5 text-xs"
          />
        )}
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!sessionId || saving}
          data-testid="save-my-template"
          className="inline-flex items-center gap-1 rounded bg-[var(--theme-accent)] px-2 py-1 text-xs text-white disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
          {t('save.mine')}
        </button>
      </section>

      <div className="flex-1 overflow-y-auto">
        {loading && assets.length === 0 ? (
          <div className="flex h-24 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--theme-text-muted)]" />
          </div>
        ) : assets.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title={t('empty.title')}
            description={t('empty.description')}
          />
        ) : (
          <>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
              {t('section.builtin')} ({builtins.length})
            </h3>
            <ul className="mb-3 space-y-1">{builtins.map(renderRow)}</ul>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
              {t('section.mine')} ({mine.length})
            </h3>
            <ul className="space-y-1">{mine.map(renderRow)}</ul>
          </>
        )}
      </div>
    </aside>
  );
}
