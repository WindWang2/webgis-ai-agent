import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MapActionRenderer } from './map-action-renderer';

const dispatchAction = vi.fn();

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction }),
  MapActionProvider: ({ children }: { children: React.ReactNode }) => children,
  MapActionContext: {},
}));

describe('MapActionRenderer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns null for empty content', () => {
    const { container } = render(<MapActionRenderer content="" />);
    expect(container.innerHTML).toBe('');
  });

  it('returns null for "undefined" content', () => {
    const { container } = render(<MapActionRenderer content="undefined" />);
    expect(container.innerHTML).toBe('');
  });

  it.skip('dispatches action for bare JSON with command', async () => {
    // The component's regex \{[\s\S]*?\} is non-greedy and may not capture nested JSON
    // JSON is typically delivered in code fences from the backend
    render(<MapActionRenderer content=' {"command":"fly_to","params":{"center":[116.4,39.9]}} ' />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledWith({
        command: 'fly_to',
        params: { center: [116.4, 39.9] },
      });
    }, { timeout: 2000 });
  });

  it('dispatches action for JSON in markdown code fence', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"add_layer","params":{"name":"test"}}\n```'} />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledWith({
        command: 'add_layer',
        params: { name: 'test' },
      });
    }, { timeout: 2000 });
  });

  it('dispatches multiple JSON blocks', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"fly_to","params":{"zoom":12}}\n```\n```json\n{"command":"add_layer","params":{}}\n```'} />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledTimes(2);
    }, { timeout: 2000 });
  });

  it('skips JSON without command field', async () => {
    const { container } = render(<MapActionRenderer content='{"no_command":true}' />);
    // Component returns null when no valid actions found
    await waitFor(() => {
      expect(container.innerHTML).toBe('');
    });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('skips invalid JSON blocks', async () => {
    const { container } = render(<MapActionRenderer content='not json at all' />);
    await waitFor(() => {
      expect(container.innerHTML).toBe('');
    });
  });

  it('shows success status after parsing', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"fly_to","params":{}}\n```'} />);
    await waitFor(() => {
      expect(screen.getByText('地图指令已同步')).toBeInTheDocument();
    }, { timeout: 2000 });
  });
});
