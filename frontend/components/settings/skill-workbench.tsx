'use client';
import { devOnly } from '@/lib/utils/logger';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { STitle } from '@/components/shared/section-title';
import { getSessionIdentity } from '@/lib/store/session-identity';
import {
  SkillJobApiError,
  cancelSkillJobRun,
  createPreset,
  getCatalog,
  getSkillJobRun,
  listPresets,
  listSkillJobRuns,
  prepareSkillJob,
  presetPrepare,
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

function runnabilityBadge(entry: SkillCatalogEntry): { text: string; cls: string } {
  switch (entry.runnability) {
    case 'runnable':
      return { text: '可执行', cls: 'bg-emerald-500/15 text-emerald-500' };
    case 'deprecated':
      return { text: '已弃用', cls: 'bg-amber-500/15 text-amber-500' };
    case 'unavailable':
      return { text: '能力不可用', cls: 'bg-red-500/15 text-red-400' };
    default:
      return { text: '仅指导', cls: 'bg-slate-500/15 text-slate-400' };
  }
}

export function SkillWorkbench() {
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
  const [sessionId, setSessionId] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getCatalog()
      .then((c) => {
        setCatalog(c.skills || []);
        setGuides(c.md_guides || []);
        setCandidates(c.extension_candidates || []);
      })
      .catch((err: unknown) => devOnly.warn('SkillWorkbench: catalog failed', err));
    listPresets()
      .then((p) => setPresets(p.presets || []))
      .catch(() => undefined);
    const { sessionId: sid } = getSessionIdentity();
    if (sid) setSessionId(sid);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const refreshRuns = useCallback((sid: string) => {
    listSkillJobRuns(sid)
      .then((r) => setRuns(r.runs || []))
      .catch(() => undefined);
  }, []);

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
      pollRef.current = setInterval(() => {
        getSkillJobRun(jobId, sessionId)
          .then((r) => {
            setRun(r);
            if (!r.active) {
              stopPolling();
              if (sessionId) refreshRuns(sessionId);
            }
          })
          .catch(() => undefined);
      }, 2500);
    },
    [refreshRuns, sessionId, stopPolling],
  );

  const onSelectSkill = (entry: SkillCatalogEntry) => {
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
    if (!selected || !sessionId) return;
    setBusy(true);
    setError(null);
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
      });
      setPrepared(res);
      setPhase('preview');
    } catch (err) {
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
      else setError({ code: 'PREPARE_FAILED', message: String(err).slice(0, 160) });
    } finally {
      setBusy(false);
    }
  };

  const onStart = async () => {
    if (!prepared || !sessionId) return;
    setBusy(true);
    setError(null);
    try {
      const r = await startSkillJob({ prepared_id: prepared.prepared_id, session_id: sessionId });
      setRun(r);
      setPhase('running');
      if (r.active) startPolling(r.job_id);
      else refreshRuns(sessionId);
    } catch (err) {
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
      else setError({ code: 'START_FAILED', message: String(err).slice(0, 160) });
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!run || !sessionId) return;
    try {
      await cancelSkillJobRun(run.job_id, sessionId);
    } catch {
      /* 取消请求失败：下一轮轮询如实反映 */
    }
  };

  const onSavePreset = async () => {
    if (!selected || !presetName.trim()) return;
    setBusy(true);
    try {
      await createPreset({
        name: presetName.trim(),
        skill_id: selected.id,
        skill_version: selected.version || '',
        params: params,
        bindings_template: Object.fromEntries(
          bindings.map((b) => [b.role, { last_ref: b.ref, mode: b.mode }]),
        ),
      });
      setPresetName('');
      listPresets().then((p) => setPresets(p.presets || [])).catch(() => undefined);
    } catch (err) {
      if (err instanceof SkillJobApiError) setError({ code: err.code, message: err.message });
    } finally {
      setBusy(false);
    }
  };

  const onReusePreset = async (preset: SkillJobPreset) => {
    if (!sessionId) return;
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
      setBusy(false);
    }
  };

  const activeList = tab === 'skills' ? catalog : tab === 'guides' ? guides : candidates;
  const paramSpecs = useMemo(() => selected?.parameters || [], [selected]);

  return (
    <section className="space-y-4" data-testid="skill-workbench">
      <STitle title="技能作业工作台" sub="选择版本 · 绑定数据 · 审阅计划 · 启动执行 · 回看结果 · 复用配置" />

      {!sessionId && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-300">
          尚无活跃会话：先在对话中开启一个会话（数据 ref 与运行记录按会话归属），
          再回到本工作台绑定数据。
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
              ['skills', `可执行技能 ${catalog.length}`],
              ['guides', `方法指南 ${guides.length}`],
              ['candidates', `扩展 ${candidates.length}`],
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
              const badge = entry.runnability ? runnabilityBadge(entry) : null;
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
              <p className="p-2 text-xs text-muted-foreground">（空）</p>
            )}
          </div>
        </div>

        {/* ── 主面板 ── */}
        <div className="min-h-[420px] space-y-4 rounded-lg border border-border p-4">
          {!selected && <p className="text-sm text-muted-foreground">从左侧目录选择一个技能开始。</p>}

          {selected && (
            <>
              <header className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-base font-semibold">{selected.name}</h3>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                    {selected.id} v{selected.version}
                  </span>
                  {(() => {
                    const badge = runnabilityBadge(selected);
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
                    <legend className="text-sm font-medium">数据绑定（角色 → 会话 ref 或内联 GeoJSON）</legend>
                    {bindings.map((b, i) => (
                      <div key={b.role} className="space-y-1 rounded-md border border-border p-2">
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono">{b.role}</span>
                          <span className="text-muted-foreground">
                            {(selected.role_bindings || []).find((rb) => rb.role === b.role)
                              ?.geometry_kinds.join('/') || '任意几何'}
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
                            会话 ref
                          </button>
                          <button
                            className={`rounded px-2 py-0.5 ${b.mode === 'inline' ? 'bg-primary/20' : 'bg-muted'}`}
                            onClick={() =>
                              setBindings((arr) =>
                                arr.map((x, j) => (j === i ? { ...x, mode: 'inline' } : x)),
                              )
                            }
                          >
                            内联 GeoJSON
                          </button>
                        </div>
                        {b.mode === 'ref' ? (
                          <input
                            className="w-full rounded border border-border bg-background px-2 py-1 font-mono text-xs"
                            placeholder="ref:geojson-…（本会话的数据引用）"
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
                      <p className="text-xs text-muted-foreground">该技能无需数据绑定。</p>
                    )}
                  </fieldset>

                  {/* 参数表单 */}
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">参数</legend>
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
                            <option value="">（未提供）</option>
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
                            <option value="">（未提供）</option>
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
                      <p className="text-xs text-muted-foreground">该技能无声明参数。</p>
                    )}
                  </fieldset>

                  <button
                    className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                    disabled={busy || !sessionId || selected.runnability !== 'runnable'}
                    onClick={onPrepare}
                    data-testid="prepare-btn"
                  >
                    {busy ? '准备中…' : '预览执行计划（零副作用）'}
                  </button>
                  {selected.runnability !== 'runnable' && (
                    <p className="text-xs text-muted-foreground">
                      该资产不是可执行技能（{selected.runnability}）—— 只能作为方法指导。
                    </p>
                  )}

                  {/* 预设 */}
                  <details className="rounded-md border border-border p-2">
                    <summary className="cursor-pointer text-xs font-medium">
                      保存/复用配置预设（{presets.length}）
                    </summary>
                    <div className="mt-2 flex gap-1">
                      <input
                        className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs"
                        placeholder="预设名（如：学校点分布-标准配置）"
                        value={presetName}
                        onChange={(e) => setPresetName(e.target.value)}
                      />
                      <button
                        className="rounded bg-muted px-2 py-1 text-xs disabled:opacity-50"
                        disabled={busy || !presetName.trim()}
                        onClick={onSavePreset}
                      >
                        保存
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
                            复用（重新绑定）
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
                    执行计划预览（技能 digest {prepared.preview.skill.digest}）
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
                          读取角色：{st.reads_roles.join(', ') || '—'} · 证据：
                          {st.produces_evidence.join(', ') || '—'}
                        </div>
                      </li>
                    ))}
                  </ol>
                  <div className="text-xs">
                    <div>
                      证据覆盖：{prepared.preview.evidence_covered.join(', ') || '—'}
                    </div>
                    {prepared.preview.evidence_not_covered.length > 0 && (
                      <div className="text-amber-400">
                        未覆盖义务（运行记录将如实保留）：{prepared.preview.evidence_not_covered.join(', ')}
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
                      {busy ? '启动中…' : '启动运行'}
                    </button>
                    <button
                      className="rounded-md bg-muted px-4 py-2 text-sm"
                      onClick={() => setPhase('configure')}
                    >
                      返回修改
                    </button>
                  </div>
                </div>
              )}

              {(phase === 'running' || run) && run && (
                <div className="space-y-2 text-sm" data-testid="run-panel">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">运行 {run.job_id}</span>
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
                        幂等复用（未重复执行）
                      </span>
                    )}
                    {run.already_running && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                        已在运行（双击/重试不重复执行）
                      </span>
                    )}
                  </div>
                  {run.progress != null && <div>进度：{run.progress}%</div>}
                  {(run.steps || []).length > 0 && (
                    <ol className="space-y-1 text-xs">
                      {(run.steps || []).map((st) => (
                        <li key={String(st.step_id)} className="rounded border border-border p-2">
                          <span className="font-mono">{String(st.step_id)}</span>{' '}
                          <span className="rounded bg-muted px-1">{String(st.tool)}</span>{' '}
                          <span
                            className={
                              st.status === 'ok' ? 'text-emerald-500' : 'text-red-400'
                            }
                          >
                            {String(st.status)}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                  {run.result_ref && (
                    <div className="font-mono text-xs">结果 ref：{run.result_ref}</div>
                  )}
                  {(run.evidence_not_covered || []).length > 0 && (
                    <div className="text-xs text-amber-400">
                      未覆盖义务：{(run.evidence_not_covered || []).join(', ')}
                    </div>
                  )}
                  {run.error && <div className="text-xs text-red-400">{run.error}</div>}
                  <div className="flex gap-2">
                    {run.cancellable && (
                      <button className="rounded-md bg-muted px-3 py-1.5 text-xs" onClick={onCancel}>
                        取消运行
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
                      再跑一次（重新准备）
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
          <h4 className="text-sm font-medium">本会话运行记录（刷新后仍可回看）</h4>
          {sessionId && (
            <button
              className="rounded bg-muted px-2 py-1 text-xs"
              onClick={() => refreshRuns(sessionId)}
            >
              刷新
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
          {runs.length === 0 && <li className="text-muted-foreground">（暂无运行）</li>}
        </ul>
      </div>
    </section>
  );
}
