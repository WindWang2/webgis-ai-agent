'use client';

import { useId, useRef } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useInertWhenClosed } from '@/lib/hooks/use-inert';
import ToggleSwitch from '@/components/shared/toggle-switch';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';

interface TweaksPanelProps {
  children?: React.ReactNode;
}

/**
 * 主题色预设。全部取"能承载白色文字"的一档 —— 之前的 #16a34a 与 #0891b2 配
 * 白字只有 3.30:1 / 3.68:1，而 accent 底 + 白字正是主按钮与用户气泡的用法，
 * 也就是说其中两个预设一旦被选中，主按钮的文字就不达 AA。
 */
const ACCENT_COLORS: { value: string; name: string }[] = [
  { value: '#15803d', name: '绿色' },
  { value: '#1d4ed8', name: '蓝色' },
  { value: '#6d28d9', name: '紫色' },
  { value: '#b91c1c', name: '红色' },
  { value: '#0e7490', name: '青色' },
];

/** 供测试断言：每个预设都必须能承载 --text-on-accent 的白色文字。 */
export const ACCENT_PRESETS = ACCENT_COLORS.map((c) => c.value);

/** 分段选择器 —— 主题 / 密度共用，替代两处各写一遍的内联按钮组。 */
function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex gap-0.5 rounded-sm bg-surface-sunken p-0.5">
      {options.map((opt) => {
        const selected = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt.value)}
            className={`flex-1 rounded-xs py-1 text-meta transition-colors ${
              selected
                ? 'bg-surface-raised font-medium text-ink shadow-raised'
                : 'text-ink-muted hover:text-ink'
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/** 带值读数的滑杆行。label 通过 htmlFor 绑定，之前两处滑杆都是无名控件。 */
function SliderRow({
  label,
  value,
  suffix,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  suffix: string;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const id = useId();
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label htmlFor={id} className="eyebrow">
          {label}
        </label>
        <span className="font-mono text-caption tabular-nums text-ink-secondary">
          {value}
          {suffix}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="h-1 w-full cursor-pointer appearance-none rounded-pill bg-edge-subtle"
      />
    </div>
  );
}

export function TweaksPanel({ children }: TweaksPanelProps) {
  const tweaksOpen = useHudStore((s) => s.tweaksOpen);
  const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
  const accentColor = useHudStore((s) => s.accentColor);
  const setAccentColor = useHudStore((s) => s.setAccentColor);
  const theme = useHudStore((s) => s.theme);
  const setTheme = useHudStore((s) => s.setTheme);
  const fontSize = useHudStore((s) => s.fontSize);
  const setFontSize = useHudStore((s) => s.setFontSize);
  const hudOpen = useHudStore((s) => s.hudOpen);
  const setHudOpen = useHudStore((s) => s.setHudOpen);
  const ragPanelOpen = useHudStore((s) => s.ragPanelOpen);
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);
  const setSidebarWidth = useHudStore((s) => s.setSidebarWidth);

  const panelRef = useRef<HTMLDivElement>(null);

  /*
    a11y：此前本面板常驻 DOM，仅靠 transform + opacity + pointerEvents 隐藏，
    既没有 role=dialog / 可访问名称，也没有 Escape，读屏在它视觉隐藏时依然能读到
    里面全部控件。现在补齐对话框语义并接入共享 useDialogFocus（与 settings /
    history / 模板库同一套焦点契约）。
    关闭时除了 aria-hidden 还要 inert：只写 aria-hidden 是 ARIA 违规 —— 容器里
    仍有可聚焦控件，键盘会 Tab 进一个看不见、也不会被播报的面板。
  */
  useInertWhenClosed(panelRef, tweaksOpen);

  useDialogFocus({
    open: tweaksOpen,
    containerRef: panelRef,
    onEscape: () => setTweaksOpen(false),
  });

  return (
    <>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="false"
        aria-labelledby="tweaks-panel-title"
        aria-hidden={!tweaksOpen}
        /* max-h + overflow-y：审计发现本面板既无高度上限也无内部滚动，
           视口一矮就整体溢出屏幕。 */
        className="fixed bottom-8 left-1/2 z-[100] max-h-[min(72vh,520px)] w-[300px] -translate-x-1/2 overflow-y-auto rounded-md border border-edge-subtle bg-surface-overlay p-panel shadow-drawer transition-transform duration-200"
        style={{
          transform: tweaksOpen ? 'translateX(-50%)' : 'translateX(-50%) translateY(105%)',
          opacity: tweaksOpen ? 1 : 0,
          pointerEvents: tweaksOpen ? 'auto' : 'none',
        }}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id="tweaks-panel-title" className="text-title font-semibold text-ink">
            UI 调整
          </h2>
          <button
            type="button"
            onClick={() => setTweaksOpen(false)}
            className="rounded-sm px-1.5 py-0.5 text-meta text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
          >
            关闭
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <div className="eyebrow mb-1.5">主题色</div>
            <div className="flex gap-1.5">
              {ACCENT_COLORS.map((color) => (
                <button
                  key={color.value}
                  type="button"
                  /* a11y：这五颗色板此前是完全空的 <button>，既无 aria-label
                     也无 title —— 读屏只会读到「按钮」。 */
                  aria-label={`主题色：${color.name}`}
                  title={color.name}
                  aria-pressed={accentColor === color.value}
                  onClick={() => setAccentColor(color.value)}
                  className={`h-control-sm w-control-sm rounded-sm border-2 transition-colors ${
                    accentColor === color.value ? 'border-ink' : 'border-transparent'
                  }`}
                  style={{ backgroundColor: color.value }}
                />
              ))}
            </div>
          </div>

          <SliderRow
            label="字体大小"
            value={fontSize}
            suffix="px"
            min={11}
            max={16}
            step={0.5}
            onChange={setFontSize}
          />

          <div>
            <div className="eyebrow mb-1.5">主题</div>
            <SegmentedControl
              label="主题"
              value={theme}
              options={[
                { value: 'light' as const, label: '亮色' },
                { value: 'dark' as const, label: '暗色' },
              ]}
              onChange={setTheme}
            />
          </div>

          <SliderRow
            label="侧边栏宽度"
            value={sidebarWidth}
            suffix="px"
            min={240}
            max={480}
            step={10}
            onChange={(v) => setSidebarWidth(Math.round(v))}
          />

          <div>
            <div className="eyebrow mb-1.5">面板</div>
            <div className="space-y-0.5">
              {/* 这三行原本是自绘的无名开关（无 role=switch / aria-checked，
                  label 只是旁边一个游离的 span）。改用共享 ToggleSwitch，
                  可访问名称由它的必填 prop 强制。 */}
              <ToggleRow label="Agent 环境 HUD" value={hudOpen} onChange={setHudOpen} />
              <ToggleRow label="RAG 独立面板" value={ragPanelOpen} onChange={setRagPanelOpen} />
            </div>
          </div>
        </div>
      </div>

      {children}
    </>
  );
}

function ToggleRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex min-h-row-sm items-center justify-between">
      <span className="text-meta text-ink-secondary">{label}</span>
      <ToggleSwitch label={label} checked={value} onChange={() => onChange(!value)} />
    </div>
  );
}

export default TweaksPanel;
