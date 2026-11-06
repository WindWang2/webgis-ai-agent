import { useRef, useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SearchField } from '@/components/shared/search-field';
import { useDialogFocus } from './use-dialog-focus';

function Dialog({ onClose, open = true }: { onClose: () => void; open?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('人口');
  useDialogFocus({ open, containerRef: ref, onEscape: onClose });
  return <div role="dialog" ref={ref}><SearchField value={query} onChange={setQuery} aria-label="Search" /><button>Last</button></div>;
}

describe('dialog keyboard integration', () => {
  it('lets SearchField clear its nonempty query before a second Escape closes the dialog', async () => {
    const close = vi.fn();
    render(<Dialog onClose={close} />);
    const input = screen.getByRole('searchbox');
    input.focus();
    await userEvent.keyboard('{Escape}');
    expect(input).toHaveValue('');
    expect(close).not.toHaveBeenCalled();
    await userEvent.keyboard('{Escape}');
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('retains capture Tab trapping even when focus starts outside the dialog', () => {
    render(<Dialog onClose={vi.fn()} />);
    fireEvent.keyDown(document.body, { key: 'Tab' });
    expect(screen.getByRole('searchbox')).toHaveFocus();
  });

  it('prevents native search cancel while consuming a nonempty Escape', () => {
    const close = vi.fn();
    render(<Dialog onClose={close} />);
    const input = screen.getByRole('searchbox');
    expect(fireEvent.keyDown(input, { key: 'Escape', cancelable: true })).toBe(false);
    expect(input).toHaveValue('');
    expect(close).not.toHaveBeenCalled();
  });

  it('only closes the most recently opened modal', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<><Dialog onClose={first} /><Dialog open={false} onClose={second} /></>);
    act(() => rerender(<><Dialog onClose={first} /><Dialog open onClose={second} /></>));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
