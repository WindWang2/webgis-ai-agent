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

  // 审计 a11y：error 状态现在渲染 sr-only 状态提示而非返回 null，
  // 避免把渲染失败藏起来。辅助技术可探测到 role="status"。
  it('renders accessible error hint for empty content', async () => {
    render(<MapActionRenderer content="" />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
    expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
  });

  it('renders accessible error hint for "undefined" content', async () => {
    render(<MapActionRenderer content="undefined" />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    });
  });

  it('dispatches action for bare JSON with command', async () => {
    // FE-03 修复：balanced brace matching 现在能正确捕获嵌套 JSON
    render(<MapActionRenderer content=' {"command":"fly_to","params":{"center":[116.4,39.9]}} ' />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledWith({
        command: 'fly_to',
        params: { center: [116.4, 39.9] },
      });
    }, { timeout: 2000 });
  });

  it('dispatches action for JSON in markdown code fence', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"add_layer","params":{"id":"layer-1","name":"test"}}\n```'} />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledWith({
        command: 'add_layer',
        params: { id: 'layer-1', name: 'test' },
      });
    }, { timeout: 2000 });
  });

  it('dispatches multiple JSON blocks', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"fly_to","params":{"center":[116.4,39.9]}}\n```\n```json\n{"command":"add_layer","params":{"id":"l2"}}\n```'} />);
    await waitFor(() => {
      expect(dispatchAction).toHaveBeenCalledTimes(2);
    }, { timeout: 2000 });
  });

  it('skips JSON without command field', async () => {
    render(<MapActionRenderer content='{"no_command":true}' />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('skips invalid JSON blocks', async () => {
    render(<MapActionRenderer content='not json at all' />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    });
  });

  // FE-04: per-command params schema validation
  it('rejects action with missing required params (add_layer without id)', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"add_layer","params":{"name":"no-id"}}\n```'} />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    }, { timeout: 2000 });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('rejects action with wrong-type params (fly_to center not array)', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"fly_to","params":{"center":"not-array"}}\n```'} />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    }, { timeout: 2000 });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('rejects unknown command even with valid params', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"delete_everything","params":{}}\n```'} />);
    await waitFor(() => {
      expect(screen.getByText('地图指令解析失败')).toBeInTheDocument();
    }, { timeout: 2000 });
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('shows success status after parsing', async () => {
    render(<MapActionRenderer content={'```json\n{"command":"fly_to","params":{"center":[116.4,39.9]}}\n```'} />);
    await waitFor(() => {
      expect(screen.getByText('地图指令已同步')).toBeInTheDocument();
    }, { timeout: 2000 });
  });
});
