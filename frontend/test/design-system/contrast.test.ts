/**
 * WCAG contrast contract for the V4 token layer.
 *
 * The audit that motivated V4 measured the shipped light theme and found the
 * muted/subtle text tokens at 2.45:1 and 1.42:1 — both used for real label copy,
 * both far under the 4.5:1 AA floor. Those are not judgement calls, they are
 * arithmetic, so they belong in a test rather than in a review comment.
 *
 * The ratios are computed from the actual values in `app/globals.css`, alpha
 * composited over the surface they sit on, so retuning a hue without checking
 * its contrast fails here rather than in production.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CSS = readFileSync(resolve(__dirname, '../../app/globals.css'), 'utf8');

type RGB = [number, number, number];
type RGBA = [number, number, number, number];

function blockBody(selector: string): string {
  const start = CSS.indexOf(selector);
  const open = CSS.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1;
    else if (CSS[i] === '}') {
      depth -= 1;
      if (depth === 0) return CSS.slice(open + 1, i);
    }
  }
  throw new Error(`unterminated block for ${selector}`);
}

function rawTokens(selector: string): Map<string, string> {
  const body = blockBody(selector).replace(/\/\*[\s\S]*?\*\//g, '');
  const out = new Map<string, string>();
  for (const line of body.split(';')) {
    const m = line.match(/(--[a-z0-9-]+)\s*:\s*(.+)/i);
    if (m) out.set(m[1], m[2].trim());
  }
  return out;
}

const LIGHT = rawTokens(':root');
const DARK = rawTokens(".dark,\n[data-theme='dark']");

/** AA body text. Everything that can carry a sentence must clear this. */
const AA_BODY = 4.5;
/** AA non-text / large text. Icons, dividers, decorative marks. */
const AA_NON_TEXT = 3.0;

/** The five accent presets the tweaks panel offers. */
const ACCENT_PRESETS = (() => {
  const src = readFileSync(resolve(__dirname, '../../components/tweaks-panel.tsx'), 'utf8');
  return Array.from(src.matchAll(/\{ value: '(#[0-9a-f]{6})', name:/gi)).map((m) => m[1]);
})();

/** Resolves a token to a colour, following one level of `var()` indirection. */
function resolve_(theme: Map<string, string>, token: string): RGBA {
  let value = theme.get(token) ?? LIGHT.get(token);
  if (!value) throw new Error(`unknown token ${token}`);
  const varMatch = value.match(/^var\((--[a-z0-9-]+)\)$/i);
  if (varMatch) value = theme.get(varMatch[1]) ?? LIGHT.get(varMatch[1]) ?? '';
  return parseColor(value, token);
}

function parseColor(value: string, token: string): RGBA {
  const hex = value.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const short = value.match(/^#([0-9a-f]{3})$/i);
  if (short) {
    const [r, g, b] = short[1].split('').map((c) => parseInt(c + c, 16));
    return [r, g, b, 1];
  }
  const rgba = value.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?\s*\)$/i);
  if (rgba) {
    return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])];
  }
  throw new Error(`cannot parse colour for ${token}: ${value}`);
}

/** Composites a possibly-translucent colour over an opaque backdrop. */
function over(fg: RGBA, bg: RGB): RGB {
  const a = fg[3];
  return [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)) as RGB;
}

function luminance([r, g, b]: RGB): number {
  const lin = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrast(fg: RGB, bg: RGB): number {
  const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

/** Contrast of `textToken` on `surfaceToken`, each composited over the canvas. */
function ratio(theme: Map<string, string>, textToken: string, surfaceToken: string): number {
  const canvas = over(resolve_(theme, '--surface-canvas'), [255, 255, 255]);
  const surface = over(resolve_(theme, surfaceToken), canvas);
  const text = over(resolve_(theme, textToken), surface);
  return contrast(text, surface);
}

/**
 * The runtime accent is a CSS lever: `--agent-accent-raw` carries the exact hex
 * the user picked, and `--agent-accent` is the theme-corrected value every
 * consumer reads. This checks the correction actually holds for every preset —
 * the raw presets are chosen to carry a white label on a light surface, which
 * makes them far too dark to work on a dark one (2.09:1 as a mark, 2.66:1 under
 * the near-black on-accent label), so a missing correction is a silent AA
 * failure across the whole dark theme.
 */
describe('runtime accent correction', () => {
  const ACCENT_MIX = /--agent-accent:\s*color-mix\(in srgb,\s*var\(--agent-accent-raw\)\s*(\d+)%,\s*#ffffff\)/;

  it('derives the dark accent from the raw value rather than using it directly', () => {
    const darkBlock = blockBody(".dark,\n[data-theme='dark']");
    expect(darkBlock, 'dark theme must correct the raw accent').toMatch(ACCENT_MIX);
    // Light is the identity: the presets already clear AA there.
    expect(blockBody(':root')).toMatch(/--agent-accent:\s*var\(--agent-accent-raw\)/);
  });

  it.each(ACCENT_PRESETS)(
    '%s stays visible as a mark and can carry a label in dark',
    (preset) => {
      const pct = Number(blockBody(".dark,\n[data-theme='dark']").match(ACCENT_MIX)![1]);
      const raw = parseColor(preset, 'preset');
      const fill: RGB = [0, 1, 2].map(
        (i) => Math.round(raw[i] * (pct / 100) + 255 * (1 - pct / 100)),
      ) as RGB;

      const canvas = over(resolve_(DARK, '--surface-canvas'), [255, 255, 255]);
      // Worst case surface for a mark is the lightest dark surface.
      const overlay = over(resolve_(DARK, '--surface-overlay'), canvas);
      expect(contrast(fill, overlay), `${preset} as a mark`).toBeGreaterThanOrEqual(AA_NON_TEXT);

      const label = over(resolve_(DARK, '--text-on-accent'), fill);
      expect(contrast(label, fill), `${preset} under its label`).toBeGreaterThanOrEqual(AA_BODY);
    },
  );

  it.each(ACCENT_PRESETS)('%s can carry a label in light without correction', (preset) => {
    const [r, g, b] = parseColor(preset, 'preset');
    const fill: RGB = [r, g, b];
    const label = over(resolve_(LIGHT, '--text-on-accent'), fill);
    expect(contrast(label, fill)).toBeGreaterThanOrEqual(AA_BODY);
  });
});

const THEMES: [string, Map<string, string>][] = [
  ['light', LIGHT],
  ['dark', DARK],
];

describe.each(THEMES)('WCAG contrast — %s theme', (_name, theme) => {
  const SURFACES = ['--surface-panel', '--surface-raised', '--surface-overlay', '--surface-sunken'];

  it.each(SURFACES)('text-primary clears AA body on %s', (surface) => {
    expect(ratio(theme, '--text-primary', surface)).toBeGreaterThanOrEqual(AA_BODY);
  });

  it.each(SURFACES)('text-secondary clears AA body on %s', (surface) => {
    expect(ratio(theme, '--text-secondary', surface)).toBeGreaterThanOrEqual(AA_BODY);
  });

  it.each(SURFACES)('text-muted clears AA body on %s', (surface) => {
    // The regression that started this work: --text-muted is used for 60+ label
    // and hint sites, so it is body text, not decoration.
    expect(ratio(theme, '--text-muted', surface)).toBeGreaterThanOrEqual(AA_BODY);
  });

  it('text-muted clears AA body directly on the canvas', () => {
    expect(ratio(theme, '--text-muted', '--surface-canvas')).toBeGreaterThanOrEqual(AA_BODY);
  });

  it.each(SURFACES)('text-disabled clears the AA non-text floor on %s', (surface) => {
    // Disabled/placeholder copy is exempt from AA body, but must stay visible.
    expect(ratio(theme, '--text-disabled', surface)).toBeGreaterThanOrEqual(AA_NON_TEXT);
  });

  it('accent clears AA body as label text on a panel', () => {
    // `--accent` is the text-safe accent; `--accent-vivid` is fills only.
    expect(ratio(theme, '--accent', '--surface-panel')).toBeGreaterThanOrEqual(AA_BODY);
  });

  it.each(['--success', '--info', '--warning', '--critical', '--neutral'])(
    '%s clears AA body as status text on a panel',
    (token) => {
      expect(ratio(theme, token, '--surface-panel')).toBeGreaterThanOrEqual(AA_BODY);
    },
  );

  it('the focus ring clears the AA non-text floor against every surface', () => {
    for (const surface of [...SURFACES, '--surface-canvas']) {
      expect(
        ratio(theme, '--focus-ring', surface),
        `focus ring on ${surface}`,
      ).toBeGreaterThanOrEqual(AA_NON_TEXT);
    }
  });

  it('map chrome text clears AA body on the map chrome surface', () => {
    const canvas = over(resolve_(theme, '--surface-canvas'), [255, 255, 255]);
    const chrome = over(resolve_(theme, '--map-chrome-bg'), canvas);
    expect(contrast(over(resolve_(theme, '--map-chrome-text'), chrome), chrome)).toBeGreaterThanOrEqual(AA_BODY);
    expect(contrast(over(resolve_(theme, '--map-chrome-text-muted'), chrome), chrome)).toBeGreaterThanOrEqual(AA_BODY);
  });

  it('text-on-accent clears AA body on the text-bearing accent fill', () => {
    // White on the old #16a34a measured 3.30:1 and was used for button labels
    // and chat bubbles. The V4 rule is that text-bearing fills use `--accent`
    // (checked here) while `--accent-vivid` is reserved for non-text marks.
    const canvas = over(resolve_(theme, '--surface-canvas'), [255, 255, 255]);
    const fill = over(resolve_(theme, '--accent'), canvas);
    expect(contrast(over(resolve_(theme, '--text-on-accent'), fill), fill)).toBeGreaterThanOrEqual(AA_BODY);
  });

  it('separates the surface ladder by a perceptible step', () => {
    // canvas → panel → raised must be visually distinguishable, otherwise the
    // "map bed vs panel vs card" hierarchy collapses into one flat field.
    const canvas = over(resolve_(theme, '--surface-canvas'), [255, 255, 255]);
    const panel = over(resolve_(theme, '--surface-panel'), canvas);
    const raised = over(resolve_(theme, '--surface-raised'), canvas);
    expect(Math.abs(luminance(panel) - luminance(canvas))).toBeGreaterThan(0.004);
    expect(Math.abs(luminance(raised) - luminance(panel))).toBeGreaterThan(0.004);
  });
});
