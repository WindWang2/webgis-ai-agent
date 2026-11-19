'use client';

import { useT } from '@/lib/i18n/useT';
/**
 * G27 P4 —— 「按尺度表达」面板（样式/标注设置附近的小型可解释入口）。
 *
 * - 当前档位 / 为何隐藏或稀疏 / 用户覆盖 / 恢复自动；
 * - observed camera 变化只走只读事件（runtime zoomend 广播），分档去抖，
 *   **不产生每帧持久化 mutation**（A09/A10）；
 * - 显式策略修改才经既有 REST → service → CAS/undo 落库（A19/A20）；
 * - 人工隐藏层不会因放大自动出现；锁定字号/pin 由既有优先级保留；
 * - 无消费者 / 无效策略 / 后端未确认时绝不显示「已生效」（A28）。
 */
import {
  useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore,
} from 'react';
import { apiFetch } from '@/lib/api/transport';
import {
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { refreshMapSpecAfterExternalMutation } from '@/lib/mapspec/user-mutation';
import {
  G27_SCALE_POLICY_VERSION,
  resolveScalePortrayalView,
} from '@/lib/carto-ir/scale-portrayal';

const ZOOM_EVENT = 'webgis:zoom';
const BAND_BOUNDARIES = [6, 10, 14];
const DEBOUNCE_MS = 200;

function bandOf(zoom: number): number {
  let band = 0;
  for (const edge of BAND_BOUNDARIES) if (zoom >= edge) band += 1;
  return band;
}

interface PolicyPreset {
  id: string;
  label: string;
  policy: Record<string, unknown> | null;
}

const PRESETS: PolicyPreset[] = [
  { id: 'auto', label: 'presetAuto',
    policy: { version: G27_SCALE_POLICY_VERSION, mode: 'auto_lod' } },
  { id: 'fixed', label: 'presetFixed',
    policy: { version: G27_SCALE_POLICY_VERSION, mode: 'fixed_density',
      pinned_label_top_ratio: 0.6 } },
  { id: 'restore', label: 'presetRestore', policy: null },
];

export function ScalePortrayalPanel({ layerId }: { layerId: string }) {
  const t = useT('map.scalePortrayal');
  const [zoom, setZoom] = useState<number>(8);
  const [explanation, setExplanation] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const lastBandRef = useRef<number>(-1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const epochRef = useRef(0);

  // observed camera：只在跨档时更新状态（去抖 + 幂等），不写 desired spec。
  useEffect(() => {
    const onZoom = (event: Event) => {
      const detail = (event as CustomEvent<{ zoom?: number }>).detail;
      const z = detail?.zoom;
      if (typeof z !== 'number' || !Number.isFinite(z)) return;
      const band = bandOf(z);
      if (band === lastBandRef.current) return;
      lastBandRef.current = band;
      setZoom(z);
    };
    window.addEventListener(ZOOM_EVENT, onZoom);
    return () => {
      window.removeEventListener(ZOOM_EVENT, onZoom);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const sessionId = useMemo(() => getMapSpecSessionCursor().sessionId, []);

  const loadExplanation = useCallback(async (z: number) => {
    if (!sessionId || !layerId) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    const epoch = ++epochRef.current;
    timerRef.current = setTimeout(async () => {
      try {
        const q = new URLSearchParams({
          layer: layerId,
          observed_zoom: String(z),
        });
        const data = await apiFetch<Record<string, unknown>>(
          `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/scale-portrayal?${q}`,
          { label: 'Scale portrayal inspect' },
        );
        if (epoch !== epochRef.current) return;   // 快速缩放：丢弃旧计算
        setExplanation(
          data && typeof data.explanation === 'object'
            ? data.explanation as Record<string, unknown> : null);
      } catch {
        // 读取失败保留本地投影（不伪造服务端回执）
        if (epoch === epochRef.current) setExplanation(null);
      }
    }, DEBOUNCE_MS);
  }, [sessionId, layerId]);

  useEffect(() => { void loadExplanation(zoom); }, [zoom, loadExplanation]);

  const commit = useCallback(async (preset: PolicyPreset) => {
    if (!sessionId || !layerId || busy) return;
    setBusy(true);
    setMessage('');
    try {
      const { revision } = getMapSpecSessionCursor();
      const data = await apiFetch<Record<string, unknown>>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/scale-portrayal`,
        {
          method: 'POST',
          body: {
            layer: layerId,
            policy: preset.policy,
            mode: 'commit',
            expected_revision: revision,
            operation_id: `g27:${layerId}:${Date.now()}`,
          },
          label: 'Scale portrayal apply',
        },
      );
      const status = typeof data?.status === 'string' ? data.status : '';
      setMessage(status === 'committed'
        ? t('committed', { p0: String(data?.mapspec_revision ?? '') })
        : status === 'superseded' ? t('superseded') : t('notApplied', { p0: status || t('unknownReceipt') }));
      await refreshMapSpecAfterExternalMutation(
        sessionId,
        typeof data?.mapspec_revision === 'number' ? data.mapspec_revision : undefined,
      );
      await loadExplanation(zoom);
    } catch (err) {
      setMessage(t('failed', { p0: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  }, [busy, layerId, sessionId, zoom, loadExplanation, t]);

  // 真实 committed spec：view 必须基于实际 MapSpec（含 label.zoomBands），
  // 否则「已生效」判定会失去消费者依据；后端回执在场时以回执为准。
  const committedSpec = useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec);
  const layer = useMemo(() => {
    const layers = (committedSpec as {
      layers?: Array<Record<string, unknown>>;
    } | null)?.layers;
    if (!Array.isArray(layers)) return null;
    return layers.find((row) => row && row.id === layerId) ?? null;
  }, [committedSpec, layerId]);
  const view = resolveScalePortrayalView(
    layer ?? { id: layerId }, zoom, { explanation });

  const diagnostics = view.diagnostics.slice(0, 4);
  const disclosures = view.disclosures.slice(0, 2);

  return (
    <section
      aria-label={t('title')}
      data-testid="scale-portrayal-panel"
      className="rounded border border-edge-subtle bg-surface-sunken p-3 space-y-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-ink-muted uppercase tracking-wider">
          {t('title')}
        </span>
        <span
          className={`text-micro px-1.5 py-0.5 rounded-full border ${
            view.applied
              ? 'bg-status-accent-soft text-status-accent border-status-accent-border'
              : 'bg-surface-panel text-ink-muted border-edge-subtle'
          }`}
        >
          {view.applied ? t('applied') : t('unapplied')}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-caption text-ink">
        <dt className="text-ink-muted">{t('currentBand')}</dt>
        <dd>{view.bandId} · z{view.effectiveZoom.toFixed(1)}</dd>
        <dt className="text-ink-muted">{t('scaleBand')}</dt>
        <dd>{view.tier === 'not_evaluated' ? t('pending') : view.tier}</dd>
        <dt className="text-ink-muted">{t('visibility')}</dt>
        <dd>{view.visibility === 'none' ? t('hidden') : t('visible')}</dd>
        <dt className="text-ink-muted">{t('labelRatios')}</dt>
        <dd>{view.topRatio.toFixed(2)} / {view.sizeRatio.toFixed(2)}</dd>
      </dl>

      {view.hiddenReason && (
        <p className="text-micro text-status-warning">
          {t('hiddenReason')}{view.hiddenReason}
        </p>
      )}
      {!view.applied && (
        <p className="text-micro text-ink-muted">
          {t('notAppliedHelp')}
        </p>
      )}
      {view.userOverrides.length > 0 && (
        <p className="text-micro text-ink-muted">
          {t('userOverrides')}{view.userOverrides.join(t('listSeparator'))}
        </p>
      )}
      {diagnostics.length > 0 && (
        <ul className="text-micro text-status-warning space-y-0.5">
          {diagnostics.map((d, i) => <li key={`${d.code}-${i}`}>{d.code} · {d.field}</li>)}
        </ul>
      )}
      <details className="text-micro text-ink-muted">
        <summary className="cursor-pointer">{t('disclosures')}</summary>
        <ul className="mt-1 space-y-0.5">
          {disclosures.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      </details>

      <div className="flex flex-wrap gap-1.5 pt-1">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            disabled={busy}
            aria-label={t('buttonLabel', { p0: t(preset.label) })}
            onClick={() => void commit(preset)}
            className="text-micro px-2 py-1 rounded border border-edge-subtle bg-surface-panel text-ink hover:border-status-accent-border disabled:opacity-50 cursor-pointer"
          >
            {t(preset.label)}
          </button>
        ))}
      </div>
      {message && <p role="status" className="text-micro text-ink-muted">{message}</p>}
    </section>
  );
}
