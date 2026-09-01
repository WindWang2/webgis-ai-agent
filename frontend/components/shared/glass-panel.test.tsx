import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GlassPanel } from './glass-panel';

describe('GlassPanel', () => {
  it('renders children with semantic surface tokens', () => {
    render(
      <GlassPanel data-testid="glass-panel">
        <span>Glass Content</span>
      </GlassPanel>
    );

    const panel = screen.getByTestId('glass-panel');
    expect(panel).toBeInTheDocument();
    expect(screen.getByText('Glass Content')).toBeInTheDocument();
    expect(panel.className).toContain('bg-surface-raised/90');
    expect(panel.className).toContain('dark:bg-surface-overlay/85');
    expect(panel.className).toContain('backdrop-blur-md');
    expect(panel.className).toContain('border-edge-subtle');
    expect(panel.className).toContain('shadow-sm');
  });

  it('merges custom className properly', () => {
    render(
      <GlassPanel className="custom-class" data-testid="glass-panel">
        Content
      </GlassPanel>
    );

    const panel = screen.getByTestId('glass-panel');
    expect(panel.className).toContain('custom-class');
    expect(panel.className).toContain('bg-surface-raised/90');
  });
});
