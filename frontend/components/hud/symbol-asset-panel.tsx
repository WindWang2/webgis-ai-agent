'use client';

/**
 * G40 制图符号资产面板（WP10）——目录/参数/预览/应用/解绑。
 *
 * 与 Pi 工具同一领域服务（/cartographic-assets 路由）：inspect → 目录选择
 * （builtin 优先，分页）→ typed 参数编辑 → preview（零 mutation，swatch
 * 内联 SVG）→ apply（expected_revision CAS）→ unbind（恢复快照）。
 * 错误落具体参数（typed code → 字段）；generation epoch 防迟到响应。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';

import {
  ANCHORS, PointAnchor, SymbolAssetDocument,
} from '@/lib/cartographic-assets/contracts';
import { swatchSvgString } from '@/lib/cartographic-assets/rasterize';
import {
  AssetSummary, BindingReceipt,
  applyBinding, inspectLayerBinding, listAssets, previewBinding, unbindLayer,
} from '@/lib/cartographic-assets/api';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

interface InspectState {
  revision: number;
  eligibility: {
    layer_type: string;
    supports_point_symbol: boolean;
    supports_area_texture: boolean;
  };
  current_binding?: Record<string, unknown> | null;
}

export default function SymbolAssetPanel({ layerId }: { layerId: string }) {
  const t = useTranslations('symbolAssets');

  const epochRef = useRef(0);

  const [inspect, setInspect] = useState<InspectState | null>(null);
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [selected, setSelected] = useState<AssetSummary | null>(null);
  const [sizePx, setSizePx] = useState(16);
  const [anchor, setAnchor] = useState<PointAnchor>('center');
  const [rotation, setRotation] = useState(0);
  const [spacingPx, setSpacingPx] = useState(12);
  const [angleDeg, setAngleDeg] = useState(45);
  const [strokeW, setStrokeW] = useState(1.5);
  const [fgColor, setFgColor] = useState('#334155');
  const [bgColor, setBgColor] = useState('#fef08a');
  const [bgTransparent, setBgTransparent] = useState(false);
  const [preview, setPreview] = useState<BindingReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const kind: 'point_symbol' | 'area_texture' | null = inspect
    ? (inspect.eligibility.supports_point_symbol && !inspect.eligibility.supports_area_texture
        ? 'point_symbol'
        : inspect.eligibility.supports_area_texture && !inspect.eligibility.supports_point_symbol
          ? 'area_texture' : null)
    : null;

  const reload = useCallback(async () => {
    const { sessionId } = getMapSpecSessionCursor();
    if (!sessionId) return;
    const epoch = ++epochRef.current;
    setBusy(true); setError(null);
    try {
      const insp = await inspectLayerBinding(sessionId, layerId) as unknown as InspectState;
      if (epoch !== epochRef.current) return;
      setInspect(insp);
      const wantKind = insp.eligibility?.supports_point_symbol
        ? 'point_symbol' : 'area_texture';
      const list = await listAssets({ kind: wantKind, limit: 30 });
      if (epoch !== epochRef.current) return;
      setAssets(list);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError({ code: 'INSPECT_FAILED', message: String(err) });
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  }, [layerId]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => () => { epochRef.current += 1; }, []);

  const currentBinding = inspect?.current_binding ?? null;
  const hasBinding = !!currentBinding;

  const plan = useMemo(() => {
    if (!selected) return null;
    const base: Record<string, unknown> = {
      asset_id: selected.asset_id,
      revision: selected.revision ?? selected.current_revision ?? 1,
      content_digest: selected.content_digest,
      kind: selected.kind,
      params: selected.kind === 'point_symbol'
        ? { size_px: sizePx, anchor, rotation_deg: rotation }
        : {
            spacing_px: spacingPx, angle_deg: angleDeg,
            stroke_width_px: strokeW, fg_color: fgColor,
            bg_color: bgTransparent ? 'transparent' : bgColor,
          },
    };
    return base;
  }, [selected, sizePx, anchor, rotation, spacingPx, angleDeg, strokeW,
      fgColor, bgColor, bgTransparent]);

  const swatchPreview = useMemo(() => {
    if (!selected) return null;
    // 本地 swatch（builtin 内联文档；DB 资产显示通用形状 + 参数色）
    const doc = (selected as unknown as { document?: SymbolAssetDocument }).document;
    const params = selected.kind === 'point_symbol'
      ? { size_px: 24, anchor: 'center' as const, rotation_deg: 0, opacity: 1 }
      : {
          spacing_px: spacingPx, angle_deg: angleDeg,
          stroke_width_px: strokeW, fg_color: fgColor,
          bg_color: bgTransparent ? 'transparent' : bgColor, pattern_scale: 1,
        };
    if (doc) {
      try {
        return swatchSvgString(doc, params as unknown as Record<string, unknown>, 40, 32);
      } catch { /* fall through */ }
    }
    return null;
  }, [selected, spacingPx, angleDeg, strokeW, fgColor, bgColor, bgTransparent]);

  const doPreview = async () => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || !plan) return;
    const epoch = ++epochRef.current;
    setBusy(true); setError(null); setNotice(null);
    try {
      const receipt = await previewBinding(sessionId, layerId, plan, ownerToken);
      if (epoch !== epochRef.current) return;
      setPreview(receipt);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError({ code: 'PREVIEW_FAILED', message: String(err) });
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  };

  const doApply = async () => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || !plan) return;
    const epoch = ++epochRef.current;
    const revision = preview?.revision ?? inspect?.revision ?? 0;
    setBusy(true); setError(null); setNotice(null);
    try {
      const receipt = await applyBinding(
        sessionId, layerId, plan, revision,
        `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, ownerToken);
      if (epoch !== epochRef.current) return;
      if (receipt.success) {
        setNotice(t('applied', { revision: receipt.revision ?? revision }));
        setPreview(null);
        await reload();
      } else {
        setError({ code: receipt.code ?? 'APPLY_FAILED',
                   message: receipt.message ?? '' });
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError({ code: 'APPLY_FAILED', message: String(err) });
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  };

  const doUnbind = async () => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return;
    const epoch = ++epochRef.current;
    const revision = inspect?.revision ?? 0;
    setBusy(true); setError(null);
    try {
      const receipt = await unbindLayer(sessionId, layerId, revision, ownerToken);
      if (epoch !== epochRef.current) return;
      if (receipt.success) {
        setNotice(t('unbound'));
        setPreview(null);
        await reload();
      } else {
        setError({ code: receipt.code ?? 'UNBIND_FAILED',
                   message: receipt.message ?? '' });
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError({ code: 'UNBIND_FAILED', message: String(err) });
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  };

  if (!getMapSpecSessionCursor().sessionId) return null;
  if (inspect && !kind) {
    return (
      <p className="text-caption text-ink-muted" data-testid="symbol-asset-ineligible">
        {t('ineligible', { type: inspect.eligibility?.layer_type ?? '?' })}
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="symbol-asset-panel">
      <div className="flex items-center justify-between">
        <span className="text-caption text-ink-muted uppercase tracking-wider">
          {t('title')}
        </span>
        {hasBinding && (
          <button
            type="button"
            onClick={doUnbind}
            disabled={busy}
            className="text-caption text-status-danger hover:underline disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-status-accent rounded"
            data-testid="symbol-asset-unbind"
          >
            {t('unbind')}
          </button>
        )}
      </div>

      {currentBinding && (
        <p className="text-caption text-ink-muted" data-testid="symbol-asset-current">
          {t('current', {
            id: String((currentBinding as any).asset_id ?? ''),
            rev: String((currentBinding as any).revision ?? ''),
          })}
        </p>
      )}

      <div>
        <label htmlFor="symbol-asset-select" className="sr-only">
          {t('selectAsset')}
        </label>
        <select
          id="symbol-asset-select"
          value={selected?.asset_id ?? ''}
          onChange={(e) => {
            const next = assets.find((a) => a.asset_id === e.target.value) ?? null;
            setSelected(next);
            setPreview(null);
            if (next?.default_params) {
              const d = next.default_params as Record<string, number | string>;
              if (typeof d.size_px === 'number') setSizePx(d.size_px);
              if (typeof d.spacing_px === 'number') setSpacingPx(d.spacing_px);
              if (typeof d.angle_deg === 'number') setAngleDeg(d.angle_deg);
              if (typeof d.fg_color === 'string') setFgColor(d.fg_color);
            }
          }}
          className="w-full border border-border-default rounded px-2 py-1 text-sm bg-surface-primary"
          data-testid="symbol-asset-select"
        >
          <option value="">{t('selectAsset')}</option>
          {assets.map((a) => (
            <option key={a.asset_id} value={a.asset_id}>
              {a.name || a.asset_id}{a.builtin ? '' : '（库）'}
            </option>
          ))}
        </select>
      </div>

      {swatchPreview && (
        <div className="border border-border-default rounded p-1 inline-block" data-testid="symbol-asset-swatch">
          <svg width="40" height="32" viewBox="0 0 40 32" role="img" aria-label={t('swatchAria')}>
            <rect width="40" height="32" fill="#ffffff" />
            <g dangerouslySetInnerHTML={{ __html: swatchPreview }} />
          </svg>
        </div>
      )}

      {kind === 'point_symbol' ? (
        <div className="grid grid-cols-2 gap-2 text-caption">
          <label className="flex flex-col gap-1">
            {t('sizePx')}
            <input
              type="number" min={1} max={128} step={0.5} value={sizePx}
              onChange={(e) => setSizePx(Number(e.target.value))}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary"
              data-testid="symbol-asset-size" />
          </label>
          <label className="flex flex-col gap-1">
            {t('rotation')}
            <input
              type="number" min={-360} max={360} step={1} value={rotation}
              onChange={(e) => setRotation(Number(e.target.value))}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary"
              data-testid="symbol-asset-rotation" />
          </label>
          <label className="flex flex-col gap-1 col-span-2">
            {t('anchor')}
            <select
              value={anchor}
              onChange={(e) => setAnchor(e.target.value as PointAnchor)}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary"
              data-testid="symbol-asset-anchor">
              {ANCHORS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2 text-caption">
          <label className="flex flex-col gap-1">
            {t('spacing')}
            <input
              type="number" min={4} max={64} step={0.5} value={spacingPx}
              onChange={(e) => setSpacingPx(Number(e.target.value))}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary"
              data-testid="symbol-asset-spacing" />
          </label>
          <label className="flex flex-col gap-1">
            {t('angle')}
            <input
              type="number" min={-90} max={90} step={1} value={angleDeg}
              onChange={(e) => setAngleDeg(Number(e.target.value))}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary"
              data-testid="symbol-asset-angle" />
          </label>
          <label className="flex flex-col gap-1">
            {t('strokeW')}
            <input
              type="number" min={0.25} max={8} step={0.25} value={strokeW}
              onChange={(e) => setStrokeW(Number(e.target.value))}
              className="border border-border-default rounded px-2 py-0.5 bg-surface-primary" />
          </label>
          <label className="flex flex-col gap-1">
            {t('fgColor')}
            <input
              type="color" value={fgColor}
              onChange={(e) => setFgColor(e.target.value)}
              className="h-7 border border-border-default rounded bg-surface-primary"
              data-testid="symbol-asset-fg" />
          </label>
          <label className="flex flex-col gap-1">
            {t('bgColor')}
            <input
              type="color" value={bgColor} disabled={bgTransparent}
              onChange={(e) => setBgColor(e.target.value)}
              className="h-7 border border-border-default rounded bg-surface-primary disabled:opacity-40" />
          </label>
          <label className="flex items-center gap-1 self-end">
            <input
              type="checkbox" checked={bgTransparent}
              onChange={(e) => setBgTransparent(e.target.checked)}
              data-testid="symbol-asset-bg-transparent" />
            {t('bgTransparent')}
          </label>
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button" onClick={doPreview} disabled={busy || !selected}
          className="px-2 py-1 text-caption border border-border-default rounded hover:bg-surface-secondary disabled:opacity-50"
          data-testid="symbol-asset-preview-btn">
          {t('preview')}
        </button>
        <button
          type="button" onClick={doApply} disabled={busy || !plan}
          className="px-2 py-1 text-caption bg-status-accent text-surface-primary rounded hover:opacity-90 disabled:opacity-50"
          data-testid="symbol-asset-apply-btn">
          {t('apply')}
        </button>
      </div>

      {preview && (
        <div className="text-caption text-ink-muted border border-border-default rounded p-2" data-testid="symbol-asset-preview-receipt">
          <p>{t('previewReady')}</p>
          {(preview.disclosures ?? []).slice(0, 3).map((d, i) => (
            <p key={i} className="text-ink-muted/80">· {d}</p>
          ))}
        </div>
      )}
      {notice && (
        <p className="text-caption text-status-success" data-testid="symbol-asset-notice">{notice}</p>
      )}
      {error && (
        <p className="text-caption text-status-danger" data-testid="symbol-asset-error" role="alert">
          {error.code}: {error.message}
        </p>
      )}
    </div>
  );
}
