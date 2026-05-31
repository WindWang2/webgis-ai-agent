'use client';

import { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { Triangle, Layers, Scissors } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';

interface AnalysisTabProps {
  onSend: (text: string) => void;
}

type ToolKey = 'buffer' | 'overlay' | 'clip';

interface ToolDef {
  key: ToolKey;
  label: string;
  icon: typeof Triangle;
}

const TOOLS: ToolDef[] = [
  { key: 'buffer', label: '缓冲区分析', icon: Triangle },
  { key: 'overlay', label: '叠加分析', icon: Layers },
  { key: 'clip', label: '裁剪', icon: Scissors },
];

function LayerSelect({ layers, value, onChange, placeholder }: {
  layers: Layer[];
  value: string;
  onChange: (id: string) => void;
  placeholder: string;
}) {
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        backgroundColor: isDark ? 'rgba(255,255,255,0.04)' : '#fff',
        borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
        color: isDark ? '#e2e8f0' : '#334155',
      }}
      className="w-full text-xs border rounded-lg px-2 py-1.5 focus:outline-none"
    >
      <option value="">{placeholder}</option>
      {layers.map((l) => (
        <option key={l.id} value={l.id}>{l.name}</option>
      ))}
    </select>
  );
}

export function AnalysisTab({ onSend }: AnalysisTabProps) {
  const [activeTool, setActiveTool] = useState<ToolKey>('buffer');
  const layers = useHudStore((s) => s.layers);
  const theme = useHudStore((s) => s.theme);
  const accentColor = useHudStore((s) => s.accentColor);
  const isDark = theme === 'dark';

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

  const handleSubmit = () => {
    let prompt = '';
    if (activeTool === 'buffer') {
      if (!bufferLayer || !bufferDistance) return;
      prompt = `对图层 "${layerName(bufferLayer)}" 进行缓冲区分析，缓冲距离为 ${bufferDistance} 米`;
    } else if (activeTool === 'overlay') {
      if (!overlayLayerA || !overlayLayerB) return;
      const opMap: Record<string, string> = { intersection: '相交', union: '合并', difference: '差异', symmetric_difference: '对称差异' };
      prompt = `对图层 "${layerName(overlayLayerA)}" 和 "${layerName(overlayLayerB)}" 进行叠加分析，操作类型为${opMap[overlayOp] ?? overlayOp}`;
    } else if (activeTool === 'clip') {
      if (!clipTarget || !clipMask) return;
      prompt = `用图层 "${layerName(clipMask)}" 裁剪图层 "${layerName(clipTarget)}"`;
    }
    if (prompt) onSend(prompt);
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div
        className="p-3 border-b shrink-0"
        style={{
          borderBottomColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
          backgroundColor: isDark ? 'rgba(9, 9, 11, 0.4)' : 'rgba(255,255,255,0.4)',
        }}
      >
        <h2 className="text-xs font-bold tracking-wide uppercase" style={{ color: isDark ? '#e2e8f0' : '#1e293b' }}>
          空间分析
        </h2>
        <p className="text-[14px]" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>
          选择工具并配置参数
        </p>
      </div>

      {/* Tool Selector */}
      <div className="p-3 space-y-2 shrink-0">
        <div className="grid grid-cols-3 gap-2">
          {TOOLS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTool(key)}
              className="flex flex-col items-center gap-1 py-2 rounded-lg text-xs font-medium transition-all border"
              style={{
                backgroundColor: activeTool === key
                  ? (isDark ? 'rgba(255,255,255,0.08)' : '#fff')
                  : 'transparent',
                borderColor: activeTool === key
                  ? `${accentColor}33`
                  : (isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'),
                color: activeTool === key
                  ? (isDark ? '#fff' : '#1e293b')
                  : (isDark ? '#64748b' : '#94a3b8'),
              }}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Form */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {activeTool === 'buffer' && (
          <>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                输入图层
              </label>
              <LayerSelect layers={vectorLayers} value={bufferLayer} onChange={setBufferLayer} placeholder="选择图层" />
            </div>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                缓冲距离 (米)
              </label>
              <input
                type="number"
                value={bufferDistance}
                onChange={(e) => setBufferDistance(e.target.value)}
                placeholder="输入缓冲距离，如 500"
                style={{
                  backgroundColor: isDark ? 'rgba(255,255,255,0.03)' : '#fff',
                  borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
                  color: isDark ? '#f8fafc' : '#0f172a',
                }}
                className="w-full text-xs border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </>
        )}

        {activeTool === 'overlay' && (
          <>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                图层 A
              </label>
              <LayerSelect layers={vectorLayers} value={overlayLayerA} onChange={setOverlayLayerA} placeholder="选择图层 A" />
            </div>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                图层 B
              </label>
              <LayerSelect layers={vectorLayers} value={overlayLayerB} onChange={setOverlayLayerB} placeholder="选择图层 B" />
            </div>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                操作类型
              </label>
              <select
                value={overlayOp}
                onChange={(e) => setOverlayOp(e.target.value)}
                style={{
                  backgroundColor: isDark ? 'rgba(255,255,255,0.04)' : '#fff',
                  borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
                  color: isDark ? '#e2e8f0' : '#334155',
                }}
                className="w-full text-xs border rounded-lg px-2 py-1.5 focus:outline-none"
              >
                <option value="intersection">相交 (Intersection)</option>
                <option value="union">合并 (Union)</option>
                <option value="difference">差异 (Difference)</option>
                <option value="symmetric_difference">对称差异 (Symmetric Difference)</option>
              </select>
            </div>
          </>
        )}

        {activeTool === 'clip' && (
          <>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                目标图层
              </label>
              <LayerSelect layers={vectorLayers} value={clipTarget} onChange={setClipTarget} placeholder="选择目标图层" />
            </div>
            <div>
              <label className="block text-[14px] font-semibold mb-1" style={{ color: isDark ? '#94a3b8' : '#64748b' }}>
                裁剪边界图层
              </label>
              <LayerSelect layers={vectorLayers} value={clipMask} onChange={setClipMask} placeholder="选择裁剪边界" />
            </div>
          </>
        )}
      </div>

      {/* Submit */}
      <div
        className="p-3 border-t shrink-0"
        style={{ borderColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)' }}
      >
        <button
          className="w-full text-white font-bold py-2 rounded-lg shadow-md transition-all text-xs"
          style={{
            background: `linear-gradient(135deg, ${accentColor}, ${accentColor}dd)`,
            boxShadow: `0 4px 12px ${accentColor}25`,
          }}
          onClick={handleSubmit}
        >
          {activeTool === 'buffer' && '生成缓冲区'}
          {activeTool === 'overlay' && '生成叠加分析'}
          {activeTool === 'clip' && '执行裁剪'}
        </button>
      </div>
    </div>
  );
}

export default AnalysisTab;
