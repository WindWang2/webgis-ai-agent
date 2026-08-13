'use client';

import { useState, useEffect } from 'react';
import { 
  Cpu, Activity, Compass, Layers, Terminal, Sun, Moon, 
  ChevronUp, ChevronDown, Database, CheckCircle2
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';

const BASE_LAYER_LABELS: Record<string, string> = {
  osm: 'OpenStreetMap',
  amap: '高德地图',
  tianditu: '天地图',
  satellite: '卫星影像',
  dark: '暗色底图',
};

interface Step {
  label: string;
  sub: string;
}

const STEPS: Step[] = [
  { label: '感知', sub: '分析指令意图' },
  { label: '推理执行', sub: '调用空间工具' },
  { label: '渲染画布', sub: '挂载图层结果' }
];

type StepState = 'pending' | 'active' | 'done';

function getStepState(index: number, aiStatus: string): StepState {
  if (aiStatus === 'error') {
    return index === 0 ? 'active' : 'pending';
  }
  if (aiStatus === 'thinking') {
    return index === 0 ? 'active' : 'pending';
  }
  if (aiStatus === 'acting') {
    if (index === 0) return 'done';
    if (index === 1) return 'active';
    return 'pending';
  }
  if (aiStatus === 'done') {
    return 'done';
  }
  return 'pending';
}

export function EmbodiedHud() {
  const hudOpen = useHudStore((s) => s.hudOpen);
  const setHudOpen = useHudStore((s) => s.setHudOpen);
  const viewport = useHudStore((s) => s.viewport);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const layers = useHudStore((s) => s.layers);
  const theme = useHudStore((s) => s.theme);
  const setTheme = useHudStore((s) => s.setTheme);
  const aiStatus = useHudStore((s) => s.aiStatus);
  const is3D = useHudStore((s) => s.is3D);
  const opsLog = useHudStore((s) => s.opsLog) || [];
  const causalChain = useHudStore((s) => s.causalChain) || [];
  const ragResults = useHudStore((s) => s.ragResults) || [];

  const isDark = theme === 'dark';

  const lng = viewport.center[0];
  const lat = viewport.center[1];
  const zoom = viewport.zoom;
  const bearing = viewport.bearing ?? 0;
  const pitch = viewport.pitch ?? 0;
  const visibleLayerCount = layers.filter(l => l.visible).length;

  const isThinking = aiStatus === 'thinking' || aiStatus === 'acting';

  // Toggle Theme
  const handleToggleTheme = () => {
    setTheme(isDark ? 'light' : 'dark');
  };

  // Waveform phase animation
  const [phase, setPhase] = useState(0);
  const reducedMotion = usePrefersReducedMotion();
  useEffect(() => {
    // 审计 findings.md Perf Medium：HUD 关闭时（仅显示 24px 条）无需动画，
    // 暂停 rAF 循环避免空转的 60fps 状态更新 + 重渲染。
    // UI V3：prefers-reduced-motion 时同样不启动。
    if (!hudOpen || reducedMotion) return;
    let frame: number;
    const tick = () => {
      setPhase((p) => (p + (isThinking ? 0.25 : 0.08)) % (Math.PI * 2));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isThinking, hudOpen, reducedMotion]);

  // Render CPU Cognitive Waveform
  const renderWaveform = () => {
    const width = 105;
    const height = 35;
    const points: string[] = [];
    const amplitude = isThinking ? 12 : 3;
    const frequency = isThinking ? 0.08 : 0.04;

    for (let x = 0; x <= width; x += 4) {
      const y = height / 2 + Math.sin(x * frequency + phase) * amplitude;
      points.push(`${x},${y}`);
    }
    return `M ${points.join(' L ')}`;
  };

  return (
    /* V4（B）：整条状态栏改不透明 surface-panel —— 它压在持续重绘的地图画布上，
       backdrop-filter 是最贵的那类合成，半透明还会让地图细节透进正文。字体覆盖
       (Inter) 去掉，改继承全站 DM Sans。静态样式全部落 class，只剩高度/过渡等
       动态值留在 inline。 */
    <div
      className="fixed bottom-0 left-0 right-0 z-50 flex flex-col overflow-hidden border-t border-edge-subtle bg-surface-panel text-ink shadow-overlay"
      style={{
        height: hudOpen ? 210 : 24,
        transition: 'height 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <style jsx>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: transparent;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: var(--border-strong);
          border-radius: 99px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: var(--text-disabled);
        }
      `}</style>

      {/* DOCKED HEADER (Thin Telemetry Stripe)
          整条鼠标点击可开合；键盘等效操作在右侧 chevron 按钮上
          （避免 button-in-button 的嵌套交互违规）。 */}
      <div
        onClick={() => setHudOpen(!hudOpen)}
        className="flex cursor-pointer select-none items-center px-3"
        style={{
          height: 24,
          minHeight: 24,
          borderBottom: hudOpen ? '1px solid var(--border-subtle)' : 'none',
        }}
      >
        {/* Telemetry Stats */}
        <div className="flex items-center gap-4">
          {[
            { label: 'CRS', value: 'EPSG:4326' },
            { label: 'LNG', value: lng.toFixed(5) },
            { label: 'LAT', value: lat.toFixed(5) },
            { label: 'ZOOM', value: zoom.toFixed(1) },
            { label: '底图', value: BASE_LAYER_LABELS[baseLayer] ?? baseLayer },
            { label: '图层', value: `${visibleLayerCount}/${layers.length}` }
          ].map((item) => (
            <div key={item.label} className="flex items-center gap-1">
              <span className="eyebrow">{item.label}</span>
              <span className="font-mono text-meta text-ink-secondary">
                {item.value}
              </span>
            </div>
          ))}
        </div>

        {/* Neural Wave representation in Docked State */}
        {!hudOpen && (
          <div className="flex h-full flex-1 items-center gap-2 overflow-hidden" style={{ marginLeft: 24 }}>
            <span className="text-caption text-ink-disabled">|</span>
            <Activity
              size={10}
              className={isThinking ? 'animate-pulse' : ''}
              style={{ color: isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)' }}
            />
            <span className="font-mono text-caption text-ink-muted" style={{ letterSpacing: '0.04em' }}>
              {isThinking ? 'AGENT NEURAL SIGNAL ACTIVE' : 'COGNITIVE CORE IDLE'}
            </span>
            <svg width="60" height="12" className="ml-1 opacity-40">
              <path
                d={`M 0,6 Q 15,${6 + Math.sin(phase) * (isThinking ? 5 : 1.5)} 30,6 T 60,6`}
                fill="none"
                stroke={isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)'}
                strokeWidth="1"
              />
            </svg>
          </div>
        )}

        <div className="flex-1" />

        {/* Right Buttons */}
        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {/* Theme Toggle */}
          <button
            type="button"
            onClick={handleToggleTheme}
            aria-label={isDark ? '切换到浅色主题' : '切换到深色主题'}
            title="切换主题"
            className={`flex cursor-pointer items-center justify-center border-none bg-transparent p-0 text-ink-muted transition-colors ${
              isDark ? 'hover:text-status-warning' : 'hover:text-status-info'
            }`}
          >
            {isDark ? <Sun size={12} /> : <Moon size={12} />}
          </button>

          <span className="text-caption text-ink-disabled">|</span>

          {/* Expand/Collapse Chevron — HUD 开合的键盘可达控制 */}
          <button
            type="button"
            onClick={() => setHudOpen(!hudOpen)}
            aria-expanded={hudOpen}
            aria-label={hudOpen ? '收起状态栏' : '展开状态栏'}
            className="flex cursor-pointer items-center justify-center border-none bg-transparent p-0.5 text-ink-muted transition-colors hover:text-ink"
          >
            {hudOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        </div>
      </div>

      {/* EXPANDED TELEMETRY BAY (3 columns) */}
      {hudOpen && (
        <div className="grid min-h-0 flex-1 grid-cols-[1fr_1.1fr_1.2fr] gap-4 px-4 py-3 text-body">
          {/* COLUMN 1: SENSORY PERCEPTION (感知系统) */}
          <div className="flex min-h-0 flex-col gap-2 border-r border-edge-subtle pr-3">
            <div className="flex items-center gap-1.5 font-semibold text-ink-secondary" style={{ letterSpacing: '0.04em' }}>
              <Compass size={13} style={{ color: isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)' }} />
              <span>感知系统 / SENSORY PERCEPTION</span>
            </div>

            <div className="flex min-h-0 flex-1 items-center gap-3">
              {/* Sonar Vector Radar */}
              <div className="relative shrink-0" style={{ width: 70, height: 70 }}>
                <svg width="70" height="70" viewBox="0 0 100 100" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="50" cy="50" r="45" fill="none" stroke="var(--border-subtle)" strokeWidth="1" />
                  <circle cx="50" cy="50" r="30" fill="none" stroke="var(--border-subtle)" strokeWidth="1" />
                  <circle cx="50" cy="50" r="15" fill="none" stroke="var(--border-subtle)" strokeWidth="1" />
                  <line x1="50" y1="5" x2="50" y2="95" stroke="var(--border-subtle)" strokeWidth="0.75" />
                  <line x1="5" y1="50" x2="95" y2="50" stroke="var(--border-subtle)" strokeWidth="0.75" />
                  {/* Radar sweep */}
                  <path
                    d="M 50,50 L 50,5 A 45,45 0 0,1 81.8,18.1 Z"
                    style={{
                      fill: isThinking ? 'color-mix(in srgb, var(--agent-accent) 8%, transparent)' : 'transparent',
                      transformOrigin: '50px 50px',
                      animation: isThinking ? 'spin-clockwise 3s linear infinite' : 'none'
                    }}
                  />
                  {/* Pulse active marker */}
                  <circle cx="50" cy="20" r="2.5" fill={isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)'} style={{ opacity: isThinking ? 0.8 : 0.2 }} />
                </svg>
              </div>

              {/* Detailed perception reads */}
              <div className="flex flex-1 flex-col gap-3 font-mono text-meta">
                <div className="flex justify-between">
                  <span className="text-ink-muted">CENTER:</span>
                  <span className="text-ink-secondary">[{lng.toFixed(4)}, {lat.toFixed(4)}]</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-muted">BEARING/PITCH:</span>
                  <span className="text-ink-secondary">{bearing.toFixed(0)}° / {pitch.toFixed(0)}°</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-muted">DIMENSION:</span>
                  <span className="text-ink-secondary">{is3D ? '3D TERRAIN (1.5x)' : '2D PERSPECTIVE'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-muted">BASEMAP:</span>
                  <span className="max-w-[95px] truncate text-ink-secondary">
                    {BASE_LAYER_LABELS[baseLayer] ?? baseLayer}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* COLUMN 2: COGNITIVE CORE (认知中枢) */}
          <div className="flex min-h-0 flex-col gap-2 border-r border-edge-subtle pr-3">
            <div className="flex items-center gap-1.5 font-semibold text-ink-secondary" style={{ letterSpacing: '0.04em' }}>
              <Cpu size={13} style={{ color: isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)' }} />
              <span>认知中枢 / COGNITIVE CORE</span>
            </div>

            {/* AI Status Indicators */}
            <div className="flex min-h-0 flex-1 flex-col gap-1.5">
              <div className="flex items-center gap-2.5">
                {/* Status indicator */}
                <div
                  className="flex items-center gap-1.5 rounded-md px-2 py-0.5 text-caption font-semibold"
                  style={{
                    background: isThinking
                      ? 'color-mix(in srgb, var(--agent-accent) 10%, transparent)'
                      : 'var(--surface-hover)',
                    color: isThinking ? 'var(--agent-accent)' : 'var(--text-secondary)',
                    border: isThinking
                      ? '1px solid color-mix(in srgb, var(--agent-accent) 20%, transparent)'
                      : '1px solid transparent'
                  }}
                >
                  {isThinking ? (
                    <>
                      <span className="h-1.5 w-1.5 animate-ping rounded-full" style={{ backgroundColor: 'var(--agent-accent)' }} />
                      <span>{aiStatus === 'thinking' ? '感知中' : '执行中'}</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 size={10} />
                      <span>认知就绪</span>
                    </>
                  )}
                </div>

                {/* Cognitive Active Tools */}
                {isThinking && opsLog.length > 0 && (
                  <span
                    className="animate-pulse font-mono text-caption font-medium uppercase text-agent-accent"
                    style={{ letterSpacing: '0.04em' }}
                  >
                    RUNNING: {opsLog[0].type}
                  </span>
                )}
              </div>

              {/* Dynamic Waveform Graph & Memory count */}
              <div className="flex flex-1 items-center gap-3">
                <svg width="105" height="35" className="shrink-0 overflow-visible" style={{ filter: isThinking ? 'drop-shadow(0 0 2px color-mix(in srgb, var(--agent-accent) 53%, transparent))' : 'none' }}>
                  <path d={renderWaveform()} fill="none" stroke={isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)'} strokeWidth="1.5" />
                </svg>

                <div className="flex flex-col gap-3 font-mono text-caption">
                  <div className="flex items-center gap-1">
                    <Database size={10} className="text-ink-disabled" />
                    <span className="text-ink-muted">RAG MEM:</span>
                    <span className="text-ink-secondary">{ragResults.length} SLOTS</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Layers size={10} className="text-ink-disabled" />
                    <span className="text-ink-muted">SPATIAL REF:</span>
                    <span className="text-ink-secondary">{layers.length} ACTIVE</span>
                  </div>
                </div>
              </div>

              {/* 3-Step AI Stepper */}
              <div className="mt-auto flex w-full items-center justify-between border-t border-dashed border-edge-subtle pt-2">
                {STEPS.map((step, i) => {
                  const state = getStepState(i, aiStatus);
                  const isLast = i === STEPS.length - 1;

                  // Color computation
                  let dotColor = 'var(--text-disabled)';
                  let textColor = 'var(--text-muted)';
                  let glowStyle = {};

                  if (state === 'done') {
                    dotColor = 'var(--success)';
                    textColor = 'var(--text-primary)';
                  } else if (state === 'active') {
                    dotColor = 'var(--agent-accent)';
                    // accent 作文字在暗色下只有 2.96–3.40:1 —— 步骤名是正文，
                    // 用 text-safe 派生；发光/圆点是 mark，仍用原色。
                    textColor = 'var(--agent-accent)';
                    glowStyle = {
                      boxShadow: '0 0 8px var(--agent-accent)',
                      animation: 'pulse 1.5s infinite'
                    };
                  }

                  return (
                    <div key={step.label} className="flex min-w-0 items-center" style={{ flex: isLast ? '0 0 auto' : '1 1 auto' }}>
                      {/* Step item */}
                      <div className="flex min-w-0 items-center gap-1.5">
                        {/* Glowing dot */}
                        <div
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{
                            backgroundColor: dotColor,
                            transition: 'all 0.3s ease',
                            ...glowStyle
                          }}
                        />

                        {/* Label & Subtext */}
                        <div className="flex min-w-0 flex-col">
                          <span
                            className="whitespace-nowrap font-mono text-caption font-semibold"
                            style={{
                              color: textColor,
                              transition: 'color 0.3s ease'
                            }}
                          >{step.label}</span>
                          <span className="truncate whitespace-nowrap text-micro text-ink-muted">{step.sub}</span>
                        </div>
                      </div>

                      {/* Horizontal Connector Track */}
                      {!isLast && (
                        <div
                          className="mx-1.5 h-px min-w-2 flex-1"
                          style={{
                            background: state === 'done'
                              ? `linear-gradient(to right, var(--success), ${getStepState(i+1, aiStatus) === 'active' ? 'var(--agent-accent)' : 'var(--text-disabled)'})`
                              : 'var(--border-subtle)',
                            transition: 'background 0.3s ease'
                          }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* COLUMN 3: ACTION STREAM (执行通道) */}
          <div className="flex min-h-0 flex-col gap-2">
            <div className="flex items-center gap-1.5 font-semibold text-ink-secondary" style={{ letterSpacing: '0.04em' }}>
              <Terminal size={13} style={{ color: isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)' }} />
              <span>执行通道 / ACTION LOG & CAUSAL CHAIN</span>
            </div>

            {/* scrolling log window */}
            <div
              className="custom-scrollbar flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto rounded-md border border-edge-subtle bg-surface-sunken p-1.5 px-2 font-mono text-caption"
            >
              {/* Combine opsLog and causalChain */}
              {causalChain.length === 0 && opsLog.length === 0 ? (
                <div className="m-auto text-center italic text-ink-muted">
                  AWAITING SPATIAL AI COMMANDS...
                </div>
              ) : (
                <>
                  {causalChain.map((entry) => (
                    <div
                      key={entry.id}
                      className="mb-0.5 flex flex-col gap-0.5 pl-1.5"
                      style={{ borderLeft: '1.5px solid color-mix(in srgb, var(--agent-accent) 73%, transparent)' }}
                    >
                      <div className="flex justify-between font-semibold text-agent-accent">
                        <span>[CAUSAL] {entry.tool}</span>
                        <span className="text-micro text-ink-muted">{entry.time}</span>
                      </div>
                      {entry.toolInput && <div className="text-ink-secondary">IN: {entry.toolInput}</div>}
                      {entry.mapEffect && <div className="text-ink-muted">OUT: {entry.mapEffect}</div>}
                    </div>
                  ))}

                  {opsLog.map((entry) => (
                    <div
                      key={entry.id}
                      className="mb-0.5 flex flex-col gap-0.5 pl-1.5"
                      style={{ borderLeft: '1.5px solid var(--border-strong)' }}
                    >
                      <div className="flex justify-between font-medium text-ink-secondary">
                        <span>[OP] {entry.label}</span>
                        <span className="text-micro text-ink-muted">{entry.time}</span>
                      </div>
                      <div className="text-ink-muted">{entry.detail}</div>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default EmbodiedHud;
