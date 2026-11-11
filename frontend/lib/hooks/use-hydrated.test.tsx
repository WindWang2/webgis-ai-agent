import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { render, screen } from '@testing-library/react';
import { useHydrated } from './use-hydrated';

function Probe() {
  const hydrated = useHydrated();
  return <span data-testid="probe">{hydrated ? 'client' : 'server'}</span>;
}

describe('useHydrated', () => {
  it('SSR 渲染返回 false（与服务端默认值一致）', () => {
    expect(renderToString(<Probe />)).toContain('server');
  });

  it('客户端挂载后返回 true', () => {
    render(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('client');
  });
});
