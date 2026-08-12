import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ComparePanel } from './compare-panel';
import type { RunComparison } from '@/lib/api/project';

const base: RunComparison = {
  run_a_id: 'a',
  run_b_id: 'b',
  revision: { graph_same: true, run_a_revision: 'r', run_b_revision: 'r' },
  inputs_changed: { diff_keys: [] },
  dataset_versions_changed: { diff_keys: [] },
  tool_versions_changed: {},
  params_changed: {},
  output_artifacts_changed: {},
  metrics_changed: {},
  warnings_changed: {},
  run_fingerprint: { run_a: 'x', run_b: 'x', same: false },
};

describe('ComparePanel', () => {
  it('does not treat matching display fields as fingerprint equality', () => {
    render(
      <ComparePanel
        runs={[
          { id: 'a', workflow_id: 'w', workflow_version: 1, status: 'completed', created_at: '' },
          { id: 'b', workflow_id: 'w', workflow_version: 1, status: 'completed', created_at: '' },
        ]}
        selectedRunId="a"
        peerId="b"
        onPeerChange={vi.fn()}
        onCompare={vi.fn()}
        result={base}
      />,
    );
    expect(screen.getByText('后端判定运行指纹不相同')).toBeInTheDocument();
    expect(screen.queryByText('后端判定运行指纹相同')).not.toBeInTheDocument();
  });
});
