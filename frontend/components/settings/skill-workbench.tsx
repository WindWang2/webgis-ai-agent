'use client';

import { useT } from '@/lib/i18n/useT';
import { devOnly } from '@/lib/utils/logger';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { STitle } from '@/components/shared/section-title';
import { getSessionIdentity, subscribeSessionIdentity } from '@/lib/store/session-identity';
import { getAuthUser, subscribeAuth } from '@/lib/auth/tokenStore';
import {
  SkillJobApiError,
  cancelSkillJobRun,
  createPreset,
  getCatalog,
  getSkillJobRun,
  listPresets,
  listSkillJobRuns,
  prepareSkillJob,
  startSkillJob,
  type PrepareResponse,
  type SkillCatalogEntry,
  type SkillJobPreset,
  type SkillJobRun,
} from '@/lib/api/skill-jobs';

/**
 * G30 技能工作台：选版本 → 绑数据/字段 → 参数 → 计划预览 → 启动 → 进度与
 * 结果 → 复用配置。目录分型（可执行技能 / 方法指南 / 扩展 candidate）；
 * typed 错误定位到字段/数据角色；刷新后经 durable run 列表回看运行。
 *
 * 红线对应：目录不合并同名资产；guidance_only/deprecated 如实标注且不可
 * 启动；预览零副作用（后端 prepare）；启动幂等；运行状态轮询 durable 行。
 */

type Phase = 'catalog' | 'configure' | 'preview' | 'running';

interface BindingInput {
  role: string;
  mode: 'ref' | 'inline';
  ref: string;
  inlineText: string;
}

function runnabilityBadge(entry: SkillCatalogEntry, labels: Record<'runnable' | 'deprecated' | 'unavailable' | 'guidanceOnly', string>): { text: string; cls: string } {
  switch (entry.runnability) {
    case 'runnable':
      return { text: labels.runnable, cls: 'bg-emerald-500/15 text-emerald-500' };
    case 'deprecated':
      return { text: labels.deprecated, cls: 'bg-amber-500/15 text-amber-500' };
    case 'unavailable':
      return { text: labels.unavailable, cls: 'bg-red-500/15 text-red-400' };
    default:
      return { text: labels.guidanceOnly, cls: 'bg-slate-500/15 text-slate-400' };
  }
}

type WorkbenchIdentity = ReturnType<typeof getSessionIdentity> & { userId: string | null };
type WorkbenchScope = { identity: WorkbenchIdentity; generation: number; controller: AbortController };

function readWorkbenchIdentity(): WorkbenchIdentity {
  return { ...getSessionIdentity(), userId: getAuthUser()?.id ?? null };
}

function sameIdentity(left: WorkbenchIdentity, right: WorkbenchIdentity) {
  return left.userId === right.userId && left.sessionId === right.sessionId && left.ownerToken === right.ownerToken;
}

export function SkillWorkbench() {
  const [scope, setScope] = useState<WorkbenchScope>(() => ({
    identity: readWorkbenchIdentity(), generation: 0, controller: new AbortController(),
  }));
  const scopeRef = useRef(scope);

  useEffect(() => {
    const syncIdentity = () => {
      const current = scopeRef.current;
      const identity = readWorkbenchIdentity();
      if (!current.controller.signal.aborted && sameIdentity(current.identity, identity)) return;
      // Invalidate synchronously: stale DOM handlers must not borrow the next Bearer token.
      current.controller.abort();
      const next = { identity, generation: current.generation + 1, controller: new AbortController() };
      scopeRef.current = next;
      setScope(next);
    };
    const unsubscribeSession = subscribeSessionIdentity(syncIdentity);
    const unsubscribeAuth = subscribeAuth(syncIdentity);
    // Covers store changes before subscription, and StrictMode's setup/cleanup cycle.
    syncIdentity();
    return () => {
      unsubscribeSession();
      unsubscribeAuth();
      scopeRef.current.controller.abort();
    };
  }, []);

  // Every cached value/form and polling lifetime belongs to one immutable identity scope.
  return <IdentitySkillWorkbench key={scope.generation} scope={scope} />;
}

function IdentitySkillWorkbench({ scope }: { scope: WorkbenchScope }) {
  const t = useT('settings.skillWorkbench');
  const badgeLabels = {
    runnable: t('runnable'), deprecated: t('deprecated'),
    unavailable: t('unavailable'), guidanceOnly: t('guidanceOnly'),
  };
  const [catalog, setCatalog] = useState<SkillCatalogEntry[]>([]);
  const [guides, setGuides] = useState<SkillCatalogEntry[]>([]);
  const [candidates, setCandidates] = useState<SkillCatalogEntry[]>([]);
  const [tab, setTab] = useState<'skills' | 'guides' | 'candidates'>('skills');
  const [selected, setSelected] = useState<SkillCatalogEntry | null>(null);
  const [phase, setPhase] = useState<Phase>('catalog');
  const [params, setParams] = useState<Record<string, string>>({});
  const [bindings, setBindings] = useState<BindingInput[]>([]);
  const [prepared, setPrepared] = useState<PrepareResponse | null>(null);
  const [run, setRun] = useState<SkillJobRun | null>(null);
  const [runs, setRuns] = useState<SkillJobRun[]>([]);
  const [presets, setPresets] = useState<SkillJobPreset[]>([]);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [presetName, setPresetName] = useState('');
  const { sessionId, ownerToken } = scope.identity;
  const isCurrent = useCallback(() => (
    !scope.controller.signal.aborted && sameIdentity(scope.identity, readWorkbenchIdentity())
  ), [scope]);
  const requestOptions = useCallback(() => ({
    ownerToken: sessionId ? ownerToken : undefined,
    signal: scope.controller.signal,
  }), [sessionId, ownerToken, scope]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    if (!isCurrent()) return;
    const options = requestOptions();
    getCatalog(options)
      .then((c) => {
        if (!isCurrent()) return;
        setCatalog(c.skills || []);
        setGuides(c.md_guides || []);
        setCandidates(c.extension_candidates || []);
      })
      .catch((err: unknown) => { if (isCurrent()) devOnly.warn('SkillWorkbench: catalog failed', err); });
    listPresets(options)
      .then((p) => { if (isCurrent()) setPresets(p.presets || []); })
      .catch(() => undefined);
  }, [requestOptions, isCurrent]);

  const refreshRuns = useCallback((sid: string) => {
    if (!isCurrent() || sid !== sessionId) return;
    const options = requestOptions();
    listSkillJobRuns(sid, options)
      .then((r) => { if (isCurrent()) setRuns(r.runs || []); })
      .catch(() => undefined);
  }, [requestOptions, isCurrent, sessionId]);

  useEffect(() => {
    if (sessionId) refreshRuns(sessionId);
  }, [sessionId, refreshRuns]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      stopPolling();
      if (!isCurrent() || !sessionId) return;
      const options = requestOptions();
      pollRef.current = setInterval(() => {
        if (!isCurrent()) { stopPolling(); return; }
        getSkillJobRun(jobId, sessionId, options)
          .then((r) => {
            if (!isCurrent()) return;
            setRun(r);
            if (!r.active) {
              stopPolling();
              if (sessionId) refreshRuns(sessionId);
            }
          })
          .catch(() => undefined);
      }, 2500);
    },
    [refreshRuns, sessionId, stopPolling, requestOptions, isCurrent],
  );

  const onSelectSkill = (entry: SkillCatalogEntry) => {
    if (!isCurrent()) return;
    stopPolling();
    setError(null);
    setSelected(entry);
    setPrepared(null);
    setRun(null);
    setPhase('configure');
    const defaults: Record<string, string> = {};
    (entry.parameters || []).forEach((p) => {
      if (p.default !== undefined && p.default !== null) defaults[p.name] = String(p.default);
    });
    setParams(defaults);
    setBindings(
      (entry.role_bindings || [])
        .filter((rb) => rb.required)
        .map((rb) => ({ role: rb.role, mode: 'ref' as const, ref: '', inlineText: '' })),
    );
  };

  const buildBindings = () =>
    bindings
      .filter((b) => (b.mode === 'ref' ? b.ref.trim() : b.inlineText.trim()))
      .map((b) =>
        b.mode === 'ref'
          ? { role: b.role, ref: b.ref.trim() }
          : { role: b.role, inline: JSON.parse(b.inlineText) },
      );

  const onPrepare = async () => {
    if (!isCurrent() || !selected || !sessionId) return;
    setBusy(true);
    setError(null);
    const options = requestOptions();
    try {
      const typedParams: Record<string, unknown> = {};
      (selected.parameters || []).forEach((spec) => {
        const raw = params[spec.name];
        if (raw === undefined || raw === '') return;
        if (spec.type === 'number') typedParams[spec.name] = Number(raw);
        else if (spec.type === 'boolean') typedParams[spec.name] = raw === 'true';
        else typedParams[spec.name] = raw;
      });
      const res = await prepareSkillJob({
        session_id: sessionId,
        skill_id: selected.id,
        skill_version: selected.version || '',
        params: typedParams,
        bindings: buildBindings(),
      }, options);
      if (!isCurrent()) return;
      setPrepared(res);
      setPhase('preview');
    } catch (err) {
      if (!isCurrent()) return;
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
      else setError({ code: 'PREPARE_FAILED', message: String(err).slice(0, 160) });
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const onStart = async () => {
    if (!isCurrent() || !prepared || !sessionId) return;
    setBusy(true);
    setError(null);
    const options = requestOptions();
    try {
      const r = await startSkillJob({ prepared_id: prepared.prepared_id, session_id: sessionId }, options);
      if (!isCurrent()) return;
      setRun(r);
      setPhase('running');
      if (r.active) startPolling(r.job_id);
      else refreshRuns(sessionId);
    } catch (err) {
      if (!isCurrent()) return;
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
      else setError({ code: 'START_FAILED', message: String(err).slice(0, 160) });
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!isCurrent() || !run || !sessionId) return;
    try {
      await cancelSkillJobRun(run.job_id, sessionId, requestOptions());
    } catch {
      /* 取消请求失败：下一轮轮询如实反映 */
    }
  };

  const onSavePreset = async () => {
    if (!isCurrent() || !selected || !presetName.trim()) return;
    setBusy(true);
    const options = requestOptions();
    try {
      await createPreset({
        name: presetName.trim(),
        skill_id: selected.id,
        skill_version: selected.version || '',
        params: params,
        bindings_template: Object.fromEntries(
          bindings.map((b) => [b.role, { last_ref: b.ref, mode: b.mode }]),
        ),
      }, options);
      if (!isCurrent()) return;
      setPresetName('');
      listPresets(options).then((p) => { if (isCurrent()) setPresets(p.presets || []); }).catch(() => undefined);
    } catch (err) {
      if (!isCurrent()) return;
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const onReusePreset = async (preset: SkillJobPreset) => {
    if (!isCurrent() || !sessionId || !presets.includes(preset)) return;
    setBusy(true);
    setError(null);
    try {
      const entry =
        catalog.find((s) => s.id === preset.skill_id) || null;
      if (entry) {
        setSelected(entry);
        setParams(
          Object.fromEntries(
            Object.entries(preset.params || {}).map(([k, v]) => [k, String(v)]),
          ),
        );
        const tmpl = preset.bindings_template || {};
        setBindings(
          (entry.role_bindings || [])
            .filter((rb) => rb.required)
            .map((rb) => {
              const hint = (tmpl[rb.role] || {}) as { last_ref?: string; mode?: string };
              return {
                role: rb.role,
                mode: hint.mode === 'inline' ? ('inline' as const) : ('ref' as const),
                ref: hint.last_ref || '',
                inlineText: '',
              };
            }),
        );
        setPhase('configure');
      }
      // 预设重用也可以直接走后端（参数来源=preset 标注）——此处采用
      // 前端预填 + 普通 prepare，让用户先核对绑定（ref 过期会 typed 报错）。
      await Promise.resolve();
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const activeList = tab === 'skills' ? catalog : tab === 'guides' ? guides : candidates;
  const paramSpecs = useMemo(() => selected?.parameters || [], [selected]);

  return (
    <section className="space-y-4" data-testid="skill-workbench">
      <STitle title={t('title')} sub={t('subtitle')} />

      {!sessionId && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-300">
          {t('noSession')}
        </div>
      )}
      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300"
          data-error-code={error.code}
        >
          <span className="font-mono text-xs">{error.code}</span> — {error.message}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* ── 目录 ── */}
        <div className="space-y-2">
          <div className="flex gap-1 text-xs">
            {([
              ['skills', t('skillsTab', { p0: catalog.length })],
              ['guides', t('guidesTab', { p0: guides.length })],
              ['candidates', t('extensionsTab', { p0: candidates.length })],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`rounded px-2 py-1 ${tab === key ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:bg-muted'}`}
                data-testid={`tab-${key}`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
            {activeList.map((entry) => {
              const badge = entry.runnability ? runnabilityBadge(entry, badgeLabels) : null;
              return (
                <button
                  key={`${entry.asset_type}:${entry.id}`}
                  onClick={() => entry.asset_type === 'gis_skill' && onSelectSkill(entry)}
                  className={`w-full rounded-md border p-2 text-left text-sm transition-colors ${
                    selected?.id === entry.id && entry.asset_type === 'gis_skill'
                      ? 'border-primary/60 bg-primary/10'
                      : 'border-border hover:bg-muted'
                  } ${entry.asset_type === 'gis_skill' ? 'cursor-pointer' : 'cursor-default opacity-75'}`}
                  data-testid={`skill-${entry.id}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">{entry.name}</span>
                    {badge && (
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${badge.cls}`}>
                        {badge.text}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="font-mono">{entry.id}</span>
                    {entry.version && <span>v{entry.version}</span>}
                    {entry.domain && <span>· {entry.domain}</span>}
                  </div>
                </button>
              );
            })}
            {activeList.length === 0 && (
              <p className="p-2 text-xs text-muted-foreground">{t('empty')}</p>
            )}
          </div>
        </div>

        {/* ── 主面板 ── */}
        <div className="min-h-[420px] space-y-4 rounded-lg border border-border p-4">
          {!selected && <p className="text-sm text-muted-foreground">{t('selectSkill')}</p>}

          {selected && (
            <>
              <header className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-base font-semibold">{selected.name}</h3>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                    {selected.id} v{selected.version}
                  </span>
                  {(() => {
                    const badge = runnabilityBadge(selected, badgeLabels);
                    return (
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${badge.cls}`}>
                        {badge.text}
                      </span>
                    );
                  })()}
                </div>
                <p className="text-sm text-muted-foreground">{selected.description}</p>
                {(selected.runnability_reasons || []).length > 0 && (
                  <ul className="list-inside list-disc text-xs text-amber-400/90">
                    {selected.runnability_reasons!.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                )}
              </header>

              {phase === 'configure' && (
                <div className="space-y-4" data-testid="configure-panel">
                  {/* 数据角色绑定 */}
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">{t('bindings')}</legend>
                    {bindings.map((b, i) => (
                      <div key={b.role} className="space-y-1 rounded-md border border-border p-2">
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono">{b.role}</span>
                          <span className="text-muted-foreground">
                            {(selected.role_bindings || []).find((rb) => rb.role === b.role)
                              ?.geometry_kinds.join('/') || t('anyGeometry')}
                          </span>
                        </div>
                        <div className="flex gap-1 text-xs">
                          <button
                            className={`rounded px-2 py-0.5 ${b.mode === 'ref' ? 'bg-primary/20' : 'bg-muted'}`}
                            onClick={() =>
                              setBindings((arr) =>
                                arr.map((x, j) => (j === i ? { ...x, mode: 'ref' } : x)),
                              )
                            }
                          >
                            {t('sessionRef')}
                          </button>
                          <button
                            className={`rounded px-2 py-0.5 ${b.mode === 'inline' ? 'bg-primary/20' : 'bg-muted'}`}
                            onClick={() =>
                              setBindings((arr) =>
                                arr.map((x, j) => (j === i ? { ...x, mode: 'inline' } : x)),
                              )
                            }
                          >
                            {t('inlineGeojson')}
                          </button>
                        </div>
                        {b.mode === 'ref' ? (
                          <input
                            className="w-full rounded border border-border bg-background px-2 py-1 font-mono text-xs"
                            placeholder={t('refPlaceholder')}
                            value={b.ref}
                            onChange={(e) =>
                              setBindings((arr) =>
                                arr.map((x, j) => (j === i ? { ...x, ref: e.target.value } : x)),
                              )
                            }
                            data-testid={`binding-ref-${b.role}`}
                          />
                        ) : (
                          <textarea
                            className="h-24 w-full rounded border border-border bg-background px-2 py-1 font-mono text-xs"
                            placeholder='{"type":"FeatureCollection","features":[…]}'
                            value={b.inlineText}
                            onChange={(e) =>
                              setBindings((arr) =>
                                arr.map((x, j) =>
                                  j === i ? { ...x, inlineText: e.target.value } : x,
                                ),
                              )
                            }
                          />
                        )}
                      </div>
                    ))}
                    {bindings.length === 0 && (
                      <p className="text-xs text-muted-foreground">{t('noBindings')}</p>
                    )}
                  </fieldset>

                  {/* 参数表单 */}
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">{t('parameters')}</legend>
                    {paramSpecs.map((spec) => (
                      <label key={spec.name} className="block space-y-1 text-xs">
                        <span className="flex items-center gap-1">
                          <span className="font-medium">{spec.label || spec.name}</span>
                          {spec.required && <span className="text-red-400">*</span>}
                          <span className="font-mono text-muted-foreground">({spec.type}</span>
                          {spec.unit && <span className="font-mono text-muted-foreground">, {spec.unit}</span>
                          }
                          <span className="font-mono text-muted-foreground">)</span>
                        </span>
                        {spec.description && (
                          <span className="block text-muted-foreground">{spec.description}</span>
                        )}
                        {spec.type === 'enum' ? (
                          <select
                            className="w-full rounded border border-border bg-background px-2 py-1"
                            value={params[spec.name] ?? ''}
                            onChange={(e) =>
                              setParams((p) => ({ ...p, [spec.name]: e.target.value }))
                            }
                          >
                            <option value="">{t('notProvided')}</option>
                            {(spec.enum_values || []).map((v) => (
                              <option key={v} value={v}>
                                {v}
                              </option>
                            ))}
                          </select>
                        ) : spec.type === 'boolean' ? (
                          <select
                            className="w-full rounded border border-border bg-background px-2 py-1"
                            value={params[spec.name] ?? ''}
                            onChange={(e) =>
                              setParams((p) => ({ ...p, [spec.name]: e.target.value }))
                            }
                          >
                            <option value="">{t('notProvided')}</option>
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : (
                          <input
                            className="w-full rounded border border-border bg-background px-2 py-1"
                            type={spec.type === 'number' ? 'number' : 'text'}
                            value={params[spec.name] ?? ''}
                            onChange={(e) =>
                              setParams((p) => ({ ...p, [spec.name]: e.target.value }))
                            }
                            data-testid={`param-${spec.name}`}
                          />
                        )}
                      </label>
                    ))}
                    {paramSpecs.length === 0 && (
                      <p className="text-xs text-muted-foreground">{t('noParameters')}</p>
                    )}
                  </fieldset>

                  <button
                    className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                    disabled={busy || !sessionId || selected.runnability !== 'runnable'}
                    onClick={onPrepare}
                    data-testid="prepare-btn"
                  >
                    {busy ? t('preparing') : t('previewPlan')}
                  </button>
                  {selected.runnability !== 'runnable' && (
                    <p className="text-xs text-muted-foreground">
                      {t('guidancePrefix')}{selected.runnability}{t('guidanceSuffix')}
                    </p>
                  )}

                  {/* 预设 */}
                  <details className="rounded-md border border-border p-2">
                    <summary className="cursor-pointer text-xs font-medium">
                      {t('presetsPrefix')}{presets.length}{t('closeParen')}
                    </summary>
                    <div className="mt-2 flex gap-1">
                      <input
                        className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs"
                        placeholder={t('presetPlaceholder')}
                        value={presetName}
                        onChange={(e) => setPresetName(e.target.value)}
                      />
                      <button
                        className="rounded bg-muted px-2 py-1 text-xs disabled:opacity-50"
                        disabled={busy || !presetName.trim()}
                        onClick={onSavePreset}
                      >
                        {t('save')}
                      </button>
                    </div>
                    <ul className="mt-2 space-y-1">
                      {presets.map((p) => (
                        <li key={p.id} className="flex items-center justify-between gap-2 text-xs">
                          <span className="truncate">
                            {p.name} <span className="font-mono text-muted-foreground">{p.skill_id}@{p.skill_version}</span>
                          </span>
                          <button
                            className="rounded bg-muted px-2 py-0.5"
                            disabled={busy}
                            onClick={() => onReusePreset(p)}
                          >
                            {t('reuse')}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}

              {phase === 'preview' && prepared && (
                <div className="space-y-3" data-testid="preview-panel">
                  <h4 className="text-sm font-medium">
                    {t('previewPrefix')} {prepared.preview.skill.digest}{t('closeParen')}
                  </h4>
                  <ol className="space-y-1 text-xs">
                    {prepared.preview.steps.map((st) => (
                      <li key={st.step_id} className="rounded border border-border p-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono">{st.step_id}</span>
                          <span className="rounded bg-muted px-1">{st.tool}</span>
                          <span className="text-muted-foreground">{st.side_effect}</span>
                        </div>
                        <div className="mt-0.5 text-muted-foreground">
                          {t('readRoles')}{st.reads_roles.join(', ') || '—'}  {t('evidence')}
                          {st.produces_evidence.join(', ') || '—'}
                        </div>
                      </li>
                    ))}
                  </ol>
                  <div className="text-xs">
                    <div>
                      {t('coverage')}{prepared.preview.evidence_covered.join(', ') || '—'}
                    </div>
                    {prepared.preview.evidence_not_covered.length > 0 && (
                      <div className="text-amber-400">
                        {t('uncovered')}{prepared.preview.evidence_not_covered.join(', ')}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                      disabled={busy}
                      onClick={onStart}
                      data-testid="start-btn"
                    >
                      {busy ? t('starting') : t('startRun')}
                    </button>
                    <button
                      className="rounded-md bg-muted px-4 py-2 text-sm"
                      onClick={() => setPhase('configure')}
                    >
                      {t('back')}
                    </button>
                  </div>
                </div>
              )}

              {(phase === 'running' || run) && run && (
                <div className="space-y-2 text-sm" data-testid="run-panel">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{t('run')} {run.job_id}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        run.status === 'completed'
                          ? 'bg-emerald-500/15 text-emerald-500'
                          : run.status === 'failed'
                            ? 'bg-red-500/15 text-red-400'
                            : 'bg-blue-500/15 text-blue-400'
                      }`}
                    >
                      {run.status}
                    </span>
                    {run.idempotent_reuse && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                        {t('idempotentReuse')}
                      </span>
                    )}
                    {run.already_running && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                        {t('alreadyRunning')}
                      </span>
                    )}
                  </div>
                  {run.progress != null && <div>{t('progress')}{run.progress}%</div>}
                  {(run.steps || []).length > 0 && (
                    <ol className="space-y-1 text-xs">
                      {(run.steps || []).map((st) => (
                        <li key={String(st.step_id)} className="rounded border border-border p-2">
                          <span className="font-mono">{String(st.step_id)}</span>{' '}
                          <span className="rounded bg-muted px-1">{String(st.tool)}</span>{' '}
                          <span
                            className={
                              st.status === 'failed'
                                ? 'text-red-400'
                                : st.status === 'ok'
                                  ? 'text-emerald-500'
                                  : 'text-blue-400' /* repeated=幂等复用等中间语义 */}
                          >
                            {String(st.status)}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                  {run.result_ref && (
                    <div className="font-mono text-xs">{t('resultRef')}{run.result_ref}</div>
                  )}
                  {(run.evidence_not_covered || []).length > 0 && (
                    <div className="text-xs text-amber-400">
                      {t('uncoveredResult')}{(run.evidence_not_covered || []).join(', ')}
                    </div>
                  )}
                  {run.error && <div className="text-xs text-red-400">{run.error}</div>}
                  <div className="flex gap-2">
                    {run.cancellable && (
                      <button className="rounded-md bg-muted px-3 py-1.5 text-xs" onClick={onCancel}>
                        {t('cancelRun')}
                      </button>
                    )}
                    <button
                      className="rounded-md bg-muted px-3 py-1.5 text-xs"
                      onClick={() => {
                        setSelected(selected);
                        setPrepared(null);
                        setRun(null);
                        setPhase('configure');
                      }}
                    >
                      {t('runAgain')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* 运行记录（刷新回看） */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium">{t('history')}</h4>
          {sessionId && (
            <button
              className="rounded bg-muted px-2 py-1 text-xs"
              onClick={() => refreshRuns(sessionId)}
            >
              {t('refresh')}
            </button>
          )}
        </div>
        <ul className="space-y-1 text-xs">
          {runs.map((r) => (
            <li
              key={r.job_id}
              className="flex items-center justify-between rounded border border-border px-2 py-1.5"
            >
              <span className="font-mono">{r.job_id}</span>
              <span className="font-mono text-muted-foreground">
                {r.skill_id}@{r.skill_version}
              </span>
              <span
                className={
                  r.status === 'completed'
                    ? 'text-emerald-500'
                    : r.status === 'failed'
                      ? 'text-red-400'
                      : 'text-blue-400'
                }
              >
                {r.status}
              </span>
              <span className="font-mono text-muted-foreground">
                {r.result_ref ? r.result_ref.slice(0, 28) : ''}
              </span>
            </li>
          ))}
          {runs.length === 0 && <li className="text-muted-foreground">{t('noRuns')}</li>}
        </ul>
      </div>
    </section>
  );
}
