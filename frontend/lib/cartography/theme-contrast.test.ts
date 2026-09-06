import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * V3（ADR-0101 D4）— cartographic theme 对比度基础检查。
 *
 * 后端 CartographicThemeDescriptor 的 chrome 颜色是对本文件语义 token 的
 * **引用**（真值唯一在此）；本测试对 :root 与 .dark 两个 profile 实测
 * WCAG AA（正文对 ≥ 4.5:1、大字对 ≥ 3:1），锁定「有 WCAG/contrast 基础
 * 检查」的契约。解析失败即测试失败 —— token 更名必须同步主题描述层。
 */

const cssPath = resolve(__dirname, '../../app/globals.css');
const css = readFileSync(cssPath, 'utf-8');

interface Vars {
  [name: string]: string;
}

function parseHexVars(section: string): Vars {
  const vars: Vars = {};
  const re = /--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\s*;/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(section)) !== null) {
    vars[m[1]] = m[2];
  }
  return vars;
}


/** rgba(a,r,g,b) → 不透明合成近似（按白/黑底混合后计算）。 */
function toOpaque(color: string, onDark: boolean): string {
  const rgba = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(color);
  if (!rgba) return color;
  const [, r, g, b, a] = rgba;
  if (a === undefined) return `#${[r, g, b].map((v) => Number(v).toString(16).padStart(2, '0')).join('')}`;
  const alpha = Number(a);
  const base = onDark ? 19 : 253; // dark canvas ≈ #131c2e 附近；light 近白
  const mix = (c: number) => Math.round(c * alpha + base * (1 - alpha));
  return `#${[mix(Number(r)), mix(Number(g)), mix(Number(b))].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

function srgbToLinear(c: number): number {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

function luminance(color: string): number {
  const opaque = toOpaque(color, false);
  const h = opaque.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

function contrast(fg: string, bg: string): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

const lightSection = css.slice(0, css.indexOf('.dark'));
const darkSection = css.slice(css.indexOf('.dark'));

// 后端 themes.py ChromeTokenRefs 的缺省引用集（由
// tests/cartography/test_themes_v3.py::test_chrome_token_refs_vocabulary
// 锁定同表）—— token 更名必须两侧同步。
const DESCRIPTOR_TOKEN_REFS = [
  'surface-panel',
  'surface-raised',
  'text-primary',
  'text-secondary',
  'map-chrome-border',
  'map-chrome-bg',
  'map-chrome-text',
  'map-chrome-text-muted',
] as const;

describe('V3 cartographic theme contrast gates (WCAG AA)', () => {
  it('主题描述层引用的 token 全部存在于 globals.css（light + dark 两 profile）', () => {
    for (const [profile, vars] of profiles) {
      for (const token of DESCRIPTOR_TOKEN_REFS) {
        expect(vars[token], `${profile} 缺少主题描述层引用的 token: --${token}`).toBeTruthy();
      }
    }
  });

  const profiles: Array<[string, Vars, boolean]> = [
    ['light', parseHexVars(lightSection), false],
    ['dark', parseHexVars(darkSection), true],
  ];

  for (const [profile, vars, onDark] of profiles) {
    it(`${profile}: 正文 token 在其面板底色上 ≥ 4.5:1`, () => {
      const panel = vars['surface-panel'];
      expect(panel, `${profile} --surface-panel 缺失或非 hex/rgba`).toBeTruthy();
      for (const token of ['text-primary', 'text-secondary']) {
        const fg = vars[token];
        expect(fg, `${profile} --${token} 缺失`).toBeTruthy();
        const ratio = contrast(toOpaque(fg, onDark), toOpaque(panel, onDark));
        expect(ratio, `${profile} --${token} on --surface-panel = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${profile}: map-chrome 文本在其底色上 ≥ 4.5:1`, () => {
      const bg = vars['map-chrome-bg'];
      const ink = vars['map-chrome-text'];
      const muted = vars['map-chrome-text-muted'];
      expect(bg && ink && muted, `${profile} map-chrome token 缺失`).toBeTruthy();
      expect(contrast(toOpaque(ink, onDark), toOpaque(bg, onDark))).toBeGreaterThanOrEqual(4.5);
      // muted 是辅助信息（小字），也必须过 AA
      expect(contrast(toOpaque(muted, onDark), toOpaque(bg, onDark))).toBeGreaterThanOrEqual(4.5);
    });
  }
});
