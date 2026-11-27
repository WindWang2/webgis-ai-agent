'use client';

/**
 * 只读地图发布作者面板（04 线 WP5）——工作台 exports 子 tab 的最小作者入口。
 *
 * 流程：预览计划（层/字段白名单/许可）→ 创建私有草稿 → **显式确认发布**
 * （durable job）→ 签发/撤销只读链接 → 版本/撤销。发布与链接签发都是
 * 作者显式动作（confirm=true 由按钮显式触发）；Agent 不能代行。
 * 本面板不重写 #2289 文件面板 / StoryMap / 图册。
 */
import React, { useCallback, useEffect, useId, useState } from 'react';

import { useToastStore } from '@/components/ui/toast';
import {
  createPublicationDraft,
  getPublication,
  listSessionPublications,
  mintPublicationLink,
  previewPublication,
  publishPublication,
  revokePublication,
  revokePublicationLink,
  type PublicationPlan,
  type PublicationView,
} from '@/lib/api/publication-authoring';
import { describeApiError } from '@/lib/api/transport';
import { useT } from '@/lib/i18n/useT';
import { getSessionIdentity } from '@/lib/store/session-identity';

type Step = 'plan' | 'draft' | 'published';

export function PublicationPanel() {
  const t = useT('publication');
  const panelId = useId();
  const [plan, setPlan] = useState<PublicationPlan | null>(null);
  const [publication, setPublication] = useState<PublicationView | null>(null);
  const [versions, setVersions] = useState<PublicationView['versions'] | undefined>(undefined);
  const [, setStep] = useState<Step>('plan');
  const [busy, setBusy] = useState(false);
  const [license, setLicense] = useState('');
  const [purpose, setPurpose] = useState('');
  const [attribution, setAttribution] = useState('');
  const [missingNote, setMissingNote] = useState('');
  const [shareUrl, setShareUrl] = useState('');
  const [jobId, setJobId] = useState('');
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [layerSelection, setLayerSelection] = useState<Record<string, boolean>>({});
  const [fieldSelection, setFieldSelection] = useState<Record<string, Record<string, boolean>>>({});

  const addToast = useToastStore((s) => s.addToast);

  const identity = () => {
    const { sessionId, ownerToken } = getSessionIdentity();
    return { sessionId, ownerToken };
  };

  const selectedLayerIds = () =>
    Object.entries(layerSelection).filter(([, v]) => v).map(([k]) => k);

  const whitelists = () => {
    const out: Record<string, string[]> = {};
    for (const [layerId, fields] of Object.entries(fieldSelection)) {
      const chosen = Object.entries(fields).filter(([, v]) => v).map(([k]) => k);
      if (chosen.length > 0) out[layerId] = chosen;
    }
    return out;
  };

  const handlePreview = useCallback(async () => {
    const { sessionId, ownerToken } = identity();
    if (!sessionId) {
      addToast(t('author.noSession'), 'error');
      return;
    }
    setBusy(true);
    try {
      const result = await previewPublication(sessionId, {
        selected_layer_ids: selectedLayerIds().length > 0 ? selectedLayerIds() : undefined,
        field_whitelists: Object.keys(fieldSelection).length > 0 ? whitelists() : undefined,
      }, ownerToken);
      setPlan(result);
      addToast(t('author.planReady'), 'success');
    } catch (err) {
      addToast(describeApiError(err, t('author.planFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerSelection, fieldSelection, t]);

  const handleCreateDraft = useCallback(async () => {
    const { sessionId, ownerToken } = identity();
    if (!sessionId || !plan) return;
    setBusy(true);
    try {
      const row = await createPublicationDraft(sessionId, {
        title: plan.title || undefined,
        purpose,
        attribution,
        license,
        missing_values_note: missingNote,
        selected_layer_ids: selectedLayerIds().length > 0 ? selectedLayerIds() : undefined,
        field_whitelists: Object.keys(fieldSelection).length > 0 ? whitelists() : undefined,
      }, ownerToken);
      setPublication(row);
      setStep('draft');
      addToast(t('author.draftCreated', { id: row.id.slice(0, 8) }), 'success');
    } catch (err) {
      addToast(describeApiError(err, t('author.draftFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, purpose, attribution, license, missingNote, layerSelection, fieldSelection, t]);

  const handlePublish = useCallback(async () => {
    if (!publication || !publishConfirmed) return;
    setBusy(true);
    try {
      const { ownerToken } = identity();
      const result = await publishPublication(publication.id, true, ownerToken);
      setJobId(result.job?.job_id ?? '');
      addToast(t('author.publishSubmitted', { jobId: result.job?.job_id ?? '—' }), 'success');
      // 轮询一次状态（eager 模式立即 published；worker 模式在任务中心可见）。
      setTimeout(async () => {
        try {
          const { ownerToken: tok } = identity();
          const fresh = await getPublication(publication.id, tok);
          setPublication(fresh);
          setVersions(fresh.versions);
          if (fresh.status === 'published') setStep('published');
        } catch { /* 轮询失败不打断主流程（任务中心可见） */ }
      }, 1200);
    } catch (err) {
      addToast(describeApiError(err, t('author.publishFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publication, publishConfirmed, t]);

  const handleMintLink = useCallback(async (ttlDays: number | null) => {
    if (!publication) return;
    setBusy(true);
    try {
      const { ownerToken } = identity();
      const link = await mintPublicationLink(publication.id, ttlDays, ownerToken);
      setShareUrl(link.share_url);
      addToast(t('author.linkMinted'), 'success');
    } catch (err) {
      addToast(describeApiError(err, t('author.linkFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publication, t]);

  const handleRevokeLink = useCallback(async () => {
    if (!publication) return;
    setBusy(true);
    try {
      const { ownerToken } = identity();
      await revokePublicationLink(publication.id, ownerToken);
      setShareUrl('');
      setPublication({ ...publication, has_share_link: false, access_policy: 'private' });
      addToast(t('author.linkRevoked'), 'success');
    } catch (err) {
      addToast(describeApiError(err, t('author.linkFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publication, t]);

  const handleRevokePublication = useCallback(async () => {
    if (!publication) return;
    setBusy(true);
    try {
      const { ownerToken } = identity();
      await revokePublication(publication.id, ownerToken);
      setPublication({ ...publication, status: 'revoked', has_share_link: false });
      setShareUrl('');
      addToast(t('author.revoked'), 'success');
    } catch (err) {
      addToast(describeApiError(err, t('author.revokeFailed')), 'error');
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publication, t]);

  // 列出本会话已有发布（版本历史）。
  useEffect(() => {
    const { sessionId, ownerToken } = identity();
    if (!sessionId) return;
    (async () => {
      try {
        const rows = await listSessionPublications(sessionId, ownerToken);
        if (rows.length > 0 && !publication) {
          setPublication(rows[0]);
          setVersions(rows[0].versions);
          if (rows[0].status === 'published') setStep('published');
          else if (rows[0].status === 'draft') setStep('draft');
        }
      } catch { /* 列表失败不打断 */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleAllFields = (layerId: string, fields: string[], value: boolean) => {
    setFieldSelection((prev) => ({
      ...prev,
      [layerId]: Object.fromEntries(fields.map((f) => [f, value])),
    }));
  };

  return (
    <div className="flex flex-col gap-3 text-body" aria-label={t('author.panelAria')}>
      <p className="text-caption text-ink-muted">{t('author.readonlyNotice')}</p>

      {/* Step 1：计划预览 */}
      <section aria-label={t('author.planSection')} className="flex flex-col gap-2">
        <label htmlFor={`${panelId}-license`} className="text-caption text-ink-muted">
          {t('author.license')} *
        </label>
        <input
          id={`${panelId}-license`}
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-body"
          value={license}
          onChange={(e) => setLicense(e.target.value)}
          placeholder="CC0-SIMULATED / ODbL / CC-BY-4.0"
        />
        <label htmlFor={`${panelId}-purpose`} className="text-caption text-ink-muted">{t('author.purpose')}</label>
        <input
          id={`${panelId}-purpose`}
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-body"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        />
        <label htmlFor={`${panelId}-attribution`} className="text-caption text-ink-muted">{t('author.attribution')}</label>
        <input
          id={`${panelId}-attribution`}
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-body"
          value={attribution}
          onChange={(e) => setAttribution(e.target.value)}
        />
        <label htmlFor={`${panelId}-missing`} className="text-caption text-ink-muted">{t('author.missingNote')}</label>
        <input
          id={`${panelId}-missing`}
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-body"
          value={missingNote}
          onChange={(e) => setMissingNote(e.target.value)}
        />
        <button
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1.5 text-body font-medium disabled:opacity-50"
          onClick={() => void handlePreview()}
          disabled={busy}
        >
          {t('author.previewPlan')}
        </button>
      </section>

      {plan && (
        <section aria-label={t('author.planResult')} className="flex flex-col gap-2 border-t border-edge-subtle pt-2">
          <div className="text-caption font-medium">
            {t('author.planTitle', { title: plan.title || '—' })}
            {plan.spec_valid === false && <span className="text-status-danger"> · {t('author.specInvalid')}</span>}
          </div>
          {plan.layers.map((layer) => (
            <div key={layer.layer_id} className="rounded border border-edge-subtle p-2">
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-1.5 text-caption">
                  <input
                    type="checkbox"
                    checked={layerSelection[layer.layer_id] ?? true}
                    onChange={(e) =>
                      setLayerSelection((prev) => ({ ...prev, [layer.layer_id]: e.target.checked }))
                    }
                    disabled={!layer.projectable}
                  />
                  <span className="font-medium">{layer.title || layer.layer_id}</span>
                </label>
                <span className="text-caption text-ink-muted">
                  {layer.projectable ? `${layer.feature_count}` : t('author.layerBlocked')}
                </span>
              </div>
              {layer.blocker && (
                <div className="mt-1 text-caption text-status-danger">{layer.blocker.message}</div>
              )}
              {layer.projectable && layer.candidate_fields.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <button
                    className="text-caption underline"
                    onClick={() => toggleAllFields(layer.layer_id, layer.candidate_fields, true)}
                  >
                    {t('author.allFields')}
                  </button>
                  <button
                    className="text-caption underline"
                    onClick={() => toggleAllFields(layer.layer_id, layer.candidate_fields, false)}
                  >
                    {t('author.noFields')}
                  </button>
                  {layer.candidate_fields.map((field) => (
                    <label key={field} className="flex items-center gap-1 text-caption">
                      <input
                        type="checkbox"
                        checked={fieldSelection[layer.layer_id]?.[field] ?? true}
                        onChange={(e) =>
                          setFieldSelection((prev) => ({
                            ...prev,
                            [layer.layer_id]: {
                              ...(prev[layer.layer_id] ?? Object.fromEntries(layer.candidate_fields.map((f) => [f, true]))),
                              [field]: e.target.checked,
                            },
                          }))
                        }
                      />
                      {field}
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
          {plan.blockers.length > 0 && (
            <ul className="list-disc pl-4 text-caption text-ink-muted">
              {plan.blockers.map((b, idx) => (
                <li key={idx}>{b.message}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Step 2：草稿 + 显式确认发布 */}
      <section aria-label={t('author.publishSection')} className="flex flex-col gap-2 border-t border-edge-subtle pt-2">
        <button
          className="rounded border border-edge-subtle bg-surface-raised px-2 py-1.5 text-body font-medium disabled:opacity-50"
          onClick={() => void handleCreateDraft()}
          disabled={busy || !plan}
        >
          {t('author.createDraft')}
        </button>

        {publication && (
          <div className="rounded border border-edge-subtle p-2 text-caption">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{publication.title}</span>
              <span className="rounded border border-edge-subtle px-1">{t('author.statusLabel', { status: publication.status })}</span>
              <span>v{publication.version}</span>
              {publication.materialized && <span className="text-ink-muted">{t('author.materialized')}</span>}
            </div>
            {publication.status === 'draft' && (
              <div className="mt-2 flex flex-col gap-1.5">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={publishConfirmed}
                    onChange={(e) => setPublishConfirmed(e.target.checked)}
                  />
                  {t('author.confirmPublish')}
                </label>
                <button
                  className="rounded bg-[var(--accent, #2563eb)] px-2 py-1.5 font-medium text-white disabled:opacity-50"
                  onClick={() => void handlePublish()}
                  disabled={busy || !publishConfirmed}
                >
                  {t('author.publish')}
                </button>
              </div>
            )}
            {publication.status === 'published' && (
              <div className="mt-2 flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                  <select
                    aria-label={t('author.ttl')}
                    defaultValue="7"
                    onChange={() => { /* 读值在 mint 时 */ }}
                    id={`${panelId}-ttl`}
                    className="rounded border border-edge-subtle px-1 py-0.5"
                  >
                    <option value="7">{t('author.ttl7')}</option>
                    <option value="30">{t('author.ttl30')}</option>
                    <option value="">{t('author.ttlForever')}</option>
                  </select>
                  <button
                    className="rounded border border-edge-subtle px-2 py-1 font-medium disabled:opacity-50"
                    onClick={() => {
                      const raw = (document.getElementById(`${panelId}-ttl`) as HTMLSelectElement | null)?.value;
                      void handleMintLink(raw ? Number(raw) : null);
                    }}
                    disabled={busy}
                  >
                    {t('author.mintLink')}
                  </button>
                  <button
                    className="rounded border border-edge-subtle px-2 py-1 disabled:opacity-50"
                    onClick={() => void handleRevokeLink()}
                    disabled={busy || !publication.has_share_link}
                  >
                    {t('author.revokeLink')}
                  </button>
                </div>
                {shareUrl && (
                  <div className="rounded bg-surface-sunken p-1.5">
                    <a href={shareUrl} target="_blank" rel="noreferrer" className="underline">
                      {shareUrl}
                    </a>
                    <button
                      className="ml-2 underline"
                      onClick={() => {
                        void navigator.clipboard?.writeText(`${window.location.origin}${shareUrl}`);
                        addToast(t('author.copied'), 'success');
                      }}
                    >
                      {t('author.copy')}
                    </button>
                  </div>
                )}
                <button
                  className="self-start rounded border border-status-danger px-2 py-1 text-status-danger disabled:opacity-50"
                  onClick={() => void handleRevokePublication()}
                  disabled={busy}
                >
                  {t('author.revokePublication')}
                </button>
                {jobId && <div className="text-ink-muted">{t('author.jobRef', { jobId })}</div>}
              </div>
            )}
            {publication.blockers.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-ink-muted">
                {publication.blockers.map((b, idx) => <li key={idx}>{b.message}</li>)}
              </ul>
            )}
          </div>
        )}

        {versions && versions.length > 0 && (
          <div className="text-caption">
            <div className="font-medium">{t('author.versions')}</div>
            <ul className="list-disc pl-4">
              {versions.map((v) => (
                <li key={v.id}>
                  v{v.version} · {t('author.statusLabel', { status: v.status })} · {v.published_at ? v.published_at.slice(0, 10) : '—'}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
