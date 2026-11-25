'use client';

/**
 * G43（ADR-0295）：会话可选资料工作台（WP09）—— 项目知识条目/制图记忆
 * 事实/GIS 空间记忆块的优先与排除，带无副作用预览与 CAS 冲突处理。
 *
 * 纪律：
 * - 选择只提交服务端铸造的 source_id（目录经授权过滤；未知/越权 id 服务
 *   端统一拒绝）；409 冲突展示当前值并让用户合并重试（C12）；
 * - 措辞固定披露「下一轮起生效」「排除不删除共享事实、不遗忘历史」；
 * - 预览明确标注 is_preview（不是发送回执；tokens 是估算）。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { BookMarked, ChevronDown, ChevronUp, Eye, ShieldAlert } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  fetchOptionalSources,
  fetchSourcePolicy,
  postContextPreview,
  putSourcePolicy,
  PolicyConflictError,
  type OptionalSourceView,
  type SessionSourcePolicyView,
  type TurnContextPreview,
} from '@/lib/api/turnContext';

const KIND_LABEL_KEYS: Record<string, string> = {
  project_knowledge_entry: 'chat.sources.kindKnowledge',
  carto_memory_fact: 'chat.sources.kindMemory',
  gis_memory_block: 'chat.sources.kindGisMemory',
};

export function TurnContextSources({
  sessionId,
  ownerToken,
}: {
  sessionId: string | null;
  ownerToken?: string | null;
}) {
  const t = useT();
  // 与 useMapBridge 项目上下文注入同一镜像（#558）：项目 tab 的选择是
  // activeProjectId 的唯一真相，这里只读。
  const projectId = useHudStore((s) => s.activeProjectId);
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<OptionalSourceView[]>([]);
  const [sourcesError, setSourcesError] = useState(false);
  const [view, setView] = useState<SessionSourcePolicyView | null>(null);
  const [preferred, setPreferred] = useState<Set<string>>(new Set());
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<'auto' | 'explicit'>('auto');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<SessionSourcePolicyView['policy'] | null>(null);
  const [preview, setPreview] = useState<TurnContextPreview | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    try {
      const v = await fetchSourcePolicy(sessionId, ownerToken);
      setView(v);
      setMode(v.policy.mode === 'explicit' ? 'explicit' : 'auto');
      setPreferred(new Set(v.policy.preferred ?? []));
      setExcluded(new Set(v.policy.excluded ?? []));
      setConflict(null);
    } catch {
      setView(null);
    }
    if (projectId) {
      try {
        const s = await fetchOptionalSources(sessionId, projectId, ownerToken);
        setSources(s.sources ?? []);
        setSourcesError(false);
      } catch {
        setSources([]);
        setSourcesError(true);
      }
    } else {
      setSources([]);
      setSourcesError(false);
    }
  }, [sessionId, projectId, ownerToken]);

  useEffect(() => {
    if (open && !view) void load();
  }, [open, view, load]);

  const dirty = useMemo(() => {
    if (!view) return false;
    const base = view.policy;
    const same = (a: Set<string>, b: string[]) =>
      a.size === b.length && b.every((x) => a.has(x));
    return mode !== base.mode
      || !same(preferred, base.preferred ?? [])
      || !same(excluded, base.excluded ?? []);
  }, [view, mode, preferred, excluded]);

  const toggle = (id: string, next: 'preferred' | 'excluded' | 'auto') => {
    setPreferred((prev) => {
      const s = new Set(prev);
      s.delete(id);
      if (next === 'preferred') s.add(id);
      return s;
    });
    setExcluded((prev) => {
      const s = new Set(prev);
      s.delete(id);
      if (next === 'excluded') s.add(id);
      return s;
    });
    if (mode !== 'explicit') setMode('explicit');
  };

  const save = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    setNotice(null);
    setConflict(null);
    try {
      const v = await putSourcePolicy(
        sessionId,
        {
          mode,
          preferred: [...preferred],
          excluded: [...excluded],
          expected_version: view?.policy.policy_version ?? 0,
          project_id: projectId ?? '',
        },
        ownerToken,
      );
      setView(v);
      setNotice(t('chat.sources.saved'));
    } catch (err) {
      if (err instanceof PolicyConflictError) {
        setConflict(err.current);
        setNotice(t('chat.sources.conflict'));
      } else {
        setNotice(t('chat.sources.saveFailed'));
      }
    } finally {
      setBusy(false);
    }
  }, [sessionId, mode, preferred, excluded, view, projectId, ownerToken, t]);

  const runPreview = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      const p = await postContextPreview(
        sessionId,
        {
          project_id: projectId ?? '',
          policy_draft: mode === 'explicit'
            ? {
                mode,
                preferred: [...preferred],
                excluded: [...excluded],
                expected_version: 0,
                project_id: projectId ?? '',
              }
            : undefined,
        },
        ownerToken,
      );
      setPreview(p);
    } catch {
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }, [sessionId, projectId, mode, preferred, excluded, ownerToken]);

  if (!sessionId) return null;

  return (
    <div className="border-b border-edge-subtle px-panel py-1.5" data-testid="turn-context-sources">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="turn-context-sources-toggle"
        className="flex w-full items-center justify-between text-[12px] text-ink-muted hover:text-ink-standard"
      >
        <span className="inline-flex items-center gap-1">
          <BookMarked size={11} aria-hidden />
          {t('chat.sources.label')}
          {view?.policy.mode === 'explicit' && (
            <span className="rounded-pill bg-surface-raised px-1 text-[10px]">
              {t('chat.sources.explicitBadge')}
            </span>
          )}
        </span>
        {open ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />}
      </button>

      {open && (
        <div className="mt-1.5 space-y-1.5 text-[11.5px]" data-testid="turn-context-sources-body">
          <p className="text-ink-muted">{t('chat.sources.scopeNote')}</p>
          {!projectId && <p className="text-ink-muted">{t('chat.sources.noProject')}</p>}
          {projectId && sourcesError && (
            <p className="text-status-warning" data-testid="turn-sources-error">
              {t('chat.turnFailedDefault')}
            </p>
          )}
          {projectId && !sourcesError && sources.length === 0 && (
            <p className="text-ink-muted">{t('chat.sources.empty')}</p>
          )}
          {sources.length > 0 && (
            <ul className="max-h-52 space-y-0.5 overflow-y-auto">
              {sources.map((s) => {
                const state = excluded.has(s.source_id)
                  ? 'excluded'
                  : preferred.has(s.source_id) ? 'preferred' : 'auto';
                return (
                  <li
                    key={s.source_id}
                    className="flex items-center justify-between gap-2 rounded-sm px-1 py-0.5 hover:bg-surface-raised"
                    data-testid={`turn-source-${s.source_id}`}
                  >
                    <span className="min-w-0 flex-1 truncate" title={`${s.label} ${s.version_token ? `@${s.version_token}` : ''}`}>
                      <span className="text-ink-faint">{t(KIND_LABEL_KEYS[s.kind] ?? 'chat.sources.kindKnowledge')}·</span>
                      {s.label || s.source_id}
                      {s.version_token && (
                        <span className="ml-1 font-mono text-ink-faint">{s.version_token.slice(0, 12)}</span>
                      )}
                    </span>
                    <span className="flex shrink-0 items-center gap-0.5" role="group" aria-label={s.label || s.source_id}>
                      {(['preferred', 'auto', 'excluded'] as const).map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          disabled={busy}
                          aria-pressed={state === opt}
                          onClick={() => toggle(s.source_id, opt)}
                          className={
                            state === opt
                              ? 'rounded-pill border border-edge-strong bg-surface-raised px-1.5 py-0.5 text-[10px] text-ink-standard'
                              : 'rounded-pill border border-edge-subtle px-1.5 py-0.5 text-[10px] text-ink-muted'
                          }
                          data-testid={`turn-source-${s.source_id}-${opt}`}
                        >
                          {t(`chat.sources.state_${opt}`)}
                        </button>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              disabled={busy || !dirty || !projectId}
              onClick={() => void save()}
              className="rounded-pill border border-edge-strong px-2 py-0.5 text-[11px] disabled:opacity-50"
              data-testid="turn-sources-save"
            >
              {t('chat.sources.save')}
            </button>
            <button
              type="button"
              disabled={busy || !projectId}
              onClick={() => void runPreview()}
              className="inline-flex items-center gap-1 rounded-pill border border-edge-subtle px-2 py-0.5 text-[11px] disabled:opacity-50"
              data-testid="turn-sources-preview"
            >
              <Eye size={10} aria-hidden />
              {t('chat.sources.preview')}
            </button>
            {mode === 'explicit' && dirty && (
              <button
                type="button"
                onClick={() => {
                  setMode('auto');
                  setPreferred(new Set());
                  setExcluded(new Set());
                }}
                className="rounded-pill border border-edge-subtle px-2 py-0.5 text-[11px] text-ink-muted"
              >
                {t('chat.sources.resetAuto')}
              </button>
            )}
          </div>

          {notice && <p className="text-ink-muted" data-testid="turn-sources-notice">{notice}</p>}
          {conflict && (
            <div className="flex items-start gap-1 rounded-sm border border-warning/40 bg-warning/5 px-1.5 py-1" data-testid="turn-sources-conflict">
              <ShieldAlert size={11} className="mt-0.5 shrink-0 text-warning" aria-hidden />
              <div className="text-ink-muted">
                <p>{t('chat.sources.conflictDetail')}</p>
                <button
                  type="button"
                  className="underline"
                  onClick={() => void load()}
                >
                  {t('chat.sources.conflictRefresh')}
                </button>
              </div>
            </div>
          )}
          {preview && (
            <div className="rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-1" data-testid="turn-sources-preview-body">
              <p className="text-ink-muted">{t('chat.sources.previewNote')}</p>
              <ul className="mt-0.5">
                {preview.blocks.map((b) => (
                  <li key={b.domain} className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-ink-faint">{b.domain}</span>
                    <span className={b.available ? '' : 'text-ink-faint'}>
                      {b.available ? `~${b.est_tokens} tk` : (b.reason || '—')}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-0.5 text-ink-faint">
                {t('chat.sources.previewTokens')}
                <b className="text-ink-standard">
                  {' '}{preview.estimate.optional_priors_tokens}
                </b>
                {' · '}
                {t('chat.sources.previewDisclaimer')}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
