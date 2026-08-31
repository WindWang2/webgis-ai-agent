import { describe, expect, it } from 'vitest';
import {
  suggestViewportCollapses,
  VIEWPORT_COLLAPSE_CANVAS_HEIGHT,
  type LayoutParticipant,
} from './resolve-layout';

/**
 * Scenario H 视口折叠规则（Workspace V2 / Goal E）—— 派生建议语义：
 *
 * - 小画布（< VIEWPORT_COLLAPSE_CANVAS_HEIGHT）→ 面板族
 *   （statistics/chart）建议折叠到标题条；
 * - user-pinned 浮动面板绝不折叠（user > agent > auto）；
 * - 非面板 chrome（图例/色条/标题）不参与 —— 折叠语义只对有正文的
 *   面板成立；
 * - 大画布（常规桌面/导出 A4）→ 空建议（导出 parity 不受视口规则影响）。
 */

function participant(overrides: Partial<LayoutParticipant> = {}): LayoutParticipant {
  return {
    id: 'p1',
    type: 'chart_panel',
    anchor: 'top-left',
    floating: false,
    origin: 'auto',
    ...overrides,
  };
}

describe('suggestViewportCollapses', () => {
  it('suggests collapsing anchored panels on small canvases', () => {
    const out = suggestViewportCollapses(
      [
        participant({ id: 'chart', type: 'chart_panel' }),
        participant({ id: 'stats', type: 'statistics_panel' }),
      ],
      { width: 380, height: 480 },
    );
    expect([...out].sort()).toEqual(['chart', 'stats']);
  });

  it('never collapses user-pinned floating panels', () => {
    const out = suggestViewportCollapses(
      [participant({ id: 'chart', floating: true, origin: 'user' })],
      { width: 380, height: 480 },
    );
    expect(out.size).toBe(0);
  });

  it('non-panel chrome types are exempt (no body to collapse)', () => {
    const out = suggestViewportCollapses(
      [
        participant({ id: 'legend', type: 'continuous_colorbar' }),
        participant({ id: 'title', type: 'title' }),
        participant({ id: 'north', type: 'north_arrow' }),
      ],
      { width: 380, height: 480 },
    );
    expect(out.size).toBe(0);
  });

  it('large canvases (desktop live / export A4) produce no suggestions', () => {
    const out = suggestViewportCollapses(
      [participant({ id: 'chart' })],
      { width: 1600, height: 1200 },
    );
    expect(out.size).toBe(0);
  });

  it('missing canvas (no layout context) is honest: no suggestion', () => {
    expect(suggestViewportCollapses([participant()]).size).toBe(0);
  });

  it('threshold is exactly the documented boundary', () => {
    expect(VIEWPORT_COLLAPSE_CANVAS_HEIGHT).toBe(520);
    const at = suggestViewportCollapses([participant()], { width: 800, height: 520 });
    const below = suggestViewportCollapses([participant()], { width: 800, height: 519 });
    expect(at.size).toBe(0);
    expect(below.size).toBe(1);
  });
});
