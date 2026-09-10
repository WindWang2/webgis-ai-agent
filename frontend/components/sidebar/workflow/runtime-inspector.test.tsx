import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RuntimeInspector } from './runtime-inspector';
import type { RuntimeInstance } from './runtime-inspector';

const instance: RuntimeInstance = {
  instance_id: 'wi-1',
  package_id: 'recipe-x',
  package_version: '1.0.0',
  status: 'succeeded',
  methodology_family: 'proximity',
  counts: { SUCCEEDED: 3 },
  nodes: [
    { node_id: 'data:subject', state: 'SUCCEEDED', attempts: 0, error_code: '', reused: false, binding_violations: [] },
    {
      node_id: 'transform:buffer:subject',
      state: 'SUCCEEDED',
      attempts: 2,
      error_code: '',
      reused: true,
      binding_violations: [],
    },
    {
      node_id: 'cap:blocked',
      state: 'BLOCKED',
      attempts: 1,
      error_code: '',
      reused: false,
      binding_violations: ['CRS_CLASS_MISMATCH'],
    },
  ],
  explain: {
    why_recomputed: ['决策 abc：参数 distance 变更'],
    why_reused: ['决策 abc：输入指纹一致，复用'],
    blocked: [{ node: 'cap:blocked', codes: ['CRS_CLASS_MISMATCH'] }],
  },
};

describe('RuntimeInspector', () => {
  it('renders node states, reuse badge and explanations from the projection', async () => {
    const fetcher = vi.fn().mockResolvedValue(instance);
    render(<RuntimeInspector instanceId="wi-1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId('wf-status')).toHaveTextContent('succeeded'));
    expect(screen.getByTestId('wf-node-data:subject')).toHaveTextContent('SUCCEEDED');
    expect(screen.getByTestId('wf-node-cap:blocked')).toHaveTextContent('BLOCKED');
    expect(screen.getByText('复用')).toBeInTheDocument();
    expect(screen.getByText(/参数 distance 变更/)).toBeInTheDocument();
    expect(screen.getByText(/CRS_CLASS_MISMATCH/)).toBeInTheDocument();
  });

  it('shows error notice when the fetcher rejects', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('boom'));
    render(<RuntimeInspector instanceId="wi-1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByText('运行时实例加载失败')).toBeInTheDocument());
  });

  it('shows empty state for a node-less instance', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ...instance, nodes: [] });
    render(<RuntimeInspector instanceId="wi-1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByText('无节点')).toBeInTheDocument());
  });
});
