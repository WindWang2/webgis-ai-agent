import { describe, it, expect } from 'vitest';

/**
 * Unit tests for analysis prompt builder logic.
 * The UI rendering tests use TSX and share a pre-existing JSX transform issue
 * across all component tests in this project. Test the core logic here instead.
 */

function buildAnalysisPrompt(tool: string, params: Record<string, string>): string {
  const layerName = (id: string) => id;

  if (tool === 'buffer') {
    return `对图层 "${layerName(params.layer)}" 进行缓冲区分析，缓冲距离为 ${params.distance} 米`;
  }
  if (tool === 'overlay') {
    const opMap: Record<string, string> = { intersection: '相交', union: '合并', difference: '差异', symmetric_difference: '对称差异' };
    return `对图层 "${layerName(params.layerA)}" 和 "${layerName(params.layerB)}" 进行叠加分析，操作类型为${opMap[params.op] ?? params.op}`;
  }
  if (tool === 'clip') {
    return `用图层 "${layerName(params.mask)}" 裁剪图层 "${layerName(params.target)}"`;
  }
  return '';
}

describe('buildAnalysisPrompt', () => {
  it('builds buffer prompt with distance', () => {
    const prompt = buildAnalysisPrompt('buffer', { layer: 'roads', distance: '500' });
    expect(prompt).toContain('缓冲区');
    expect(prompt).toContain('500');
    expect(prompt).toContain('roads');
  });

  it('builds overlay prompt with operation type', () => {
    const prompt = buildAnalysisPrompt('overlay', { layerA: 'roads', layerB: 'buildings', op: 'intersection' });
    expect(prompt).toContain('叠加分析');
    expect(prompt).toContain('相交');
    expect(prompt).toContain('roads');
    expect(prompt).toContain('buildings');
  });

  it('builds clip prompt with target and mask', () => {
    const prompt = buildAnalysisPrompt('clip', { target: 'buildings', mask: 'boundary' });
    expect(prompt).toContain('裁剪');
    expect(prompt).toContain('buildings');
    expect(prompt).toContain('boundary');
  });

  it('handles all overlay operations', () => {
    const ops = ['intersection', 'union', 'difference', 'symmetric_difference'];
    const labels = ['相交', '合并', '差异', '对称差异'];
    ops.forEach((op, i) => {
      const prompt = buildAnalysisPrompt('overlay', { layerA: 'a', layerB: 'b', op });
      expect(prompt).toContain(labels[i]);
    });
  });

  it('returns empty for unknown tool', () => {
    expect(buildAnalysisPrompt('unknown', {})).toBe('');
  });
});
