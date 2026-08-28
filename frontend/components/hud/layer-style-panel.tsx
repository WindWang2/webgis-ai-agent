'use client';
import { memo, useState, useRef, useEffect, useSyncExternalStore } from 'react';
import { commitLayerStyleAndCommit } from '@/lib/mapspec/user-mutation';
import { useToastStore } from '@/components/ui/toast';
import { X, RotateCcw } from 'lucide-react';
import { motion } from 'framer-motion';
import { useHudStore } from '@/lib/store/useHudStore';
import type { LayerStyle } from '@/lib/types/layer';
import { setLayerOpacityAndCommit } from '@/lib/mapspec/user-mutation';
import { getCommittedMapSpec, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';

const MODE_LABELS: Record<string, string> = { vector: '矢量', heatmap: '热力', grid: '格网' };

export const LayerStylePanel = memo(function LayerStylePanel() {
  const editingLayerId = useHudStore((s) => s.editingLayerId);
  const layers = useHudStore((s) => s.layers);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const setEditingLayerId = useHudStore((s) => s.setEditingLayerId);

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

  // v2(#1077)：LayerStyle patch → 规范 paint 键（按层型）。无规范键的
  // 控件（brightness/contrast/saturation/dashArray 等滤镜类）返回空 ——
  // 这些控件在 spec 层保持禁用（规范未建模，不发明语义）。
  const specPaintPatchFrom = (patch: Partial<LayerStyle>): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    const type = layer?.type;
    const push = (k: string, v: unknown) => { if (v !== undefined) out[k] = v; };
    if (patch.color !== undefined) push('color', patch.color);
    if (patch.strokeColor !== undefined) push('strokeColor', patch.strokeColor);
    if (patch.strokeWidth !== undefined) {
      push(type === 'vector' ? 'width' : 'strokeWidth', patch.strokeWidth);
    }
    if (patch.radius !== undefined) push('radius', patch.radius);
    if (patch.radius_px !== undefined) push('radius', patch.radius_px);
    if (patch.pointSize !== undefined && type !== 'raster') push('radius', patch.pointSize);
    return out;
  };

  const updateStyle = (patch: Partial<LayerStyle>) => {
    if (!layer) return;
    if (specBacked) {
      const paintPatch = specPaintPatchFrom(patch);
      if (Object.keys(paintPatch).length === 0) return;
      // 乐观本地行样式 + durable 提交：成功后 committed spec 驱动 reconcile
      // （spec 为源），失败/被取代由 commit 通道收敛并提示。
      updateLayer(layer.id, { style: { ...layer.style, ...patch } });
      void commitLayerStyleAndCommit(specLayerKey!, paintPatch).catch(() => {
        // 非 superseded 失败：本地乐观回滚（与 U-3 语义一致）
        updateLayer(layer.id, { style: { ...layer.style } });
        import('@/lib/api/transport').then(({ describeApiError }) => {
          useToastStore.getState().addToast(
            `样式修改未生效（已恢复）：${describeApiError({}, '网络错误')}`,
            'error',
          );
        }).catch(() => { /* noop */ });
      });
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
    setDraftOpacity(null);
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
      className="flex flex-col h-full"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06]">
        <button
          onClick={() => setEditingLayerId(null)}
          className="text-white/30 hover:text-white/60 transition-colors"
        >
          <X size={16} />
        </button>
        <span className="text-[15px] font-display font-semibold text-white/50 uppercase tracking-wider">
          图层样式
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* Name */}
        <div>
          <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">名称</label>
          {isRenaming ? (
            <div className="flex items-center gap-1">
              <input
                ref={nameRef}
                value={tempName}
                onChange={(e) => setTempName(e.target.value)}
                aria-label="重命名图层"
                className="flex-1 text-[15px] bg-white/[0.06] border border-hud-cyan/30 rounded px-2 py-1 text-white/90 focus:outline-none focus:border-hud-cyan/60"
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
              className="text-[15px] text-white/70 cursor-pointer hover:text-white/90 transition-colors"
              onDoubleClick={() => { setTempName(layer.name); setIsRenaming(true); }}
            >
              {layer.name}
              <span className="text-white/15 ml-2 text-[15px]">双击编辑</span>
            </div>
          )}
        </div>

        {/* Type & Group */}
        <div className="flex items-center gap-2">
          <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-hud-cyan/10 text-hud-cyan border border-hud-cyan/20 font-semibold uppercase">
            {layer.type}
          </span>
          {layer.group && (
            <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-white/[0.04] text-white/25 border border-white/[0.06]">
              {layer.group}
            </span>
          )}
        </div>

        {specBacked && (
          <div className="rounded-lg border border-hud-cyan/20 bg-hud-cyan/5 px-3 py-2 text-[15px] leading-relaxed text-white/45">
            该图层由制图规范（MapSpec）管理：颜色/描边/尺寸等规范样式修改会
            持久提交到地图规范（#1077）；滤镜类调整（亮度/对比度/饱和度）暂
            不支持；透明度走独立通道，始终有效。
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
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">填充颜色</label>
              <div className="flex items-center gap-2">
                <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-white/10">
                  <input type="color" value={color}
                    onChange={(e) => updateStyle({ color: e.target.value })}
                    aria-label="填充颜色"
                    className="absolute inset-0 w-full h-full cursor-pointer" />
                </div>
                <span className="text-[14px] text-white/30 font-mono">{color}</span>
              </div>
            </div>

            {/* Stroke Color */}
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">描边颜色</label>
              <div className="flex items-center gap-2">
                <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-white/10">
                  <input type="color" value={strokeColor}
                    onChange={(e) => updateStyle({ strokeColor: e.target.value })}
                    aria-label="描边颜色"
                    className="absolute inset-0 w-full h-full cursor-pointer" />
                </div>
                <span className="text-[14px] text-white/30 font-mono">{strokeColor}</span>
              </div>
            </div>

            {/* Stroke Width */}
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                描边宽度 <span className="text-white/15 font-mono">{strokeWidth}px</span>
              </label>
              <input type="range" min={0} max={10} step={0.5} value={strokeWidth}
                onChange={(e) => updateStyle({ strokeWidth: parseFloat(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>

            {/* Point Size */}
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                点大小 <span className="text-white/15 font-mono">{pointSize}px</span>
              </label>
              <input type="range" min={1} max={20} step={0.5} value={pointSize}
                onChange={(e) => updateStyle({ pointSize: parseFloat(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>

            {/* Line Dash */}
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">线型</label>
              <div className="flex gap-1">
                {([
                  { value: 'solid', label: '实线', dash: '' },
                  { value: 'dashed', label: '虚线', dash: '4 2' },
                  { value: 'dotted', label: '点线', dash: '1 2' },
                  { value: 'dashdot', label: '点划线', dash: '4 2 1 2' },
                ] as const).map((d) => (
                  <button
                    key={d.value}
                    disabled={specBacked}
                    onClick={() => updateStyle({ dashArray: d.value })}
                    className={`flex-1 px-2 py-1.5 text-[15px] rounded-lg font-semibold transition-colors ${
                      dashArray === d.value
                        ? 'bg-hud-cyan/20 text-hud-cyan'
                        : 'text-white/20 hover:text-white/40 hover:bg-white/[0.03]'
                    }`}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Fill Toggle */}
            <div className="flex items-center justify-between">
              <label className="text-[15px] text-white/25 uppercase tracking-wider">填充开关</label>
              <button
                onClick={() => updateStyle({ fill: !fillEnabled })}
                className={`w-8 h-4 rounded-full transition-colors relative ${fillEnabled ? 'bg-hud-cyan/40' : 'bg-white/10'}`}
              >
                <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
                  fillEnabled ? 'left-[18px] bg-hud-cyan' : 'left-0.5 bg-white/30'
                }`} />
              </button>
            </div>

            {/* Render Mode Switch */}
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">渲染模式</label>
              <div className="flex gap-1">
                {(['vector', 'heatmap', 'grid'] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => updateStyle({ renderType: mode })}
                    className={`flex-1 px-2 py-1.5 text-[15px] rounded-lg font-semibold transition-colors ${
                      renderType === mode
                        ? 'bg-hud-cyan/20 text-hud-cyan'
                        : 'text-white/20 hover:text-white/40 hover:bg-white/[0.03]'
                    }`}
                  >
                    {MODE_LABELS[mode]}
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
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                热力半径 <span className="text-white/15 font-mono">{radius}px</span>
              </label>
              <input type="range" min={4} max={80} step={1} value={Math.min(80, radius)}
                onChange={(e) => updateStyle({ radius_px: parseInt(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>

          </>
        )}

        {/* === RASTER CONTROLS === */}
        {(layer.type === 'raster' || layer.type === 'tile') && (
          <>
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                亮度 <span className="text-white/15 font-mono">{brightness.toFixed(1)}</span>
              </label>
              <input type="range" min={0.5} max={2} step={0.1} value={brightness} disabled={specBacked}
                onChange={(e) => updateStyle({ brightness: parseFloat(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                对比度 <span className="text-white/15 font-mono">{contrast.toFixed(1)}</span>
              </label>
              <input type="range" min={0.5} max={2} step={0.1} value={contrast} disabled={specBacked}
                onChange={(e) => updateStyle({ contrast: parseFloat(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>
            <div>
              <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
                饱和度 <span className="text-white/15 font-mono">{saturation.toFixed(1)}</span>
              </label>
              <input type="range" min={0} max={2} step={0.1} value={saturation} disabled={specBacked}
                onChange={(e) => updateStyle({ saturation: parseFloat(e.target.value) })}
                className="w-full accent-hud-cyan" />
            </div>
          </>
        )}

        </fieldset>

        {/* === OPACITY (ALL TYPES) === */}
        <div>
          <label className="text-[15px] text-white/25 uppercase tracking-wider mb-1.5 block">
            透明度 <span className="text-white/15 font-mono">{Math.round(effectiveOpacity * 100)}%</span>
          </label>
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
            className="w-full accent-hud-cyan"
          />
        </div>

        {/* Reset */}
        <button
          disabled={specBacked}
          onClick={() => {
            updateLayer(layer.id, { opacity: 0.8, style: {} });
          }}
          className="flex items-center justify-center gap-1.5 w-full py-2 text-[15px] text-white/25 hover:text-white/50 border border-white/[0.06] rounded-lg hover:border-white/[0.12] transition-all"
        >
          <RotateCcw size={10} /> 重置样式
        </button>
      </div>
    </motion.div>
  );
});
