/**
 * VNext §5/§9/§13 披露族渲染器回归：注册在场 + 防御式解析 + 空态。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { allRegisteredTypes, getComponentRenderer } from './registry';
import { renderComponent } from './index';

describe('disclosure renderers', () => {
  beforeEach(() => {
    cleanup();
  });

  it('registers all three disclosure component types', () => {
    const types = allRegisteredTypes();
    expect(types).toContain('methodology_note');
    expect(types).toContain('uncertainty_panel');
    expect(types).toContain('decision_panel');
  });

  it('renders methodology warnings with stable codes', () => {
    const el = renderComponent(
      {
        id: 'methodology-note',
        type: 'methodology_note',
        enabled: true,
        position: 'bottom-left',
        priority: 46,
        options: {
          warnings: [
            {
              code: 'EQUITY_MISSING_DENOMINATOR',
              pattern: 'spatial_equity',
              text: '缺分母不能下公平性结论',
            },
          ],
        },
      } as never,
      {} as never,
    );
    const { container } = render(<>{el}</>);
    expect(container.textContent).toContain('EQUITY_MISSING_DENOMINATOR');
    expect(container.textContent).toContain('缺分母不能下公平性结论');
  });

  it('renders uncertainty items and sample note', () => {
    const el = renderComponent(
      {
        id: 'uncertainty-panel',
        type: 'uncertainty_panel',
        enabled: true,
        position: 'bottom-right',
        priority: 47,
        options: {
          uncertainty: {
            items: [{ label: '克里金方差', kind: 'variance', detail: '[0, 0.8]' }],
            sampleNote: '240 站点',
          },
        },
      } as never,
      {} as never,
    );
    const { container } = render(<>{el}</>);
    expect(container.textContent).toContain('克里金方差');
    expect(container.textContent).toContain('240 站点');
  });

  it('renders decision rows with weight source and vetoes', () => {
    const el = renderComponent(
      {
        id: 'decision-panel',
        type: 'decision_panel',
        enabled: true,
        position: 'top-right',
        priority: 48,
        options: {
          decision: {
            method: 'TOPSIS',
            weightSource: '用户指定',
            rows: [{ rank: 1, name: '候选A', score: 0.82, basis: 'observed' }],
            vetoes: ['候选B 位于禁建区'],
          },
        },
      } as never,
      {} as never,
    );
    const { container } = render(<>{el}</>);
    expect(container.textContent).toContain('TOPSIS');
    expect(container.textContent).toContain('用户指定');
    expect(container.textContent).toContain('候选A');
    expect(container.textContent).toContain('候选B 位于禁建区');
  });

  it('degrades to empty state on malformed payload', () => {
    const renderer = getComponentRenderer('methodology_note');
    expect(renderer).toBeDefined();
    const { container } = render(
      <>
        {renderer!(
          {
            id: 'methodology-note',
            type: 'methodology_note',
            enabled: true,
            position: 'bottom-left',
            priority: 46,
            options: { warnings: 'not-an-array' },
          } as never,
          {} as never,
        )}
      </>,
    );
    expect(container.querySelector('[data-state="empty"]')).toBeTruthy();
    expect(screen.queryByText('undefined')).toBeNull();
  });
});
