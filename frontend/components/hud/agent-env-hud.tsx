'use client';

import { X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { CausalTrace } from './causal-trace';

interface AgentEnvHudProps {
  open: boolean;
  onClose: () => void;
}

export function AgentEnvHud({ open, onClose }: AgentEnvHudProps) {
  const viewport = useHudStore((s) => s.viewport);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const is3D = useHudStore((s) => s.is3D);
  const layers = useHudStore((s) => s.layers);

  return (
    <div
      style={{
        position: 'absolute',
        right: 10,
        top: '50%',
        transform: open ? 'translateY(-50%)' : 'translateY(-50%) translateX(105%)',
        zIndex: 40,
        width: 320,
        maxHeight: 'calc(100vh - 120px)',
        background: 'rgba(252,253,254,0.96)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.95)',
        boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
        borderRadius: 16,
        overflow: 'hidden',
        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        pointerEvents: open ? 'auto' : 'none',
        opacity: open ? 1 : 0,
      }}
    >
      {/* Header */}
      <div className='flex items-center justify-between px-4 py-3 border-b border-slate-200/60 bg-white/40'>
        <div className='flex items-center gap-2'>
          <div className='w-6 h-6 rounded-lg bg-violet-100 flex items-center justify-center'>
            <svg width='14' height='14' viewBox='0 0 14 14' fill='none'>
              <circle cx='7' cy='7' r='5.5' stroke='#7c3aed' strokeWidth='1.2'/>
              <circle cx='7' cy='7' r='2' stroke='#7c3aed' strokeWidth='1.2'/>
              <path d='M7 1.5v2M7 10.5v2M1.5 7h2M10.5 7h2' stroke='#7c3aed' strokeWidth='1.1' strokeLinecap='round'/>
            </svg>
          </div>
          <div>
            <div className='text-xs font-semibold text-slate-800'>Agent 环境感知</div>
            <div className='text-[10px] text-slate-400'>实时地图状态</div>
          </div>
        </div>
        <button
          onClick={onClose}
          className='w-6 h-6 flex items-center justify-center rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600'
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className='overflow-y-auto max-h-[calc(100vh-180px)]'>
        {/* Current viewport state */}
        <div className='p-4 border-b border-slate-200/60'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3'>
            视口状态
          </div>
          <div className='grid grid-cols-2 gap-2'>
            <div className='bg-slate-100/60 rounded-lg p-2'>
              <div className='text-[9px] text-slate-400 uppercase tracking-wide mb-0.5'>经纬度</div>
              <div className='text-[11px] text-slate-700 font-mono'>
                {viewport.center[0].toFixed(5)}, {viewport.center[1].toFixed(5)}
              </div>
            </div>
            <div className='bg-slate-100/60 rounded-lg p-2'>
              <div className='text-[9px] text-slate-400 uppercase tracking-wide mb-0.5'>缩放</div>
              <div className='text-[11px] text-slate-700 font-mono'>{viewport.zoom.toFixed(1)}</div>
            </div>
            <div className='bg-slate-100/60 rounded-lg p-2'>
              <div className='text-[9px] text-slate-400 uppercase tracking-wide mb-0.5'>底图</div>
              <div className='text-[11px] text-slate-700 font-mono'>{baseLayer}</div>
            </div>
            <div className='bg-slate-100/60 rounded-lg p-2'>
              <div className='text-[9px] text-slate-400 uppercase tracking-wide mb-0.5'>模式</div>
              <div className='text-[11px] text-slate-700 font-mono'>{is3D ? '3D' : '2D'}</div>
            </div>
          </div>
        </div>

        {/* Layers summary */}
        <div className='p-4 border-b border-slate-200/60'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3'>
            图层 ({layers.length})
          </div>
          <div className='space-y-1'>
            {layers.slice(0, 5).map((layer) => (
              <div key={layer.id} className='flex items-center gap-2 text-xs'>
                <div
                  className='w-2 h-2 rounded-full'
                  style={{ backgroundColor: layer.visible ? ((layer as any).color || layer.style?.color || '#16a34a') : '#cbd5e1', opacity: layer.visible ? 1 : 0.3 }}
                />
                <span className={layer.visible ? 'text-slate-700' : 'text-slate-400'}>
                  {layer.name}
                </span>
              </div>
            ))}
            {layers.length > 5 && (
              <div className='text-xs text-slate-400'>
                还有 {layers.length - 5} 个图层...
              </div>
            )}
          </div>
        </div>

        {/* Causal trace */}
        <div className='p-4'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3'>
            因果链
          </div>
          <CausalTrace />
        </div>
      </div>
    </div>
  );
}

export default AgentEnvHud;