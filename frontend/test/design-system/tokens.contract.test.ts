/**
 * Design-token contract (Visual System V4).
 *
 * These assertions guard the *structure* of the token layer, not individual
 * colour values — a designer must stay free to retune a hue. What must not
 * silently break is:
 *
 *   - `darkMode: 'class'`. Without it Tailwind v3 falls back to `media`, and
 *     every `dark:` variant in the app tracks the OS instead of the in-app
 *     theme toggle. This was a real, shipped bug: 124 `dark:` variants across
 *     13 files were inert.
 *   - Every semantic token defined in `:root` also being defined under `.dark`.
 *     A token that exists in one theme only is a guaranteed dark-mode hole.
 *   - The Tailwind theme actually pointing at the CSS custom properties, so
 *     `bg-surface-panel` and `var(--surface-panel)` cannot drift apart.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import config from '../../tailwind.config';

const CSS = readFileSync(resolve(__dirname, '../../app/globals.css'), 'utf8');

/** Extracts `--name: value` declarations from one CSS block. */
function tokensIn(selector: string): Map<string, string> {
  const start = CSS.indexOf(selector);
  expect(start, `selector ${selector} not found in globals.css`).toBeGreaterThan(-1);
  const open = CSS.indexOf('{', start);
  // Walk braces so a nested block cannot truncate the scan early.
  let depth = 0;
  let end = open;
  for (let i = open; i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1;
    else if (CSS[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = CSS.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, '');
  const out = new Map<string, string>();
  for (const line of body.split(';')) {
    const m = line.match(/(--[a-z0-9-]+)\s*:\s*(.+)/i);
    if (m) out.set(m[1], m[2].trim());
  }
  return out;
}

const LIGHT = tokensIn(':root');
const DARK = tokensIn(".dark,\n[data-theme='dark']");

/** The semantic vocabulary components are expected to reach for. */
const REQUIRED_TOKENS = [
  // surfaces
  '--surface-canvas',
  '--surface-panel',
  '--surface-raised',
  '--surface-overlay',
  '--surface-sunken',
  '--surface-hover',
  '--surface-selected',
  '--surface-scrim',
  // borders
  '--border-subtle',
  '--border-default',
  '--border-strong',
  // text
  '--text-primary',
  '--text-secondary',
  '--text-muted',
  '--text-disabled',
  '--text-on-accent',
  // status
  '--accent',
  '--accent-vivid',
  '--accent-soft',
  '--accent-border',
  '--success',
  '--info',
  '--warning',
  '--critical',
  '--neutral',
  // elevation
  '--elevation-raised',
  '--elevation-overlay',
  '--elevation-drawer',
  // focus
  '--focus-ring',
  // map chrome
  '--map-chrome-bg',
  '--map-chrome-border',
  '--map-chrome-text',
  '--map-chrome-text-muted',
  '--map-chrome-shadow',
];

/** Scale tokens are theme-independent: they live in `:root` only, by design. */
const SCALE_TOKENS = [
  '--radius-xs',
  '--radius-sm',
  '--radius-md',
  '--radius-lg',
  '--radius-xl',
  '--radius-pill',
  '--font-micro',
  '--font-caption',
  '--font-meta',
  '--font-body',
  '--font-title',
  '--font-heading',
  '--control-sm',
  '--control-md',
  '--control-lg',
  '--row-sm',
  '--row-md',
  '--row-lg',
  '--icon-sm',
  '--icon-md',
  '--icon-lg',
  '--panel-pad',
  '--topH',
  '--stH',
  '--railW',
  '--sw',
];

describe('V4 design tokens — structure', () => {
  it('enables class-based dark mode', () => {
    // The single most consequential line in the config: with `media` (the v3
    // default) the in-app theme toggle cannot reach any `dark:` variant.
    expect(config.darkMode).toBe('class');
  });

  it.each(REQUIRED_TOKENS)('defines %s in the light theme', (token) => {
    expect(LIGHT.has(token)).toBe(true);
  });

  it.each(REQUIRED_TOKENS)('re-defines %s in the dark theme', (token) => {
    // A theme-dependent token defined in only one theme is a dark-mode hole.
    expect(DARK.has(token)).toBe(true);
  });

  it.each(SCALE_TOKENS)('defines the theme-independent scale token %s', (token) => {
    expect(LIGHT.has(token)).toBe(true);
    expect(DARK.has(token)).toBe(false);
  });

  it('keeps the legacy --theme-* vocabulary aliased to the V4 tokens', () => {
    // ~475 call sites still read `var(--theme-*)`. They must resolve through
    // the V4 values, otherwise the app carries two divergent palettes again.
    for (const theme of [LIGHT, DARK]) {
      for (const [name, value] of Array.from(theme.entries())) {
        if (!name.startsWith('--theme-')) continue;
        expect(value, `${name} should alias a V4 token, got ${value}`).toMatch(/^var\(--/);
      }
    }
  });

  it('routes the shadcn HSL vars and the V4 surfaces at the same colours', () => {
    // `bg-card` and `bg-surface-panel` are both used for panel chrome; if they
    // drift the panel splits into two visibly different greys.
    for (const theme of [LIGHT, DARK]) {
      expect(theme.get('--background')).toBeDefined();
      expect(theme.get('--card')).toBeDefined();
      // HSL triples, not hex — shadcn utilities wrap them in `hsl(...)`.
      expect(theme.get('--background')).toMatch(/^\d+ \d+% \d+%$/);
      expect(theme.get('--card')).toMatch(/^\d+ \d+% \d+%$/);
    }
  });
});

describe('V4 design tokens — Tailwind exposure', () => {
  const colors = (config.theme?.extend?.colors ?? {}) as Record<string, unknown>;

  it('exposes the surface / edge / ink / status families as utilities', () => {
    for (const family of ['surface', 'edge', 'ink', 'status', 'map-chrome']) {
      expect(colors[family], `missing colour family: ${family}`).toBeDefined();
    }
  });

  it('binds every V4 colour utility to a CSS custom property', () => {
    // Hardcoding a literal here would re-introduce a value that cannot flip
    // with the theme — the exact failure mode V4 exists to remove.
    const walk = (value: unknown, path: string) => {
      if (typeof value === 'string') {
        if (!path.startsWith('surface') && !path.startsWith('edge') && !path.startsWith('ink') &&
            !path.startsWith('status') && !path.startsWith('map-chrome')) return;
        expect(value, `${path} must reference a CSS var, got ${value}`).toMatch(/^var\(--/);
        return;
      }
      if (value && typeof value === 'object') {
        for (const [k, v] of Object.entries(value)) walk(v, `${path}.${k}`);
      }
    };
    for (const [k, v] of Object.entries(colors)) walk(v, k);
  });

  it('exposes the dense type scale, control metrics and radius scale', () => {
    const fontSize = (config.theme?.extend?.fontSize ?? {}) as Record<string, unknown>;
    const spacing = (config.theme?.extend?.spacing ?? {}) as Record<string, unknown>;
    const radius = (config.theme?.extend?.borderRadius ?? {}) as Record<string, unknown>;

    for (const step of ['micro', 'caption', 'meta', 'body', 'title', 'heading']) {
      expect(fontSize[step], `missing type step: ${step}`).toBeDefined();
    }
    for (const step of ['control-sm', 'control-md', 'control-lg', 'row-sm', 'row-md', 'row-lg',
      'icon-sm', 'icon-md', 'icon-lg', 'panel', 'topbar', 'statusbar', 'rail', 'sidebar']) {
      expect(spacing[step], `missing metric: ${step}`).toBeDefined();
    }
    for (const step of ['xs', 'sm', 'md', 'lg', 'xl', 'pill']) {
      expect(radius[step], `missing radius step: ${step}`).toBeDefined();
    }
  });
});

describe('V4 design tokens — dead CSS stays dead', () => {
  it('does not reintroduce the unused .glass/.ib/.tb/.lc/.sq component classes', () => {
    // They had zero call sites and hardcoded light-only colours, which is a
    // dark-mode trap waiting for the next component to opt in.
    for (const cls of ['.glass ', '.glass{', '.ib ', '.ib:', '.tb ', '.tb:', '.lc ', '.lc:', '.sq ', '.sq:']) {
      expect(CSS.includes(cls), `dead class ${cls} is back in globals.css`).toBe(false);
    }
  });

  it('keeps the shell metric tokens actually referenced', () => {
    // `--topH/--stH/--sw` existed before V4 with zero `var()` consumers; they
    // are now the source of truth behind the Tailwind spacing scale.
    const spacing = JSON.stringify(config.theme?.extend?.spacing ?? {});
    for (const token of ['--topH', '--stH', '--sw', '--railW']) {
      expect(spacing, `${token} is defined but nothing consumes it`).toContain(token);
    }
  });
});
