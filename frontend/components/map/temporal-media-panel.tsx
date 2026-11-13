'use client';

/**
 * G35 时序地图动画媒体面板（WP09）。
 *
 * 薄入口挂在时间轴上；主体是独立面板：真实观测清单/缺期/时间解释/固定
 * 图例与范围/格式能力/资源预估 → 预览关键计划 → 提交后台任务 → 可离开页面
 * 后从服务器恢复 → 下载动画与 PNG 帧证据包。
 *
 * 不改 live 游标、不解除 user_pinned；预览只读取服务端冻结计划，不把网页
 * 里能播放当成已有媒体交付，也不伪造编码进度。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, Film, Loader2, RefreshCw, X } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import {
  cancelMediaJob, getMediaCapabilities, getMediaJobResult, getMediaJobStatus,
  listMediaJobs, previewMediaPlan, submitMediaJob,
  type MediaCapabilities, type MediaJobListItem, type MediaJobResult,
  type MediaJobStatus, type MediaPlanPreview, type MediaPlanRequest,
} from '@/lib/api/temporal-media';

interface MediaSettings {
  format: 'apng' | 'gif';
  timing_policy: 'equal' | 'proportional';
  dwell_ms: number;
  fps: number;
  order: 'chronological' | 'reverse';
  missing_display: 'skip' | 'empty' | 'hold';
  time_display: 'label' | 'label_window' | 'none';
  width: number;
  height: number;
  loop: boolean;
  partial_policy: 'blocking' | 'allow_partial';
}

const DEFAULTS: MediaSettings = {
  format: 'apng',
  timing_policy: 'equal',
  dwell_ms: 1000,
  fps: 0,
  order: 'chronological',
  missing_display: 'skip',
  time_display: 'label',
  width: 1280,
  height: 720,
  loop: true,
  partial_policy: 'blocking',
};

export interface TemporalMediaPanelProps {
  onClose?: () => void;
}

export function TemporalMediaPanel({ onClose }: TemporalMediaPanelProps) {
  const t = useT('map');
  const [settings, setSettings] = useState<MediaSettings>({ ...DEFAULTS });
  const [capabilities, setCapabilities] = useState<MediaCapabilities | null>(null);
  const [bindingMessage, setBindingMessage] = useState<string>('');
  const [preview, setPreview] = useState<MediaPlanPreview | null>(null);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<MediaJobStatus | null>(null);
  const [result, setResult] = useState<MediaJobResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>('');

  const identity = getSessionIdentity();
  const sessionId = identity.sessionId ?? '';
  const ownerToken = identity.ownerToken ?? null;

  const requestBody = useCallback((): MediaPlanRequest => ({
    session_id: sessionId,
    ...settings,
    // GIF 时间分辨率 10ms 且 writer 会合并相同帧：后端只接受 fps=0。
    fps: settings.format === 'gif' ? 0 : settings.fps,
    frame_ids: selected,
  }), [sessionId, settings, selected]);
  const previewArgsRef = useRef<string>('');

  const loadPreview = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    setError('');
    const body = requestBody();
    try {
      const [caps, plan] = await Promise.all([
        getMediaCapabilities(sessionId, ownerToken),
        previewMediaPlan(body, ownerToken),
      ]);
      previewArgsRef.current = JSON.stringify(body);
      setCapabilities(caps.capabilities);
      setBindingMessage(caps.binding?.available
        ? ''
        : String(caps.binding?.message ?? caps.binding?.code ?? ''));
      setPreview(plan);
      // 不重置 selected：勾选子集后点「预览计划」必须保留子集，
      // 否则提交会以 frame_ids=null 二次重预览并交付全部观测。
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [sessionId, ownerToken, requestBody]);

  useEffect(() => {
    void loadPreview();
    // 重开页面：从服务器恢复本会话的媒体任务与成果
    if (!sessionId) return;
    void (async () => {
      try {
        const listed = await listMediaJobs(sessionId, ownerToken);
        const candidates = listed.jobs.filter(
          (job) => job.status === 'completed' || job.status === 'running'
            || job.status === 'pending');
        // 按服务端 created_at 取时间上最近的任务（job_id 是内容哈希，无时间序）。
        const latest = candidates.reduce<MediaJobListItem | undefined>(
          (best, job) => (!best || (job.created_at ?? 0) >= (best.created_at ?? 0)
            ? job : best), undefined);
        if (!latest) return;
        setJobId(latest.job_id);
        const current = await getMediaJobStatus(sessionId, latest.job_id, ownerToken);
        setStatus(current);
        if (current.completed) {
          setResult(await getMediaJobResult(sessionId, latest.job_id, ownerToken));
        }
      } catch {
        // 恢复失败不阻断新计划；用户仍可重新提交
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!jobId || !sessionId || !status) return;
    if (['completed', 'cancelled', 'failed'].includes(status.status)) return;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const next = await getMediaJobStatus(sessionId, jobId, ownerToken);
          setStatus(next);
          if (next.completed) {
            setResult(await getMediaJobResult(sessionId, jobId, ownerToken));
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, sessionId, ownerToken, status]);

  const refreshJob = useCallback(async () => {
    if (!jobId || !sessionId) return;
    try {
      const next = await getMediaJobStatus(sessionId, jobId, ownerToken);
      setStatus(next);
      if (next.completed) {
        setResult(await getMediaJobResult(sessionId, jobId, ownerToken));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [jobId, sessionId, ownerToken]);

  const onSubmit = useCallback(async () => {
    if (!preview) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const body = requestBody();
      // 参数（格式/停留/尺寸/子集…）在上次预览后被改过：先重新预览取得
      // 同一份冻结计划的 plan_ref，否则提交会因版本漂移被 typed 拒绝。
      if (previewArgsRef.current !== JSON.stringify(body)) {
        const fresh = await previewMediaPlan(body, ownerToken);
        previewArgsRef.current = JSON.stringify(body);
        setPreview(fresh);
        setJobId(null);
        setStatus(null);
        const receipt = await submitMediaJob(
          { ...body, plan_ref: fresh.plan_ref }, ownerToken);
        setJobId(receipt.job_id);
        const current = await getMediaJobStatus(sessionId, receipt.job_id, ownerToken);
        setStatus(current);
        return;
      }
      const receipt = await submitMediaJob(
        { ...body, plan_ref: preview.plan_ref }, ownerToken);
      setJobId(receipt.job_id);
      const current = await getMediaJobStatus(sessionId, receipt.job_id, ownerToken);
      setStatus(current);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [preview, requestBody, ownerToken, sessionId]);

  const onCancel = useCallback(async () => {
    if (!jobId) return;
    try {
      const next = await cancelMediaJob(sessionId, jobId, ownerToken);
      setStatus({ ...next, success: true, completed: false });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [jobId, sessionId, ownerToken]);

  const onDownload = useCallback(async (kind: string) => {
    const artifacts = result?.artifacts ?? status?.artifacts ?? undefined;
    const item = artifacts?.[kind];
    if (!item) return;
    await downloadWithAuth(item.url, { filename: item.filename, ownerToken });
  }, [result, status, ownerToken]);

  const availableFormats = useMemo(
    () => (capabilities?.formats ?? []).filter((f) => f.available),
    [capabilities]);

  const progressLabel = status?.progress == null
    ? t('temporalMedia.progressUnknown', { defaultValue: '总帧数确认前不显示百分比' })
    : `${status.progress}% · ${status.phase}`;

  return (
    <div
      className="pointer-events-auto fixed bottom-20 left-1/2 z-40 w-[min(46rem,92vw)]
                 max-h-[72vh] -translate-x-1/2 overflow-y-auto rounded-xl
                 border border-border bg-background/98 p-4 text-xs shadow-2xl"
      role="dialog"
      aria-label={t('temporalMedia.title', { defaultValue: '时序动画媒体交付' })}
      data-testid="temporal-media-panel"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold">
          <Film size={16} aria-hidden />
          {t('temporalMedia.title', { defaultValue: '时序动画媒体交付' })}
        </div>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => { void loadPreview(); void refreshJob(); }}
            className="rounded p-1 hover:bg-accent"
            aria-label={t('temporalMedia.refresh', { defaultValue: '重新预览计划' })}
            data-testid="temporal-media-refresh">
            <RefreshCw size={14} />
          </button>
          {onClose ? (
            <button type="button" onClick={onClose} className="rounded p-1 hover:bg-accent"
              aria-label={t('temporalMedia.close', { defaultValue: '关闭' })}
              data-testid="temporal-media-close">
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>

      {bindingMessage ? (
        <p className="mb-2 rounded bg-amber-500/15 p-2 text-amber-700 dark:text-amber-300"
          data-testid="temporal-media-binding-error">{bindingMessage}</p>
      ) : null}
      {error ? (
        <p className="mb-2 rounded bg-destructive/10 p-2 text-destructive"
          data-testid="temporal-media-error">{error}</p>
      ) : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="flex flex-col gap-1">
          {t('temporalMedia.format', { defaultValue: '格式' })}
          <select value={settings.format}
            onChange={(e) => setSettings({ ...settings,
              format: e.target.value as 'apng' | 'gif' })}
            className="rounded border border-border bg-background px-1 py-1"
            data-testid="temporal-media-format">
            {availableFormats.map((f) => (
              <option key={f.name} value={f.name}>{f.name.toUpperCase()}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.timing', { defaultValue: '展示时长策略' })}
          <select value={settings.timing_policy}
            onChange={(e) => setSettings({ ...settings,
              timing_policy: e.target.value as 'equal' | 'proportional' })}
            className="rounded border border-border bg-background px-1 py-1"
            data-testid="temporal-media-timing">
            <option value="equal">{t('temporalMedia.equal', { defaultValue: '等时长逐观测' })}</option>
            <option value="proportional">{t('temporalMedia.proportional', { defaultValue: '按真实间隔缩放' })}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.dwell', { defaultValue: '每观测停留 (ms)' })}
          <input type="number" min={100} max={10000} step={100}
            value={settings.dwell_ms}
            onChange={(e) => setSettings({ ...settings, dwell_ms: Number(e.target.value) })}
            className="rounded border border-border bg-background px-1 py-1"
            data-testid="temporal-media-dwell" />
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.fps', { defaultValue: '编码 fps (0=停留复用)' })}
          <input type="number" min={0} max={24} step={1}
            value={settings.format === 'gif' ? 0 : settings.fps}
            disabled={settings.format === 'gif'}
            title={settings.format === 'gif'
              ? 'GIF 时间分辨率为 10ms 且会合并相同帧，仅支持 fps=0'
              : undefined}
            onChange={(e) => setSettings({ ...settings, fps: Number(e.target.value) })}
            className="rounded border border-border bg-background px-1 py-1 disabled:opacity-50"
            data-testid="temporal-media-fps" />
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.order', { defaultValue: '播放顺序' })}
          <select value={settings.order}
            onChange={(e) => setSettings({ ...settings,
              order: e.target.value as 'chronological' | 'reverse' })}
            className="rounded border border-border bg-background px-1 py-1">
            <option value="chronological">{t('temporalMedia.ascending', { defaultValue: '按时间正序' })}</option>
            <option value="reverse">{t('temporalMedia.descending', { defaultValue: '倒序（显式标记）' })}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.missing', { defaultValue: '缺期显示' })}
          <select value={settings.missing_display}
            onChange={(e) => setSettings({ ...settings,
              missing_display: e.target.value as 'skip' | 'empty' | 'hold' })}
            className="rounded border border-border bg-background px-1 py-1"
            data-testid="temporal-media-missing">
            <option value="skip">{t('temporalMedia.missingSkip', { defaultValue: '跳过并披露' })}</option>
            <option value="empty">{t('temporalMedia.missingEmpty', { defaultValue: '明确空态' })}</option>
            <option value="hold">{t('temporalMedia.missingHold', { defaultValue: '沿用画面并注明' })}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.width', { defaultValue: '宽 (px)' })}
          <input type="number" min={320} max={1920} step={16} value={settings.width}
            onChange={(e) => setSettings({ ...settings, width: Number(e.target.value) })}
            className="rounded border border-border bg-background px-1 py-1" />
        </label>
        <label className="flex flex-col gap-1">
          {t('temporalMedia.height', { defaultValue: '高 (px)' })}
          <input type="number" min={240} max={1080} step={16} value={settings.height}
            onChange={(e) => setSettings({ ...settings, height: Number(e.target.value) })}
            className="rounded border border-border bg-background px-1 py-1" />
        </label>
      </div>

      {preview ? (
        <section className="mt-3 rounded border border-border p-2"
          data-testid="temporal-media-preview">
          <div className="mb-1 font-medium">
            {t('temporalMedia.plan', { defaultValue: '冻结计划预览' })} · {preview.plan_hash.slice(0, 12)}
          </div>
          <p className="text-muted-foreground">
            {t('temporalMedia.observations', { defaultValue: '真实观测' })}: {preview.plan.display.observation_count} ·{' '}
            {t('temporalMedia.gaps', { defaultValue: '缺期' })}: {preview.plan.display.missing_period_count} ·{' '}
            {t('temporalMedia.omitted', { defaultValue: '省略观测' })}: {preview.plan.selection.omitted_observations} ·{' '}
            {t('temporalMedia.span', { defaultValue: '实际跨度' })}: {preview.plan.selection.actual_span_ms} ms
          </p>
          <div className="mt-1 flex flex-wrap gap-1" data-testid="temporal-media-observations">
            {(preview.all_observations ?? preview.plan.observations).map((obs) => (
              <label key={obs.id} className="flex items-center gap-1 rounded border border-border px-1 py-0.5">
                <input type="checkbox" checked={(selected ?? (preview.all_observations ?? preview.plan.observations).map((o) => o.id)).includes(obs.id)}
                  onChange={() => {
                    const all = selected ?? (preview.all_observations ?? preview.plan.observations).map((o) => o.id);
                    setSelected(all.includes(obs.id)
                      ? all.filter((id) => id !== obs.id) : [...all, obs.id]);
                  }} />
                {obs.label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-muted-foreground">
            {t('temporalMedia.totalDuration', { defaultValue: '总展示时长' })}: {preview.plan.display.timing.total_display_ms} ms ·{' '}
            {t('temporalMedia.encodedFrames', { defaultValue: '预计编码帧' })}: {preview.plan.resource_estimate.encoded_frames} ·{' '}
            {t('temporalMedia.uniqueFrames', { defaultValue: '唯一数字帧' })}: {preview.plan.resource_estimate.unique_digital_frames}
          </p>
          <p className="mt-1 text-muted-foreground">
            {t('temporalMedia.timingRule', { defaultValue: '时长规则' })}: {preview.plan.display.timing.rule}
          </p>
          {preview.plan.partial.is_partial ? (
            <p className="mt-1 text-amber-600">
              {t('temporalMedia.partial', { defaultValue: '显式 partial：以下图层将不进入画面' })}:{' '}
              {preview.plan.partial.omitted_layers.map((o) => `${o.layer_id}(${o.reason})`).join(', ')}
            </p>
          ) : null}
          <ul className="mt-1 max-h-20 overflow-y-auto text-muted-foreground">
            {preview.plan.disclosures.map((line) => <li key={line}>· {line}</li>)}
          </ul>
        </section>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadPreview()} disabled={busy}
          className="rounded border border-border px-2 py-1 hover:bg-accent">
          {t('temporalMedia.preview', { defaultValue: '预览计划' })}
        </button>
        <button type="button" onClick={() => void onSubmit()} disabled={busy || !preview}
          className="flex items-center gap-1 rounded bg-primary px-2 py-1 text-primary-foreground disabled:opacity-50"
          data-testid="temporal-media-submit">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
          {t('temporalMedia.submit', { defaultValue: '提交动画任务' })}
        </button>
        {jobId && status && !['completed', 'cancelled', 'failed'].includes(status.status) ? (
          <button type="button" onClick={() => void onCancel()}
            className="rounded border border-destructive px-2 py-1 text-destructive"
            data-testid="temporal-media-cancel">
            {t('temporalMedia.cancel', { defaultValue: '取消任务' })}
          </button>
        ) : null}
      </div>

      {status ? (
        <section className="mt-2 rounded border border-border p-2"
          data-testid="temporal-media-status">
          <div className="flex items-center justify-between">
            <span>{status.status} · {status.message}</span>
            <span>{progressLabel}</span>
          </div>
          <p className="text-muted-foreground">
            {t('temporalMedia.rendered', { defaultValue: '唯一渲染帧' })}: {status.rendered_frames ?? 0} ·{' '}
            {t('temporalMedia.cacheHits', { defaultValue: '缓存命中' })}: {status.cache_hits ?? 0}
          </p>
          {status.error ? <p className="text-destructive">{String(JSON.stringify(status.error))}</p> : null}
        </section>
      ) : null}

      {result ? (
        <section className="mt-2 rounded border border-border p-2"
          data-testid="temporal-media-result">
          <div className="mb-1 font-medium">
            {t('temporalMedia.verified', { defaultValue: '独立解码核验通过' })} ·{' '}
            {result.verification.decoded_frames} {t('temporalMedia.frames', { defaultValue: '帧' })}
          </div>
          <div className="flex flex-wrap gap-2">
            {(['animation', 'evidence', 'manifest'] as const).map((kind) => (
              <button key={kind} type="button" onClick={() => void onDownload(kind)}
                className="flex items-center gap-1 rounded border border-border px-2 py-1 hover:bg-accent"
                data-testid={`temporal-media-download-${kind}`}>
                <Download size={14} /> {kind} ({result.artifacts[kind]?.bytes ?? 0} B)
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export default TemporalMediaPanel;
