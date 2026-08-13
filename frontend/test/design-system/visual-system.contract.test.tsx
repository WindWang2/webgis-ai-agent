/**
 * Behaviour contracts the V4 visual pass introduced or repaired.
 *
 * These are deliberately about behaviour and semantics, not class strings — the
 * one exception is the token-driven metric assertions, which exist because the
 * whole point of `--row-*` / `--control-*` is that density is agreed once
 * instead of re-derived per component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { SField, STitle } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';
import { IconButton } from '@/components/shared/icon-button';
import { StatusBadge } from '@/components/shared/status-badge';
import { LegendCard, formatLegendValue } from '@/components/map/legends/legend-card';
import { ACCENT_PRESETS } from '@/components/tweaks-panel';
import { Eye } from 'lucide-react';

const ROOT = resolve(__dirname, '../..');
const read = (p: string) => readFileSync(resolve(ROOT, p), 'utf8');
/**
 * Source with comments stripped. Several assertions below check that a pattern
 * is *absent*; the explanatory comments in those same files quote the pattern
 * they removed, so a naive text search matches the explanation, not the code.
 */
const readCode = (p: string) =>
  read(p)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');

/* ────────────────────────────── a11y contracts ───────────────────────────── */

describe('form controls carry programmatic names', () => {
  it('SField associates its visible label with the input', () => {
    // Before V4 the label was a bare sibling with no htmlFor, so all eight
    // settings fields (LLM / RAG / map config) were unnamed to assistive tech.
    render(<SField label="Base URL" value="https://example.org" onChange={() => {}} />);
    const input = screen.getByLabelText('Base URL');
    expect(input.tagName).toBe('INPUT');
  });

  it('SField exposes its hint through aria-describedby', () => {
    render(<SField label="Model" value="gpt" onChange={() => {}} hint="留空则使用默认模型" />);
    const input = screen.getByLabelText('Model');
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe('留空则使用默认模型');
  });

  it('ToggleSwitch is a named switch that reports its state', () => {
    // `label` is a required prop precisely so an unnamed switch cannot compile.
    const onChange = vi.fn();
    render(<ToggleSwitch label="Prompt Caching" checked onChange={onChange} />);
    const sw = screen.getByRole('switch', { name: 'Prompt Caching' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });

  it('IconButton always has an accessible name and reports pressed state', () => {
    render(<IconButton label="显示图层" icon={Eye} active />);
    const btn = screen.getByRole('button', { name: '显示图层' });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(btn).toHaveAttribute('title', '显示图层');
  });

  it('IconButton renders a disabled state that is visually distinct', () => {
    // Audited gap: disabled and enabled icon buttons were pixel-identical.
    const { container } = render(<IconButton label="删除" icon={Eye} disabled />);
    const btn = container.querySelector('button')!;
    expect(btn).toBeDisabled();
    expect(btn.className).toContain('text-ink-disabled');
    expect(btn.className).toContain('cursor-not-allowed');
  });
});

describe('overlays announce themselves and honour Escape', () => {
  it('the tweaks panel is a named dialog that is hidden from AT when closed', async () => {
    // It is always mounted and only visually hidden, so without aria-hidden a
    // screen reader read its whole contents while it was invisible.
    const { useHudStore } = await import('@/lib/store/useHudStore');
    const { default: TweaksPanel } = await import('@/components/tweaks-panel');

    useHudStore.setState({ tweaksOpen: false });
    const { container } = render(<TweaksPanel />);
    const dialog = container.querySelector('[role="dialog"]')!;
    expect(dialog).toHaveAttribute('aria-hidden', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby');
    const titleId = dialog.getAttribute('aria-labelledby')!;
    expect(document.getElementById(titleId)?.textContent).toBe('UI 调整');
  });

  it('the tweaks panel colour swatches are named', async () => {
    const { useHudStore } = await import('@/lib/store/useHudStore');
    const { default: TweaksPanel } = await import('@/components/tweaks-panel');
    useHudStore.setState({ tweaksOpen: true });
    render(<TweaksPanel />);
    // Five empty <button>s with no name at all before V4.
    expect(screen.getByRole('button', { name: '主题色：绿色' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '主题色：青色' })).toBeInTheDocument();
  });

  it('the tweaks panel sliders are labelled', async () => {
    const { useHudStore } = await import('@/lib/store/useHudStore');
    const { default: TweaksPanel } = await import('@/components/tweaks-panel');
    useHudStore.setState({ tweaksOpen: true });
    render(<TweaksPanel />);
    expect(screen.getByLabelText('字体大小')).toBeInTheDocument();
    expect(screen.getByLabelText('侧边栏宽度')).toBeInTheDocument();
  });
});

describe('streaming output and operation feedback are announced', () => {
  it('announces the turn lifecycle without narrating every token batch', async () => {
    // Review finding: putting aria-live on the transcript itself re-reads the
    // whole accumulated answer on every batch, because each batch re-parses the
    // markdown and replaces the bubble's entire subtree — `aria-atomic=false`
    // only yields increments for appends to a leaf. So the announcer reports the
    // turn's STATE plus the finished reply once, and the list carries no live
    // region at all.
    const { ChatAnnouncer } = await import('@/components/chat/chat-announcer');
    const messages = [{ role: 'assistant' as const, content: '已生成 3 个等时圈' }];

    const { rerender } = render(<ChatAnnouncer messages={messages} aiStatus="thinking" />);
    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    // atomic: one whole sentence per announcement, not a growing increment.
    expect(region).toHaveAttribute('aria-atomic', 'true');
    expect(region).toHaveTextContent('正在分析指令');

    rerender(<ChatAnnouncer messages={messages} aiStatus="idle" />);
    expect(screen.getByRole('status')).toHaveTextContent('回复已完成：已生成 3 个等时圈');

    // And the transcript container itself must NOT be a live region.
    const src = readCode('components/sidebar/chat-tab.tsx');
    const list = src.slice(src.indexOf('messages.map') - 400, src.indexOf('messages.map'));
    expect(list).not.toMatch(/aria-live/);
  });

  it('the toast container is a polite status region', () => {
    const src = read('components/ui/toast.tsx');
    expect(src).toMatch(/role="status"/);
    expect(src).toMatch(/aria-live="polite"/);
  });

  it('does not leave undefined Tailwind utilities in the toast chrome', () => {
    // `bg-ds-surface` / `backdrop-blur-hud` / `text-hud-cyan` were never in the
    // config, so the toast rendered with no background at all.
    const src = readCode('components/ui/toast.tsx');
    for (const dead of ['bg-ds-surface', 'backdrop-blur-hud', 'text-hud-cyan']) {
      expect(src, `${dead} is not a defined utility`).not.toContain(dead);
    }
  });
});

describe('keyboard reachability of mouse-only affordances', () => {
  it('the composer keeps a keyboard focus ring', () => {
    // It used to carry an inline `outline: 'none'`, which beats the unlayered
    // global *:focus-visible rule — the one control in the app with no ring.
    const src = readCode('components/sidebar/chat-tab.tsx');
    expect(src).not.toMatch(/outline:\s*'none'/);
    expect(src).toMatch(/focus-visible:ring/);
  });

  it('layer reordering has a keyboard path on a named control', async () => {
    // Reordering was drag-only, i.e. the layer panel's core function was
    // unavailable without a mouse. Review finding: hanging the keys off the row
    // (role="group" + tabIndex) was a role misuse and added one dead tab stop
    // per layer, so the grip is a real button and owns the keys.
    const { useHudStore } = await import('@/lib/store/useHudStore');
    const { LayersTab } = await import('@/components/sidebar/layers-tab');

    const layer = (id: string, name: string) => ({
      id,
      name,
      type: 'geojson' as const,
      visible: true,
      opacity: 1,
      source: { type: 'FeatureCollection', features: [] },
    });
    useHudStore.setState({
      layers: [layer('a', '路网'), layer('b', 'POI')] as never,
    });

    render(<LayersTab />);
    const grips = screen.getAllByRole('button', { name: /重新排序/ });
    expect(grips).toHaveLength(2);
    // The name states position and the shortcut, so it is discoverable.
    expect(grips[0]).toHaveAccessibleName(/第 1 \/ 2 层/);

    grips[0].focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(useHudStore.getState().layers.map((l) => l.id)).toEqual(['b', 'a']);

    // The row itself must not be a tab stop.
    const src = readCode('components/sidebar/layers-tab.tsx');
    expect(src).not.toMatch(/role="group"/);
  });

  it('the map centre crosshair only captures pointer events when actionable', () => {
    // A permanently `pointerEvents: 'auto'` 24px target at dead centre swallowed
    // feature picks even while idle.
    const src = read('components/map/spatial-crosshair.tsx');
    expect(src).toMatch(/pointerEvents:\s*copied \|\| isThinking \? 'auto' : 'none'/);
  });

  it('framer-motion honours the reduced-motion preference', () => {
    // The global CSS reduced-motion kill-switch cannot reach JS-driven springs.
    const src = read('components/providers/client-providers.tsx');
    expect(src).toMatch(/MotionConfig/);
    expect(src).toMatch(/reducedMotion="user"/);
  });
});

describe('unique ids for aria relationships', () => {
  it('collapsible think panels do not share a constant id', () => {
    const src = readCode('components/chat/collapsible-think.tsx');
    expect(src).not.toContain("'think-content'");
    expect(src).toMatch(/useId\(\)/);
  });

  it('tool-call chain lists do not share a constant id', () => {
    const src = readCode('components/chat/tool-call-card.tsx');
    expect(src).not.toContain("'tool-call-chain-list'");
    expect(src).toMatch(/useId\(\)/);
  });
});

/* ─────────────────────────── visual-language contracts ───────────────────── */

describe('one status colour vocabulary', () => {
  it('renders in-progress as info and success as success', () => {
    // Audited conflict: `active` was blue in StatusBadge and green in the data
    // source card; "in progress" was blue for jobs and green for the agent.
    const { container: running } = render(<StatusBadge status="running" />);
    expect(running.firstElementChild!.className).toContain('status-info');

    const { container: done } = render(<StatusBadge status="completed" />);
    expect(done.firstElementChild!.className).toContain('status-success');

    const { container: failed } = render(<StatusBadge status="failed" />);
    expect(failed.firstElementChild!.className).toContain('status-critical');
  });

  it('renders result warning statuses in the warning tone, not neutral', () => {
    // Result Workbench audit P0: `partial` / `warning` results fell back to the
    // neutral slot and were indistinguishable from `unknown` — a result that
    // carries warnings must read as warning-tone like every other warning state.
    const { container: partial } = render(<StatusBadge status="partial" />);
    expect(partial.firstElementChild!.className).toContain('status-warning');

    const { container: warning } = render(<StatusBadge status="warning" />);
    expect(warning.firstElementChild!.className).toContain('status-warning');
  });

  it('treats a healthy data source as success, not as in-progress', () => {
    const src = read('components/sidebar/data-sources/source-item-card.tsx');
    // Both healthy and active mean "reachable" for a source; neither may borrow
    // the pulsing blue that means "something is running".
    expect(src).toMatch(/status === 'healthy' \|\| status === 'active'/);
    expect(src).toMatch(/status: 'ok'/);
  });

  it('does not paint a neutral count with the accent colour', () => {
    // `可见` was accent green, which reads as a status on a plain tally.
    const src = readCode('components/sidebar/layers-tab.tsx');
    const stats = src.slice(src.indexOf('总图层'), src.indexOf('要素'));
    expect(stats).not.toMatch(/accent/);
  });
});

describe('a single header hierarchy', () => {
  it('keeps a section subtitle smaller than its title', () => {
    // The shipped STitle had a 15px subtitle under a 14px title — inverted.
    render(<STitle title="模型" sub="选择推理模型" />);
    const title = screen.getByText('模型');
    const sub = screen.getByText('选择推理模型');
    expect(title.className).toContain('text-title');
    expect(sub.className).toContain('text-meta');
    // And the subtitle must not be re-promoted above the title step.
    expect(sub.className).not.toContain('text-title');
    expect(sub.className).not.toContain('text-heading');
  });

  it('uses the shared eyebrow treatment for micro labels', () => {
    // The audit found five different 10px uppercase treatments.
    for (const file of [
      'components/sidebar/layers-tab.tsx',
      'components/shared/section-title.tsx',
      'components/tweaks-panel.tsx',
    ]) {
      expect(read(file), `${file} should use .eyebrow`).toContain('eyebrow');
    }
  });
});

describe('map chrome is one container recipe', () => {
  it('routes every legend through the shared LegendCard', () => {
    for (const file of [
      'components/map/legends/categorical-legend.tsx',
      'components/map/legends/continuous-legend.tsx',
      'components/map/legends/graduated-legend.tsx',
    ]) {
      const src = read(file);
      expect(src, `${file} should use LegendCard`).toContain('LegendCard');
      // The container string used to be copy-pasted three times.
      expect(src).not.toContain('backdrop-blur-md');
    }
  });

  it('gives the legend stack the height budget, and each card scrolls inside it', () => {
    // Review finding: capping each card individually still let two legends
    // overflow the workspace at 1024x768 with the HUD open, clipping the top
    // card. The stack owns the budget (pinned top + bottom, scrolls); the card
    // just yields (min-h-0 + flex column + scrolling body).
    const card = readCode('components/map/legends/legend-card.tsx');
    expect(card).toMatch(/min-h-0/);
    expect(card).toMatch(/overflow-y-auto/);
    expect(card).not.toMatch(/max-h-\[min\(/);

    const stack = readCode('components/map/map-panel.tsx');
    const block = stack.slice(stack.indexOf('thematicLayers.length > 0'));
    expect(block).toMatch(/overflow-y-auto/);
    expect(block).toMatch(/--map-chrome-bottom/);
    expect(block).toMatch(/top:/);
  });

  it('formats legend values identically everywhere', () => {
    // continuous used 1 decimal, graduated used 0, for the same data.
    expect(formatLegendValue(0)).toBe('0');
    expect(formatLegendValue(1)).toBe('1');
    expect(formatLegendValue(1234)).toBe('1,234');
    expect(formatLegendValue(12_345)).toBe('12.3k');
    expect(formatLegendValue(2_500_000)).toBe('2.5M');
    expect(formatLegendValue(-1_500_000)).toBe('-1.5M');
    expect(formatLegendValue(0.5)).toBe('0.5');
    expect(formatLegendValue(Number.NaN)).toBe('—');
  });

  it('labels the legend renderer it is actually showing', () => {
    render(
      <LegendCard field="pop" kind="发散渐变渲染">
        <span>swatches</span>
      </LegendCard>,
    );
    expect(screen.getByText('发散渐变渲染')).toBeInTheDocument();
  });

  it('exposes a coordinate, zoom and CRS readout', () => {
    // Absent before V4: a desktop GIS always tells you where and how far in.
    const src = read('components/map/map-status-readout.tsx');
    expect(src).toContain('EPSG:4326');
    expect(src).toMatch(/toFixed\(4\)/);
    expect(src).toMatch(/Z\{/);
  });

  it('stacks bottom map chrome from one baseline variable', () => {
    // The heatmap legend and the thematic legend stack used to share an exact
    // left/bottom, so the higher-z card simply hid the other.
    const page = read('app/page.tsx');
    expect(page).toContain('--map-chrome-bottom');
    expect(read('components/map/map-decorations.tsx')).toContain('--map-chrome-bottom');
  });
});

describe('density is token-driven', () => {
  it('sizes the layer row from the row scale, not an ad-hoc padding', () => {
    // Rows were 59-63px because two 28px action buttons set the height while the
    // icons inside were 12px. QGIS-class layer trees run 22-24px.
    const src = read('components/sidebar/layers-tab.tsx');
    expect(src).toMatch(/min-h-row-md/);
    expect(src).toMatch(/size="sm"/);
  });

  it('gives every appearance-none range input a visible thumb', () => {
    // Review finding (verified in Chromium): `appearance-none` on the layer
    // opacity slider removed the native thumb and nothing restyled it, so the
    // control rendered as a bare 4px bar with no grabbable handle.
    const css = read('app/globals.css');
    expect(css).toMatch(/\.slider-track::-webkit-slider-thumb/);
    expect(css).toMatch(/\.slider-track::-moz-range-thumb/);

    const layers = readCode('components/sidebar/layers-tab.tsx');
    const range = layers.slice(layers.indexOf("type=\"range\""), layers.indexOf("type=\"range\"") + 600);
    expect(range).toContain('slider-track');
    // 36px over 100 steps is 0.36px per step; the track needs real width.
    expect(range).not.toMatch(/\bw-9\b/);
  });

  it('hides closed always-mounted panels from focus as well as from AT', () => {
    // Review finding: `aria-hidden` on a container that still holds focusable
    // children is an ARIA violation — keyboard users tab into invisible
    // controls that announce nothing. `inert` removes focus, hit-testing and
    // the a11y subtree together.
    for (const file of ['components/tweaks-panel.tsx', 'components/panel/rag-independent-panel.tsx']) {
      const src = readCode(file);
      expect(src, `${file} should use useInertWhenClosed`).toContain('useInertWhenClosed');
      expect(src).toMatch(/aria-hidden=\{!/);
    }
    expect(read('lib/hooks/use-inert.ts')).toMatch(/setAttribute\('inert'/);
  });

  it('keeps the control scale to three steps, none below the 24px target floor', () => {
    // WCAG 2.2 SC 2.5.8 wants a 24x24 minimum target; the smallest control step
    // sits inside a 30px row, so it cannot claim the spacing exemption.
    const css = read('app/globals.css');
    const steps = ['sm', 'md', 'lg'].map((step) => {
      const m = css.match(new RegExp(`--control-${step}:\\s*(\\d+)px`));
      expect(m, `--control-${step} is not defined`).not.toBeNull();
      return Number(m![1]);
    });
    expect(steps[0]).toBeGreaterThanOrEqual(24);
    expect(steps[0]).toBeLessThan(steps[1]);
    expect(steps[1]).toBeLessThan(steps[2]);
    expect(css).toMatch(/--row-sm:\s*24px/);
  });
});

describe('theme survives a reload', () => {
  it('persists the theme choice', () => {
    // Not persisted before V4: switching to dark and reloading silently
    // reverted to light.
    const src = read('lib/store/useHudStore.ts');
    const partialize = src.slice(src.indexOf('partialize'), src.indexOf('}),', src.indexOf('partialize')));
    expect(partialize).toMatch(/theme: state\.theme/);
    expect(partialize).toMatch(/accentColor: state\.accentColor/);
  });

  it('applies the persisted theme before the first paint', () => {
    const src = read('app/layout.tsx');
    expect(src).toMatch(/geoagent-settings/);
    expect(src).toMatch(/classList\.add\('dark'\)/);
  });

  it('syncs the runtime accent into the CSS custom property', () => {
    // `var(--agent-accent)` consumers (nav rail, panel separator) were stuck on
    // the default green no matter what accent the user picked. The effect writes
    // the raw accent; globals.css derives the theme-corrected --agent-accent.
    const src = read('app/page.tsx');
    expect(src).toMatch(/setProperty\('--agent-accent-raw'/);
  });
});

describe('every accent preset can carry label text', () => {
  const css = read('app/globals.css');
  const onAccent = css.match(/--text-on-accent:\s*([^;]+);/)![1].trim();

  const lum = (hex: string) => {
    const n = parseInt(hex.replace('#', ''), 16);
    const parts = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2];
  };
  const ratio = (a: string, b: string) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };

  it.each(ACCENT_PRESETS)('%s clears AA body under the on-accent text colour', (preset) => {
    expect(onAccent).toBe('#ffffff');
    expect(ratio(onAccent, preset)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('responsive behaviour at the 1024 floor', () => {
  it('keeps the map meaningfully visible behind the right-hand drawers at 1024', () => {
    // Visible map at the 1024 floor = 1024 - 48 (rail) - 330 (context panel) = 646px.
    // Review finding: a nominally-clamped 62vw drawer left an 11px sliver, i.e.
    // "technically visible" and practically covered. Both drawers now share one
    // token, and the assertion is the outcome (map strip), not the expression.
    const css = read('app/globals.css');
    const rule = css.match(/--drawer-w:\s*min\((\d+)px,\s*max\((\d+)px,\s*(\d+)vw\)\);/);
    expect(rule, '--drawer-w should be a min(cap, max(floor, Nvw)) rule').not.toBeNull();
    const [cap, floor, vw] = rule!.slice(1).map(Number);

    const mapStrip = (viewport: number) => {
      const drawer = Math.min(cap, Math.max(floor, (vw / 100) * viewport));
      return viewport - drawer - 378; // 378 = rail 48 + context panel 330
    };
    // At the minimum target the map must keep a usable strip, not a sliver.
    expect(mapStrip(1024)).toBeGreaterThan(120);
    // And the drawer must not keep growing past its cap on wide screens.
    expect(mapStrip(1920)).toBeGreaterThan(700);

    // Both drawers must consume the token rather than restating the formula.
    for (const file of [
      'components/settings/settings-panel.tsx',
      'components/drawers/template-gallery-v2.tsx',
    ]) {
      const src = readCode(file);
      expect(src, `${file} should use var(--drawer-w)`).toContain("width: 'var(--drawer-w)'");
      expect(src, `${file} should not restate the width formula`).not.toMatch(/\d+vw/);
    }
  });

  it('bounds the tweaks panel height and lets it scroll', () => {
    const src = read('components/tweaks-panel.tsx');
    expect(src).toMatch(/max-h-\[min\(/);
    expect(src).toMatch(/overflow-y-auto/);
  });
});

describe('no unnecessary compositing over the map', () => {
  const OVER_MAP = [
    'components/layout/nav-rail.tsx',
    'components/layout/context-panel.tsx',
    'components/layout/top-bar.tsx',
    'components/sidebar/chat-tab.tsx',
    'components/drawers/history-drawer.tsx',
    'components/panel/rag-independent-panel.tsx',
    'components/tweaks-panel.tsx',
    'components/map/legends/legend-card.tsx',
  ];

  it.each(OVER_MAP)('%s does not blur the map behind it', (file) => {
    // A backdrop-filter over a continuously repainting canvas is the expensive
    // kind, and translucency lets map detail bleed through dense text.
    const src = readCode(file);
    expect(src).not.toMatch(/backdropFilter/);
    expect(src).not.toMatch(/backdrop-blur/);
  });
});

/* ───────────────────────── interaction state contracts ───────────────────── */

describe('selected is distinguishable from hover', () => {
  it('gives the nav rail active tab a different background than hover', async () => {
    const { NavRail } = await import('@/components/layout/nav-rail');
    const { container } = render(<NavRail />);
    const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
    const active = tabs.find((t) => t.getAttribute('aria-selected') === 'true')!;
    const inactive = tabs.find((t) => t.getAttribute('aria-selected') === 'false')!;

    // Before V4 both used the same `bg-[var(--theme-bg-hover)]`, so hovering the
    // selected tab produced no change at all.
    expect(active.className).toContain('bg-status-accent-soft');
    expect(inactive.className).not.toContain('bg-status-accent-soft');
    expect(inactive.className).toContain('hover:bg-surface-hover');
  });
});

describe('graduated legend classes are operable and stateful', () => {
  beforeEach(() => vi.clearAllMocks());

  it('toggles a class with Space as well as Enter and reports aria-pressed', async () => {
    const { GraduatedLegend } = await import('@/components/map/legends/graduated-legend');
    const onFilterChange = vi.fn();
    render(
      <GraduatedLegend
        spec={{
          type: 'graduated',
          field: 'pop',
          breaks: [0, 10, 20],
          palette: 'Blues',
          palette_colors: ['#deebf7', '#3182bd'],
        }}
        onFilterChange={onFilterChange}
      />,
    );
    const rows = screen.getAllByRole('button');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('aria-pressed', 'true');

    // Space must work, not only Enter: role="button" implies both.
    rows[0].focus();
    await userEvent.keyboard(' ');
    expect(onFilterChange).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole('button')[0]).toHaveAttribute('aria-pressed', 'false');

    await userEvent.keyboard('{Enter}');
    expect(onFilterChange).toHaveBeenCalledTimes(2);
    expect(screen.getAllByRole('button')[0]).toHaveAttribute('aria-pressed', 'true');
  });
});
