'use client';
import { memo, useState, useRef, useEffect, useSyncExternalStore } from 'react';
import { commitLayerStyleAndCommit } from '@/lib/mapspec/user-mutation';
import { useToastStore } from '@/components/ui/toast';
import { X, RotateCcw } from 'lucide-react';
import { motion } from 'framer-motion';
import { useHudStore } from '@/lib/store/useHudStore';
import type { LayerStyle } from '@/lib/types/layer';
import { setLayerOpacityAndCommit } from '@/lib/mapspec/user-mutation';
import {
  commitLayerOverrideForFrame,
  frameEditScopeLabel,
  resolveEditScope,
} from '@/lib/cartography/frame-edit-scope';
import { getFrameGridSnapshot } from '@/lib/cartography/frame-grid-store';
import { getCommittedMapSpec, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import { useT } from '@/lib/i18n/useT';
import { t as tNow } from '@/lib/i18n/t';
// G20：栅格专属工作台（局部挂载，不改全局布局/主题）
import { RasterAuthoringPanel } from '@/components/hud/raster-authoring-panel';
// G27：按尺度表达（可解释控制 + 局部覆盖，仅在有 committed spec 层时挂载）
import { ScalePortrayalPanel } from '@/components/hud/scale-portrayal-panel';

/** 渲染模式 id → 消息键（spec 层型语义见 specPaintPatchFrom）。 */
const MODE_LABELS: Record<string, string> = {
  vector: 'style.mode.vector',
  heatmap: 'style.mode.heatmap',
  grid: 'style.mode.grid',
};

export const LayerStylePanel = memo(function LayerStylePanel() {
  const t = useT('hud');
  const editingLayerId = useHudStore((s) => s.editingLayerId);
  const layers = useHudStore((s) => s.layers);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const setEditingLayerId = useHudStore((s) => s.setEditingLayerId);
  const [showRasterAuthoring, setShowRasterAuthoring] = useState(false);

  const layer = layers.find((l) => l.id === editingLayerId);
  // audit #840: committed MapSpec 存在时，HUD 层样式不进入 paint
  // （composeLiveMapSpec 的 layers 完全取自 committed）—— 面板样式控件必须
  // 显式禁用并说明，而不是静默 no-op；opacity/visibility 走 presentation
  // mutation，仍然有效。
  // #1077: 守卫解析别名 —— runtimePatch 挂载行的 id 是 geojson_ref 而
  // _mapspecLayerId 才是 spec 层 id；精确 id 匹配会让这些行误判为
  // 非 specBacked（样式控件可用但 compose 不消费 = 静默 no-op 被别名绕过）。
  const specLayerKey = layer?._mapspecLayerId ?? editingLayerId;
  const committedSpec = useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec);
  const specBacked = !!committedSpec
    && !!specLayerKey
    && Array.isArray((committedSpec as { layers?: { id?: string }[] }).layers)
    && (committedSpec as { layers?: { id?: string }[] }).layers!.some(
      (l) => l?.id === specLayerKey
        || (!!l?.id && l.id.startsWith(`${specLayerKey}__`)),
    );

  // v2(#1077)：LayerStyle patch → 规范 paint 键（按 SPEC 层型映射 ——
  // review R4-P2-7：HUD type==='vector' 不蕴含 spec 层型，width 仅对 line
  // 规范、strokeWidth 仅对 circle；错键会被 paint-bridge 丢弃并作为垃圾
  // 写进权威 spec）。无规范键的控件（brightness/contrast/saturation/
  // dashArray/fill/renderType 等滤镜类）返回空 —— 这些控件在 spec 层保持
  // 禁用（规范未建模，不发明语义）。
  const specLayerType = (() => {
    const specLayers = (committedSpec as { layers?: { id?: string; type?: string }[] })?.layers;
    const hit = specLayers?.find(
      (l) => l?.id === specLayerKey || (!!l?.id && l.id.startsWith(`${specLayerKey}__`)),
    );
    return hit?.type;
  })();
  // G20：数值栅格层挂局部表达工作台；旧 PNG / 瓦片层没有数值源，
  // 只显示「从已登记栅格绑定」的选择器，绝不出现伪波段/伪像元控件。
  const numericRaster = (() => {
    const doc = committedSpec as {
      layers?: Array<Record<string, unknown>>;
      sources?: Record<string, Record<string, unknown>>;
    } | null;
    const hit = doc?.layers?.find(
      (l) => l?.id === specLayerKey
        || (typeof l?.id === 'string' && l.id.startsWith(`${specLayerKey}__`)),
    );
    if (!hit || !hit.raster_style || typeof hit.source !== 'string') return null;
    const src = doc?.sources?.[hit.source];
    if (!src || src.type !== 'numeric_raster') return null;
    return {
      layerId: String(hit.id),
      refId: typeof src.refId === 'string' ? src.refId : undefined,
      committedStyle: hit.raster_style as never,
      committedLegend: (hit.legend_spec ?? null) as never,
    };
  })();

  const specPaintPatchFrom = (patch: Partial<LayerStyle>): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    const push = (k: string, v: unknown) => { if (v !== undefined) out[k] = v; };
    if (patch.color !== undefined) push('color', patch.color);
    if (patch.strokeColor !== undefined && (specLayerType === 'circle' || specLayerType === 'fill' || !specLayerType)) {
      push('strokeColor', patch.strokeColor);
    }
    if (patch.strokeWidth !== undefined) {
      if (specLayerType === 'line') push('width', patch.strokeWidth);
      else if (specLayerType === 'circle' || !specLayerType) push('strokeWidth', patch.strokeWidth);
    }
    if (patch.radius !== undefined) push('radius', patch.radius);
    if (patch.radius_px !== undefined) push('radius', patch.radius_px);
    if (patch.pointSize !== undefined && specLayerType !== 'raster') push('radius', patch.pointSize);
    return out;
  };

  const pendingStyleRef = useRef<Record<string, unknown>>({});
  const styleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const styleEpochRef = useRef(0);
  useEffect(() => () => { if (styleTimerRef.current) clearTimeout(styleTimerRef.current); }, []);
  useEffect(() => {
    if (styleTimerRef.current) clearTimeout(styleTimerRef.current);
    pendingStyleRef.current = {};
    styleEpochRef.current += 1;
  }, [editingLayerId]);

  const updateStyle = (patch: Partial<LayerStyle>) => {
    if (!layer) return;
    if (specBacked) {
      const paintPatch = specPaintPatchFrom(patch);
      if (Object.keys(paintPatch).length === 0) return;
      // 乐观本地行样式 + durable 提交：成功后 committed spec 驱动 reconcile
      // （spec 为源），失败/被取代由 commit 通道收敛并提示。
      updateLayer(layer.id, { style: { ...layer.style, ...patch } });
      pendingStyleRef.current = { ...pendingStyleRef.current, ...paintPatch };
      if (styleTimerRef.current) clearTimeout(styleTimerRef.current);
      const epoch = ++styleEpochRef.current;
      styleTimerRef.current = setTimeout(() => {
        const pending = pendingStyleRef.current;
        pendingStyleRef.current = {};
        styleTimerRef.current = null;
        void commitLayerStyleAndCommit(specLayerKey!, pending).catch((err: unknown) => {
          if (epoch !== styleEpochRef.current) return;
          const committed = getCommittedMapSpec()?.layers.find((candidate) => candidate.id === specLayerKey);
          const paint = committed?.paint ?? {};
          const rollback = { ...layer.style };
          const color = paint.color ?? paint['fill-color'] ?? paint['line-color'] ?? paint['circle-color'];
          const width = paint.width ?? paint['line-width'];
          const radius = paint.radius ?? paint['circle-radius'];
          if (typeof color === 'string') rollback.color = color;
          if (typeof width === 'number') rollback.strokeWidth = width;
          if (typeof radius === 'number') rollback.pointSize = radius;
          updateLayer(layer.id, { style: rollback });
          import('@/lib/api/transport').then(({ describeApiError }) => {
            useToastStore.getState().addToast(tNow('hud.style.commitFailed', {
              reason: describeApiError(err, tNow('hud.style.networkError')),
            }), 'error');
          }).catch(() => { /* noop */ });
        });
      }, 180);
      return;
    }
    updateLayer(layer.id, { style: { ...layer.style, ...patch } });
  };

  const [tempName, setTempName] = useState('');
  const [isRenaming, setIsRenaming] = useState(false);
  const [draftOpacity, setDraftOpacity] = useState<number | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming && nameRef.current) {
      nameRef.current.focus();
      nameRef.current.select();
    }
  }, [isRenaming]);

  // V7（审计 §2-M 防线二）：编辑目标行消失（会话切换/图层删除）时自愈
  // 复位 —— 此前 return null 把图层 tab 渲染成整页空白且钻入态永久滞留。
  const layerMissing = !layer && editingLayerId != null;
  useEffect(() => {
    if (layerMissing) setEditingLayerId(null);
  }, [layerMissing, setEditingLayerId]);

  if (!layer) return null;

  const style = layer.style || {};
  const color = style.color || '#00f2ff';
  const strokeColor = style.strokeColor || color;
  const strokeWidth = style.strokeWidth ?? 2;
  const fillEnabled = style.fill !== false;
  const renderType = style.renderType || 'vector';
  const radius = style.radius_px ?? style.radius ?? 30;
  const pointSize = style.pointSize ?? 5;
  const dashArray = style.dashArray || 'solid';
  const brightness = style.brightness ?? 1;
  const contrast = style.contrast ?? 1;
  const saturation = style.saturation ?? 1;

  const effectiveOpacity = draftOpacity ?? layer.opacity;

  const commitOpacity = (val: number) => {
    if (draftOpacity === null || val === layer.opacity) return;
    setDraftOpacity(null);
    // G25（P2 编辑范围路由）：多画幅网格打开且有活动画幅时，「允许
    // override」的图层操作落到该帧 layerOverrides（仅本幅 —— 其余帧与
    // 根层默认值不动）；无活动画幅走既有根级 presentation 提交。
    const gridSnap = getFrameGridSnapshot();
    const frameScope = resolveEditScope(gridSnap.open, gridSnap.activeFrameId);
    if (frameScope === 'frame_only' && gridSnap.activeFrameId) {
      const specLayerId = layer._mapspecLayerId || layer.id;
      void commitLayerOverrideForFrame(gridSnap.activeFrameId, {
        layerId: specLayerId,
        override: { opacity: val },
      })
        .then((outcome) => {
          if (outcome !== 'superseded') {
            updateLayer(layer.id, { opacity: val });
          }
        })
        .catch(() => {
          useToastStore.getState().addToast(
            t('layerStyle.frameOverrideFailed', { scope: frameEditScopeLabel(frameScope) }),
            'error',
          );
        });
      return;
    }
    // audit #842: 经带回滚的 wrapper 提交 —— 裸 void commitLayerPresentation
    // 失败时 unhandled rejection 且本地透明度不回滚（与服务端分叉）。
    void setLayerOpacityAndCommit(layer.id, val);
  };

  return (
    <motion.div
      initial={{ x: 40, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 40, opacity: 0 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="flex flex-col h-full bg-surface-panel text-ink"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-edge-subtle">
        <button
          onClick={() => setEditingLayerId(null)}
          aria-label={t('style.closeAria')}
          className="text-ink-muted hover:text-ink transition-colors cursor-pointer"
        >
          <X size={16} />
        </button>
        <span className="text-title font-display font-semibold text-ink uppercase tracking-wider">
          {t('style.title')}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* G27：按尺度表达（可解释控制 + 局部覆盖），仅在 spec 层在场时挂载 */}
        {specBacked && specLayerKey && (
          <ScalePortrayalPanel layerId={specLayerKey} />
        )}
        {/* Name */}
        <div>
          <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">{t('style.name')}</span>
          {isRenaming ? (
            <div className="flex items-center gap-1">
              <input
                ref={nameRef}
                value={tempName}
                onChange={(e) => setTempName(e.target.value)}
                aria-label={t('style.renameAria')}
                className="flex-1 text-body bg-surface-sunken border border-edge-subtle focus:border-status-accent rounded px-2 py-1 text-ink focus:outline-none"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    if (tempName.trim()) updateLayer(layer.id, { name: tempName.trim() });
                    setIsRenaming(false);
                  }
                  if (e.key === 'Escape') setIsRenaming(false);
                }}
              />
            </div>
          ) : (
            <div
              className="text-body text-ink cursor-pointer hover:text-status-accent transition-colors"
              onDoubleClick={() => { setTempName(layer.name); setIsRenaming(true); }}
            >
              {layer.name}
              <span className="text-ink-disabled ml-2 text-caption">{t('style.doubleClickEdit')}</span>
            </div>
          )}
        </div>

        {/* Type & Group */}
        <div className="flex items-center gap-2">
          <span className="text-micro px-1.5 py-0.5 rounded-full bg-status-accent-soft text-status-accent border border-status-accent-border font-semibold uppercase">
            {layer.type}
          </span>
          {layer.group && (
            <span className="text-micro px-1.5 py-0.5 rounded-full bg-surface-sunken text-ink-muted border border-edge-subtle">
              {layer.group}
            </span>
          )}
        </div>

        {specBacked && (
          <div className="rounded-lg border border-status-accent-border bg-status-accent-soft px-3 py-2 text-caption leading-relaxed text-ink-secondary">
            {t('style.specManaged')}
          </div>
        )}

        {/* audit #840: spec-backed 图层上以下样式控件全部禁用（写 HUD store
            不进 paint，此前是静默 no-op）。 */}
        {/* v2(#1077)：规范键控件对 spec 层启用（durable 通道）；滤镜类控件
            在下方按 specBacked 单独禁用。 */}
        <fieldset>
        {/* === VECTOR CONTROLS === */}
        {layer.type === 'vector' && (
          <>
            {/* Fill Color */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">{t('style.fillColor')}</span>
              <div className="flex items-center gap-2">
                <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-edge-subtle shadow-xs">
                  <input type="color" value={color}
                    onChange={(e) => updateStyle({ color: e.target.value })}
                    aria-label={t('style.fillColor')}
                    className="absolute inset-0 w-full h-full cursor-pointer" />
                </div>
                <span className="text-meta text-ink-muted font-mono">{color}</span>
              </div>
            </div>

            {/* Stroke Color */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">{t('style.strokeColor')}</span>
              <div className="flex items-center gap-2">
                <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-edge-subtle shadow-xs">
                  <input type="color" value={strokeColor}
                    onChange={(e) => updateStyle({ strokeColor: e.target.value })}
                    aria-label={t('style.strokeColor')}
                    className="absolute inset-0 w-full h-full cursor-pointer" />
                </div>
                <span className="text-meta text-ink-muted font-mono">{strokeColor}</span>
              </div>
            </div>

            {/* Stroke Width */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.strokeWidth')} <span className="text-ink-disabled font-mono">{strokeWidth}px</span>
              </span>
              <input type="range" min={0} max={10} step={0.5} value={strokeWidth}
                onChange={(e) => updateStyle({ strokeWidth: parseFloat(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>

            {/* Point Size */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.pointSize')} <span className="text-ink-disabled font-mono">{pointSize}px</span>
              </span>
              <input type="range" min={1} max={20} step={0.5} value={pointSize}
                onChange={(e) => updateStyle({ pointSize: parseFloat(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>

            {/* Line Dash */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">{t('style.dashType')}</span>
              <div className="flex gap-1">
                {([
                  { value: 'solid', labelKey: 'style.dash.solid', dash: '' },
                  { value: 'dashed', labelKey: 'style.dash.dashed', dash: '4 2' },
                  { value: 'dotted', labelKey: 'style.dash.dotted', dash: '1 2' },
                  { value: 'dashdot', labelKey: 'style.dash.dashdot', dash: '4 2 1 2' },
                ] as const).map((d) => (
                  <button
                    key={d.value}
                    disabled={specBacked}
                    onClick={() => updateStyle({ dashArray: d.value })}
                    className={`flex-1 px-2 py-1.5 text-caption rounded-lg font-medium transition-colors border ${
                      dashArray === d.value
                        ? 'bg-status-accent-soft text-status-accent border-status-accent-border font-semibold'
                        : 'text-ink-muted border-edge-subtle hover:text-ink hover:bg-surface-hover hover:border-edge'
                    }`}
                  >
                    {t(d.labelKey)}
                  </button>
                ))}
              </div>
            </div>

            {/* Fill Toggle */}
            <div className="flex items-center justify-between">
              <span className="text-caption text-ink-muted uppercase tracking-wider">{t('style.fillToggle')}</span>
              <button
                disabled={specBacked}
                onClick={() => updateStyle({ fill: !fillEnabled })}
                className={`w-8 h-4 rounded-full transition-colors relative cursor-pointer ${fillEnabled ? 'bg-status-accent' : 'bg-surface-sunken border border-edge-subtle'}`}
              >
                <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
                  fillEnabled ? 'left-[18px] bg-surface-raised shadow-xs' : 'left-0.5 bg-ink-disabled'
                }`} />
              </button>
            </div>

            {/* Render Mode Switch */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">{t('style.renderMode')}</span>
              <div className="flex gap-1">
                {(['vector', 'heatmap', 'grid'] as const).map((mode) => (
                  <button
                    key={mode}
                    disabled={specBacked}
                    onClick={() => updateStyle({ renderType: mode })}
                    className={`flex-1 px-2 py-1.5 text-caption rounded-lg font-medium transition-colors border ${
                      renderType === mode
                        ? 'bg-status-accent-soft text-status-accent border-status-accent-border font-semibold'
                        : 'text-ink-muted border-edge-subtle hover:text-ink hover:bg-surface-hover hover:border-edge'
                    }`}
                  >
                    {t(MODE_LABELS[mode])}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {/* === HEATMAP CONTROLS === */}
        {layer.type === 'heatmap' && (
          <>
            {/* audit #840: 色带/热力强度控件已移除 —— 两个参数在任何渲染
                路径都没有消费者（grep: 仅本面板读写），假控件不如没有。 */}

            {/* Radius */}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.heatmapRadius')} <span className="text-ink-disabled font-mono">{radius}px</span>
              </span>
              <input type="range" min={4} max={80} step={1} value={Math.min(80, radius)}
                onChange={(e) => updateStyle({ radius_px: parseInt(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>

          </>
        )}

        {/* === RASTER CONTROLS === */}
        {(layer.type === 'raster' || layer.type === 'tile') && (
          <>
            {/* G20：数值栅格表达工作台入口。旧亮度/对比/饱和控件仍按
                specBacked 禁用并如实说明，不新增静默无效的旋钮。 */}
            <button
              type="button"
              aria-expanded={showRasterAuthoring}
              onClick={() => setShowRasterAuthoring((v) => !v)}
              className="w-full text-left text-caption text-status-accent hover:underline focus:outline-none focus:ring-2 focus:ring-status-accent rounded"
            >
              {t('rasterAuthoring.title')}
            </button>
            {showRasterAuthoring && (
              <RasterAuthoringPanel
                layerId={numericRaster?.layerId ?? String(specLayerKey ?? layer.id)}
                refId={numericRaster?.refId}
                committedStyle={numericRaster?.committedStyle ?? null}
                committedLegend={numericRaster?.committedLegend ?? null}
              />
            )}
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.brightness')} <span className="text-ink-disabled font-mono">{brightness.toFixed(1)}</span>
              </span>
              <input type="range" min={0.5} max={2} step={0.1} value={brightness} disabled={specBacked}
                onChange={(e) => updateStyle({ brightness: parseFloat(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.contrast')} <span className="text-ink-disabled font-mono">{contrast.toFixed(1)}</span>
              </span>
              <input type="range" min={0.5} max={2} step={0.1} value={contrast} disabled={specBacked}
                onChange={(e) => updateStyle({ contrast: parseFloat(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>
            <div>
              <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
                {t('style.saturation')} <span className="text-ink-disabled font-mono">{saturation.toFixed(1)}</span>
              </span>
              <input type="range" min={0} max={2} step={0.1} value={saturation} disabled={specBacked}
                onChange={(e) => updateStyle({ saturation: parseFloat(e.target.value) })}
                className="w-full accent-status-accent cursor-pointer" />
            </div>
          </>
        )}

        </fieldset>

        {/* === OPACITY (ALL TYPES) === */}
        <div>
          <span className="text-caption text-ink-muted uppercase tracking-wider mb-1.5 block">
            {t('style.opacity')} <span className="text-ink-disabled font-mono">{Math.round(effectiveOpacity * 100)}%</span>
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={effectiveOpacity}
            onChange={(e) => setDraftOpacity(parseFloat(e.target.value))}
            onPointerUp={() => commitOpacity(effectiveOpacity)}
            onKeyUp={() => commitOpacity(effectiveOpacity)}
            onBlur={() => commitOpacity(effectiveOpacity)}
            className="w-full accent-status-accent cursor-pointer"
          />
        </div>

        {/* Reset */}
        <button
          disabled={specBacked}
          onClick={() => {
            updateLayer(layer.id, { opacity: 0.8, style: {} });
          }}
          className="flex items-center justify-center gap-1.5 w-full py-2 text-body text-ink-muted hover:text-ink border border-edge-subtle rounded-lg hover:border-edge hover:bg-surface-hover transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <RotateCcw size={12} /> {t('style.reset')}
        </button>
      </div>
    </motion.div>
  );
});
