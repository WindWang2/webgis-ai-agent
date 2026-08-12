'use client';

import { useId, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { Triangle, Layers, Scissors } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';
import { InlineNotice } from '@/components/shared/inline-notice';

interface AnalysisTabProps {
  onSend: (text: string) => void;
}

type ToolKey = 'buffer' | 'overlay' | 'clip';

interface ToolDef {
  key: ToolKey;
  label: string;
  icon: typeof Triangle;
  /** 工具目标（做什么） */
  desc: string;
  /** 期望产出 */
  output: string;
}

const TOOLS: ToolDef[] = [
  { key: 'buffer', label: '缓冲区分析', icon: Triangle, desc: '为图层要素生成指定距离的缓冲区域', output: '新的缓冲区图层' },
  { key: 'overlay', label: '叠加分析', icon: Layers, desc: '计算两个图层的空间叠加关系', output: '叠加结果图层' },
  { key: 'clip', label: '裁剪', icon: Scissors, desc: '用边界图层裁剪目标图层范围', output: '裁剪结果图层' },
];

function LayerSelect({ id, layers, value, onChange, placeholder }: {
  id?: string;
  layers: Layer[];
  value: string;
  onChange: (id: string) => void;
  placeholder: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        backgroundColor: 'var(--theme-bg-input)',
        borderColor: 'var(--theme-border)',
        color: 'var(--theme-text-primary)',
      }}
      className="w-full text-xs border rounded-lg px-2 py-1.5"
    >
      <option value="">{placeholder}</option>
      {layers.map((l) => (
        <option key={l.id} value={l.id}>{l.name}</option>
      ))}
    </select>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="block text-[12px] font-semibold mb-1" style={{ color: 'var(--theme-text-secondary)' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

export function AnalysisTab({ onSend }: AnalysisTabProps) {
  const uid = useId();
  const [activeTool, setActiveTool] = useState<ToolKey>('buffer');
  const layers = useHudStore((s) => s.layers);
  const accentColor = useHudStore((s) => s.accentColor);

  // Buffer state
  const [bufferLayer, setBufferLayer] = useState('');
  const [bufferDistance, setBufferDistance] = useState('');

  // Overlay state
  const [overlayLayerA, setOverlayLayerA] = useState('');
  const [overlayLayerB, setOverlayLayerB] = useState('');
  const [overlayOp, setOverlayOp] = useState('intersection');

  // Clip state
  const [clipTarget, setClipTarget] = useState('');
  const [clipMask, setClipMask] = useState('');

  const vectorLayers = layers.filter((l) => l.type === 'vector');
  const layerName = (id: string) => layers.find((l) => l.id === id)?.name ?? id;
  const activeDef = TOOLS.find((t) => t.key === activeTool)!;

  const bufferDist = parseFloat(bufferDistance);
  const canSubmit =
    activeTool === 'buffer'
      ? !!bufferLayer && !isNaN(bufferDist) && bufferDist > 0
      : activeTool === 'overlay'
        ? !!overlayLayerA && !!overlayLayerB
        : !!clipTarget && !!clipMask;

  const handleSubmit = () => {
    if (!canSubmit) return;
    let prompt = '';
    if (activeTool === 'buffer') {
      prompt = `对图层 "${layerName(bufferLayer)}" 进行缓冲区分析，缓冲距离为 ${bufferDistance} 米`;
    } else if (activeTool === 'overlay') {
      const opMap: Record<string, string> = { intersection: '相交', union: '合并', difference: '差异', symmetric_difference: '对称差异' };
      prompt = `对图层 "${layerName(overlayLayerA)}" 和 "${layerName(overlayLayerB)}" 进行叠加分析，操作类型为${opMap[overlayOp] ?? overlayOp}`;
    } else if (activeTool === 'clip') {
      prompt = `用图层 "${layerName(clipMask)}" 裁剪图层 "${layerName(clipTarget)}"`;
    }
    if (prompt) onSend(prompt);
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tool Selector */}
      <div className="p-3 space-y-2 shrink-0">
        <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="分析工具">
          {TOOLS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              role="radio"
              aria-checked={activeTool === key}
              onClick={() => setActiveTool(key)}
              className="flex flex-col items-center gap-1 py-2 rounded-lg text-xs font-medium transition-all border"
              style={{
                backgroundColor: activeTool === key
                  ? 'var(--theme-bg-subtle)'
                  : 'transparent',
                borderColor: activeTool === key
                  ? `${accentColor}55`
                  : 'var(--theme-border)',
                color: activeTool === key
                  ? 'var(--theme-text-primary)'
                  : 'var(--theme-text-muted)',
              }}
            >
              <Icon size={16} aria-hidden />
              {label}
            </button>
          ))}
        </div>
        {/* 当前工具目标与产出（UI V3：强调任务目标而不是工具堆积） */}
        <p className="text-[12px] leading-relaxed" style={{ color: 'var(--theme-text-muted)' }}>
          {activeDef.desc} · 输出：{activeDef.output}
        </p>
      </div>

      {/* Form */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {vectorLayers.length === 0 && (
          <InlineNotice variant="info">
            暂无可分析的矢量图层。可先在数据织网加载数据集，或直接告诉 AI 你要分析的内容。
          </InlineNotice>
        )}

        {activeTool === 'buffer' && (
          <>
            <Field id={`${uid}-buffer-layer`} label="输入图层">
              <LayerSelect id={`${uid}-buffer-layer`} layers={vectorLayers} value={bufferLayer} onChange={setBufferLayer} placeholder="选择图层" />
            </Field>
            <Field id={`${uid}-buffer-dist`} label="缓冲距离 (米)">
              <input
                id={`${uid}-buffer-dist`}
                type="number"
                value={bufferDistance}
                onChange={(e) => setBufferDistance(e.target.value)}
                placeholder="输入缓冲距离，如 500"
                style={{
                  backgroundColor: 'var(--theme-bg-input)',
                  borderColor: 'var(--theme-border)',
                  color: 'var(--theme-text-primary)',
                }}
                className="w-full text-xs border rounded-lg px-3 py-2"
              />
            </Field>
          </>
        )}

        {activeTool === 'overlay' && (
          <>
            <Field id={`${uid}-overlay-a`} label="图层 A">
              <LayerSelect id={`${uid}-overlay-a`} layers={vectorLayers} value={overlayLayerA} onChange={setOverlayLayerA} placeholder="选择图层 A" />
            </Field>
            <Field id={`${uid}-overlay-b`} label="图层 B">
              <LayerSelect id={`${uid}-overlay-b`} layers={vectorLayers} value={overlayLayerB} onChange={setOverlayLayerB} placeholder="选择图层 B" />
            </Field>
            <Field id={`${uid}-overlay-op`} label="操作类型">
              <select
                id={`${uid}-overlay-op`}
                value={overlayOp}
                onChange={(e) => setOverlayOp(e.target.value)}
                style={{
                  backgroundColor: 'var(--theme-bg-input)',
                  borderColor: 'var(--theme-border)',
                  color: 'var(--theme-text-primary)',
                }}
                className="w-full text-xs border rounded-lg px-2 py-1.5"
              >
                <option value="intersection">相交 (Intersection)</option>
                <option value="union">合并 (Union)</option>
                <option value="difference">差异 (Difference)</option>
                <option value="symmetric_difference">对称差异 (Symmetric Difference)</option>
              </select>
            </Field>
          </>
        )}

        {activeTool === 'clip' && (
          <>
            <Field id={`${uid}-clip-target`} label="目标图层">
              <LayerSelect id={`${uid}-clip-target`} layers={vectorLayers} value={clipTarget} onChange={setClipTarget} placeholder="选择目标图层" />
            </Field>
            <Field id={`${uid}-clip-mask`} label="裁剪边界图层">
              <LayerSelect id={`${uid}-clip-mask`} layers={vectorLayers} value={clipMask} onChange={setClipMask} placeholder="选择裁剪边界" />
            </Field>
          </>
        )}
      </div>

      {/* Submit */}
      <div
        className="p-3 border-t shrink-0"
        style={{ borderColor: 'var(--theme-border)' }}
      >
        <button
          className="w-full text-white font-bold py-2 rounded-lg shadow-md transition-all text-xs disabled:opacity-40"
          style={{
            background: `linear-gradient(135deg, ${accentColor}, ${accentColor}dd)`,
            boxShadow: `0 4px 12px ${accentColor}25`,
          }}
          disabled={!canSubmit}
          onClick={handleSubmit}
        >
          {activeTool === 'buffer' && '生成缓冲区'}
          {activeTool === 'overlay' && '生成叠加分析'}
          {activeTool === 'clip' && '执行裁剪'}
        </button>
        {!canSubmit && (
          <p className="mt-1.5 text-[11px]" style={{ color: 'var(--theme-text-subtle)' }}>
            请选择所需图层{activeTool === 'buffer' ? '并输入有效距离' : ''}后执行
          </p>
        )}
      </div>
    </div>
  );
}

export default AnalysisTab;
