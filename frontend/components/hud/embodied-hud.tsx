'use client';

import { useState, useEffect, useRef } from 'react';
import { 
  Cpu, Activity, Compass, Layers, Terminal, Sun, Moon, 
  ChevronUp, ChevronDown, Database, Play, CheckCircle2, AlertOctagon
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';

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
  const accentColor = useHudStore((s) => s.accentColor);
  const is3D = useHudStore((s) => s.is3D);
  const opsLog = useHudStore((s) => s.opsLog) || [];
  const causalChain = useHudStore((s) => s.causalChain) || [];
  const ragResults = useHudStore((s) => s.ragResults) || [];

  const colors = getThemeColors(theme);
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
  useEffect(() => {
    let frame: number;
    const tick = () => {
      setPhase((p) => (p + (isThinking ? 0.25 : 0.08)) % (Math.PI * 2));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isThinking]);

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
    <div
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 50,
        height: hudOpen ? 210 : 24,
        background: isDark ? 'rgba(15, 23, 42, 0.82)' : 'rgba(252, 253, 254, 0.82)',
        backdropFilter: 'blur(28px)',
        WebkitBackdropFilter: 'blur(28px)',
        borderTop: isDark ? '1px solid rgba(148, 163, 184, 0.12)' : '1px solid rgba(15, 23, 42, 0.06)',
        boxShadow: isDark ? '0 -8px 32px rgba(0,0,0,0.5)' : '0 -4px 20px rgba(15,23,42,0.06)',
        color: colors.text,
        transition: 'height 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        fontFamily: "'Inter', sans-serif"
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
          background: rgba(148, 163, 184, 0.2);
          border-radius: 99px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(148, 163, 184, 0.4);
        }
      `}</style>

      {/* DOCKED HEADER (Thin Telemetry Stripe) */}
      <div
        onClick={() => setHudOpen(!hudOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          height: 24,
          minHeight: 24,
          paddingLeft: 12,
          paddingRight: 12,
          cursor: 'pointer',
          userSelect: 'none',
          borderBottom: hudOpen ? (isDark ? '1px solid rgba(148, 163, 184, 0.08)' : '1px solid rgba(15,23,42,0.04)') : 'none',
        }}
      >
        {/* Telemetry Stats */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {[
            { label: 'CRS', value: 'EPSG:4326' },
            { label: 'LNG', value: lng.toFixed(5) },
            { label: 'LAT', value: lat.toFixed(5) },
            { label: 'ZOOM', value: zoom.toFixed(1) },
            { label: '底图', value: BASE_LAYER_LABELS[baseLayer] ?? baseLayer },
            { label: '图层', value: `${visibleLayerCount}/${layers.length}` }
          ].map((item) => (
            <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: isDark ? '#475569' : '#94a3b8', letterSpacing: '0.06em' }}>
                {item.label}
              </span>
              <span style={{ fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: isDark ? '#94a3b8' : '#64748b' }}>
                {item.value}
              </span>
            </div>
          ))}
        </div>

        {/* Neural Wave representation in Docked State */}
        {!hudOpen && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 24, flex: 1, height: '100%', overflow: 'hidden' }}>
            <span style={{ fontSize: 11, color: isDark ? '#334155' : '#cbd5e1' }}>|</span>
            <Activity size={10} style={{ color: isThinking ? accentColor : '#475569', animation: isThinking ? 'pulse 1s infinite' : 'none' }} />
            <span style={{ fontSize: 11, color: isDark ? '#475569' : '#94a3b8', fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.04em' }}>
              {isThinking ? 'AGENT NEURAL SIGNAL ACTIVE' : 'COGNITIVE CORE IDLE'}
            </span>
            <svg width="60" height="12" style={{ opacity: 0.4, marginLeft: 4 }}>
              <path
                d={`M 0,6 Q 15,${6 + Math.sin(phase) * (isThinking ? 5 : 1.5)} 30,6 T 60,6`}
                fill="none"
                stroke={isThinking ? accentColor : '#64748b'}
                strokeWidth="1"
              />
            </svg>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {/* Right Buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }} onClick={(e) => e.stopPropagation()}>
          {/* Theme Toggle */}
          <button
            onClick={handleToggleTheme}
            style={{
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              color: isDark ? '#64748b' : '#94a3b8',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title="切换主题"
          >
            {isDark ? <Sun size={12} className="hover:text-amber-400 transition-colors" /> : <Moon size={12} className="hover:text-indigo-600 transition-colors" />}
          </button>

          <span style={{ fontSize: 11, color: isDark ? '#334155' : '#cbd5e1' }}>|</span>

          {/* Expand/Collapse Chevron */}
          <div 
            onClick={() => setHudOpen(!hudOpen)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: isDark ? '#64748b' : '#94a3b8',
              cursor: 'pointer'
            }}
          >
            {hudOpen ? <ChevronDown size={14} className="hover:text-slate-200 transition-colors" /> : <ChevronUp size={14} className="hover:text-slate-200 transition-colors" />}
          </div>
        </div>
      </div>

      {/* EXPANDED TELEMETRY BAY (3 columns) */}
      {hudOpen && (
        <div 
          style={{
            flex: 1,
            display: 'grid',
            gridTemplateColumns: '1fr 1.1fr 1.2fr',
            gap: 16,
            padding: '12px 16px',
            fontSize: '13px',
            minHeight: 0
          }}
        >
          {/* COLUMN 1: SENSORY PERCEPTION (感知系统) */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            borderRight: isDark ? '1px solid rgba(148, 163, 184, 0.08)' : '1px solid rgba(15,23,42,0.04)',
            paddingRight: 12
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: isDark ? '#94a3b8' : '#475569', fontWeight: 600, letterSpacing: '0.04em' }}>
              <Compass size={13} style={{ color: isThinking ? accentColor : '#64748b' }} />
              <span>感知系统 / SENSORY PERCEPTION</span>
            </div>

            <div style={{ flex: 1, display: 'flex', gap: 12, alignItems: 'center', minHeight: 0 }}>
              {/* Sonar Vector Radar */}
              <div style={{ position: 'relative', width: 70, height: 70, flexShrink: 0 }}>
                <svg width="70" height="70" viewBox="0 0 100 100" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="50" cy="50" r="45" fill="none" stroke={isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(0,0,0,0.05)'} strokeWidth="1" />
                  <circle cx="50" cy="50" r="30" fill="none" stroke={isDark ? 'rgba(148, 163, 184, 0.06)' : 'rgba(0,0,0,0.03)'} strokeWidth="1" />
                  <circle cx="50" cy="50" r="15" fill="none" stroke={isDark ? 'rgba(148, 163, 184, 0.06)' : 'rgba(0,0,0,0.03)'} strokeWidth="1" />
                  <line x1="50" y1="5" x2="50" y2="95" stroke={isDark ? 'rgba(148, 163, 184, 0.06)' : 'rgba(0,0,0,0.03)'} strokeWidth="0.75" />
                  <line x1="5" y1="50" x2="95" y2="50" stroke={isDark ? 'rgba(148, 163, 184, 0.06)' : 'rgba(0,0,0,0.03)'} strokeWidth="0.75" />
                  {/* Radar sweep */}
                  <path
                    d="M 50,50 L 50,5 A 45,45 0 0,1 81.8,18.1 Z"
                    fill={`linear-gradient(45deg, transparent, ${accentColor}15)`}
                    style={{
                      fill: isThinking ? `${accentColor}15` : 'transparent',
                      transformOrigin: '50px 50px',
                      animation: isThinking ? 'spin-clockwise 3s linear infinite' : 'none'
                    }}
                  />
                  {/* Pulse active marker */}
                  <circle cx="50" cy="20" r="2.5" fill={isThinking ? accentColor : '#64748b'} style={{ opacity: isThinking ? 0.8 : 0.2 }} />
                </svg>
              </div>

              {/* Detailed perception reads */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 3, fontFamily: "'JetBrains Mono', monospace", fontSize: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>CENTER:</span>
                  <span style={{ color: colors.textSecondary }}>[{lng.toFixed(4)}, {lat.toFixed(4)}]</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>BEARING/PITCH:</span>
                  <span style={{ color: colors.textSecondary }}>{bearing.toFixed(0)}° / {pitch.toFixed(0)}°</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>DIMENSION:</span>
                  <span style={{ color: colors.textSecondary }}>{is3D ? '3D TERRAIN (1.5x)' : '2D PERSPECTIVE'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>BASEMAP:</span>
                  <span style={{ color: colors.textSecondary, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 95 }}>
                    {BASE_LAYER_LABELS[baseLayer] ?? baseLayer}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* COLUMN 2: COGNITIVE CORE (认知中枢) */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            borderRight: isDark ? '1px solid rgba(148, 163, 184, 0.08)' : '1px solid rgba(15,23,42,0.04)',
            paddingRight: 12
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: isDark ? '#94a3b8' : '#475569', fontWeight: 600, letterSpacing: '0.04em' }}>
              <Cpu size={13} style={{ color: isThinking ? accentColor : '#64748b' }} />
              <span>认知中枢 / COGNITIVE CORE</span>
            </div>

            {/* AI Status Indicators */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1, justifyItems: 'center', minHeight: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {/* Status indicator */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  padding: '2px 8px',
                  borderRadius: 6,
                  background: isThinking ? `${accentColor}18` : (isDark ? 'rgba(51, 65, 85, 0.4)' : 'rgba(0,0,0,0.03)'),
                  fontSize: '11.5px',
                  fontWeight: 600,
                  color: isThinking ? accentColor : (isDark ? '#94a3b8' : '#64748b'),
                  border: isThinking ? `1px solid ${accentColor}33` : '1px solid transparent'
                }}>
                  {isThinking ? (
                    <>
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" style={{ backgroundColor: accentColor }} />
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
                  <span style={{
                    fontSize: '11.5px',
                    fontFamily: "'JetBrains Mono', monospace",
                    color: accentColor,
                    letterSpacing: '0.04em',
                    fontWeight: 500,
                    textTransform: 'uppercase',
                    animation: 'pulse 1.5s infinite'
                  }}>
                    RUNNING: {opsLog[0].type}
                  </span>
                )}
              </div>

              {/* Dynamic Waveform Graph & Memory count */}
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flex: 1 }}>
                <svg width="105" height="35" style={{ flexShrink: 0, overflow: 'visible', filter: isThinking ? `drop-shadow(0 0 2px ${accentColor}88)` : 'none' }}>
                  <path d={renderWaveform()} fill="none" stroke={isThinking ? accentColor : '#475569'} strokeWidth="1.5" />
                </svg>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: '11px', fontFamily: "'JetBrains Mono', monospace" }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Database size={10} style={{ color: '#94a3b8' }} />
                    <span style={{ color: '#64748b' }}>RAG MEM:</span>
                    <span style={{ color: colors.textSecondary }}>{ragResults.length} SLOTS</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Layers size={10} style={{ color: '#94a3b8' }} />
                    <span style={{ color: '#64748b' }}>SPATIAL REF:</span>
                    <span style={{ color: colors.textSecondary }}>{layers.length} ACTIVE</span>
                  </div>
                </div>
              </div>

              {/* 3-Step AI Stepper */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginTop: 'auto',
                paddingTop: 8,
                borderTop: isDark ? '1px dashed rgba(148, 163, 184, 0.08)' : '1px dashed rgba(15, 23, 42, 0.04)',
                width: '100%'
              }}>
                {STEPS.map((step, i) => {
                  const state = getStepState(i, aiStatus);
                  const isLast = i === STEPS.length - 1;
                  
                  // Color computation
                  let dotColor = '#64748b';
                  let textColor = '#64748b';
                  let glowStyle = {};
                  
                  if (state === 'done') {
                    dotColor = '#10b981';
                    textColor = isDark ? '#cbd5e1' : '#1e293b';
                  } else if (state === 'active') {
                    dotColor = accentColor;
                    textColor = accentColor;
                    glowStyle = {
                      boxShadow: `0 0 8px ${accentColor}`,
                      animation: 'pulse 1.5s infinite'
                    };
                  } else {
                    dotColor = isDark ? '#334155' : '#cbd5e1';
                    textColor = isDark ? '#475569' : '#94a3b8';
                  }

                  return (
                    <div key={step.label} style={{ display: 'flex', alignItems: 'center', flex: isLast ? '0 0 auto' : '1 1 auto', minWidth: 0 }}>
                      {/* Step item */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                        {/* Glowing dot */}
                        <div style={{
                          width: 6,
                          height: 6,
                          borderRadius: '50%',
                          backgroundColor: dotColor,
                          flexShrink: 0,
                          transition: 'all 0.3s ease',
                          ...glowStyle
                        }} />
                        
                        {/* Label & Subtext */}
                        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                          <span style={{
                            fontSize: '11px',
                            fontWeight: 600,
                            fontFamily: "'JetBrains Mono', monospace",
                            color: textColor,
                            transition: 'color 0.3s ease',
                            whiteSpace: 'nowrap'
                          }}>{step.label}</span>
                          <span style={{
                            fontSize: '9.5px',
                            color: isDark ? '#475569' : '#94a3b8',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis'
                          }}>{step.sub}</span>
                        </div>
                      </div>

                      {/* Horizontal Connector Track */}
                      {!isLast && (
                        <div style={{
                          flex: 1,
                          height: 1,
                          marginLeft: 6,
                          marginRight: 6,
                          background: state === 'done' 
                            ? `linear-gradient(to right, #10b981, ${getStepState(i+1, aiStatus) === 'active' ? accentColor : (isDark ? '#334155' : '#cbd5e1')})`
                            : (isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(0,0,0,0.05)'),
                          transition: 'background 0.3s ease',
                          minWidth: 8
                        }} />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* COLUMN 3: ACTION STREAM (执行通道) */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            minHeight: 0
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: isDark ? '#94a3b8' : '#475569', fontWeight: 600, letterSpacing: '0.04em' }}>
              <Terminal size={13} style={{ color: isThinking ? accentColor : '#64748b' }} />
              <span>执行通道 / ACTION LOG & CAUSAL CHAIN</span>
            </div>

            {/* scrolling log window */}
            <div 
              className="custom-scrollbar"
              style={{
                flex: 1,
                overflowY: 'auto',
                background: isDark ? 'rgba(9, 15, 30, 0.4)' : 'rgba(0, 0, 0, 0.02)',
                border: isDark ? '1px solid rgba(148,163,184,0.06)' : '1px solid rgba(15,23,42,0.04)',
                borderRadius: 8,
                padding: '6px 8px',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '11px',
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
                minHeight: 0
              }}
            >
              {/* Combine opsLog and causalChain */}
              {causalChain.length === 0 && opsLog.length === 0 ? (
                <div style={{ color: '#475569', fontStyle: 'italic', textAlign: 'center', margin: 'auto' }}>
                  AWAITING SPATIAL AI COMMANDS...
                </div>
              ) : (
                <>
                  {causalChain.map((entry) => (
                    <div key={entry.id} style={{ display: 'flex', flexDirection: 'column', gap: 1, borderLeft: `1.5px solid ${accentColor}bb`, paddingLeft: 6, marginBottom: 2 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: accentColor, fontWeight: 600 }}>
                        <span>[CAUSAL] {entry.tool}</span>
                        <span style={{ color: '#475569', fontSize: '10px' }}>{entry.time}</span>
                      </div>
                      {entry.toolInput && <div style={{ color: colors.textSecondary }}>IN: {entry.toolInput}</div>}
                      {entry.mapEffect && <div style={{ color: '#94a3b8' }}>OUT: {entry.mapEffect}</div>}
                    </div>
                  ))}

                  {opsLog.map((entry) => (
                    <div key={entry.id} style={{ display: 'flex', flexDirection: 'column', gap: 1, borderLeft: '1.5px solid #475569', paddingLeft: 6, marginBottom: 2 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: colors.textSecondary, fontWeight: 500 }}>
                        <span>[OP] {entry.label}</span>
                        <span style={{ color: '#475569', fontSize: '10px' }}>{entry.time}</span>
                      </div>
                      <div style={{ color: '#64748b' }}>{entry.detail}</div>
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
