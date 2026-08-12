import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LineageList } from './lineage-list';

describe('LineageList', () => {
  it('does not fetch until the user asks', () => {
    const onLoad = vi.fn();
    render(<LineageList artifactId="a1" state={undefined} onLoad={onLoad} />);
    expect(onLoad).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '加载血统' }));
    expect(onLoad).toHaveBeenCalledWith('a1');
  });

  it('renders empty lineage without fabricating nodes', () => {
    render(
      <LineageList
        artifactId="a1"
        artifactCrs={null}
        state={{ artifact_id: 'a1', parents: [], consumers: [] }}
        onLoad={vi.fn()}
      />,
    );
    expect(screen.getByText('无血统')).toBeInTheDocument();
  });
});
