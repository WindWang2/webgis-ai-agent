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
      className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-meta text-ink"
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
      <label htmlFor={id} className="mb-1 block text-meta font-semibold text-ink-secondary">
        {label}
      </label>
      {children}
    </div>
  );
}

export function AnalysisTab({ onSend, aiStatus }: AnalysisTabProps & { aiStatus?: import('@/lib/store/hud-types').AiStatus }) {
  const uid = useId();
  const [activeTool, setActiveTool] = useState<ToolKey>('buffer');
  const layers = useHudStore((s) => s.layers);
  const setActiveLeftTab = useHudStore((s) => s.setActiveLeftTab);
  const isBusy = aiStatus === 'thinking' || aiStatus === 'acting';

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
  const baseCanSubmit =
    activeTool === 'buffer'
      ? !!bufferLayer && !isNaN(bufferDist) && bufferDist > 0
      : activeTool === 'overlay'
        ? !!overlayLayerA && !!overlayLayerB
        : !!clipTarget && !!clipMask;
  const canSubmit = baseCanSubmit && !isBusy;

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
    if (prompt) {
      setActiveLeftTab('chat');
      onSend(prompt);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tool Selector */}
      <div className="p-3 space-y-2 shrink-0">
        {/* Review P2 修复：radiogroup 补 APG 键盘契约 —— roving tabindex +
            方向键移动选中并跟随焦点。 */}
        <div
          className="grid grid-cols-3 gap-2"
          role="radiogroup"
          aria-label="分析工具"
          onKeyDown={(e) => {
            if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
            e.preventDefault();
            const idx = TOOLS.findIndex((t) => t.key === activeTool);
            const delta = e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 1;
            const next = TOOLS[(idx + delta + TOOLS.length) % TOOLS.length];
            setActiveTool(next.key);
            setTimeout(() => document.getElementById(`analysis-tool-${next.key}`)?.focus(), 0);
          }}
        >
          {TOOLS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              id={`analysis-tool-${key}`}
              role="radio"
              aria-checked={activeTool === key}
              tabIndex={activeTool === key ? 0 : -1}
              onClick={() => setActiveTool(key)}
              className="flex flex-col items-center gap-1 rounded-md border py-2 text-meta font-medium transition-all"
              style={{
                backgroundColor: activeTool === key
                  ? 'var(--surface-raised)'
                  : 'transparent',
                borderColor: activeTool === key
                  ? 'color-mix(in srgb, var(--agent-accent) 33%, transparent)'
                  : 'var(--border-subtle)',
                color: activeTool === key
                  ? 'var(--text-primary)'
                  : 'var(--text-muted)',
              }}
            >
              <Icon size={16} aria-hidden />
              {label}
            </button>
          ))}
        </div>
        {/* 当前工具目标与产出（UI V3：强调任务目标而不是工具堆积） */}
        <p className="text-meta leading-relaxed text-ink-muted">
          {activeDef.desc} · 输出：{activeDef.output}
        </p>
      </div>

      {/* Form */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {vectorLayers.length === 0 && (
          <InlineNotice variant="info">
            暂无可分析的矢量图层。可先在数据面板加载数据集，或直接告诉 AI 你要分析的内容。
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
                className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-meta text-ink"
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
                className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-meta text-ink"
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
        style={{ borderColor: 'var(--border-subtle)' }}
      >
        <button
          className="w-full rounded-md py-1.5 text-meta font-semibold text-ink-on-accent transition-opacity hover:opacity-90 disabled:opacity-40"
          style={{
            background: 'linear-gradient(135deg, var(--agent-accent), color-mix(in srgb, var(--agent-accent) 87%, transparent))',
            boxShadow: '0 4px 12px color-mix(in srgb, var(--agent-accent) 15%, transparent)',
          }}
          disabled={!canSubmit}
          aria-busy={isBusy || undefined}
          onClick={handleSubmit}
        >
          {isBusy
            ? 'AI 忙碌中…'
            : activeTool === 'buffer'
              ? '生成缓冲区'
              : activeTool === 'overlay'
                ? '生成叠加分析'
                : '执行裁剪'}
        </button>
        {isBusy ? (
          <p className="mt-1.5 text-caption text-ink-muted">AI 正在处理上一条指令，完成后可再次提交。</p>
        ) : !baseCanSubmit ? (
          <p className="mt-1.5 text-caption text-ink-muted">
            请选择所需图层{activeTool === 'buffer' ? '并输入有效距离' : ''}后执行
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default AnalysisTab;
