import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreviewModal } from './preview-modal';
import type { QueryResult } from '@/lib/api/data-fabric';

describe('PreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockQueryResult: QueryResult = {
    dataset_id: 'cd-schools-2026',
    features: [
      {
        id: 'sch-1',
        name: '成都市实验小学',
        district: '青羊区',
        students: 1800,
        geometry: { type: 'Point', coordinates: [104.055, 30.662] },
      },
      {
        id: 'sch-2',
        name: '成都市泡桐树小学',
        district: '青羊区',
        students: 2100,
        geometry: { type: 'Point', coordinates: [104.048, 30.658] },
      },
    ],
    total_count: 2,
  };

  it('renders modal dialog with accessible title and feature count badge', () => {
    const handleClose = vi.fn();
    render(<PreviewModal result={mockQueryResult} onClose={handleClose} />);

    const dialog = screen.getByRole('dialog', { name: '数据样例预览' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText('数据集: cd-schools-2026')).toBeInTheDocument();
    expect(screen.getByText('共 2 要素')).toBeInTheDocument();
  });

  it('supports switching between 属性表格 and 原始 JSON tabs', async () => {
    const user = userEvent.setup();
    render(<PreviewModal result={mockQueryResult} onClose={vi.fn()} />);

    // Default tab is 属性表格
    const gridTab = screen.getByRole('tab', { name: '属性表格' });
    const jsonTab = screen.getByRole('tab', { name: '原始 JSON' });
    expect(gridTab).toHaveAttribute('aria-selected', 'true');
    expect(jsonTab).toHaveAttribute('aria-selected', 'false');

    // Tabular data grid is visible
    expect(screen.getByText('成都市实验小学')).toBeInTheDocument();

    // Switch to 原始 JSON
    await user.click(jsonTab);
    expect(jsonTab).toHaveAttribute('aria-selected', 'true');
    expect(gridTab).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByText(/格式化 GeoJSON \/ 结果数据/)).toBeInTheDocument();
    expect(screen.getAllByText(/cd-schools-2026/).length).toBeGreaterThan(0);

    // Switch back to 属性表格
    await user.click(gridTab);
    expect(gridTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('成都市实验小学')).toBeInTheDocument();
  });

  it('supports keyboard navigation (ArrowLeft / ArrowRight) across tabs', async () => {
    render(<PreviewModal result={mockQueryResult} onClose={vi.fn()} />);

    const gridTab = screen.getByRole('tab', { name: '属性表格' });
    gridTab.focus();

    fireEvent.keyDown(gridTab, { key: 'ArrowRight' });
    const jsonTab = screen.getByRole('tab', { name: '原始 JSON' });
    expect(jsonTab).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(jsonTab, { key: 'ArrowLeft' });
    expect(gridTab).toHaveAttribute('aria-selected', 'true');
  });

  it('copies full JSON payload when 复制 JSON button is clicked', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextSpy },
      writable: true,
      configurable: true,
    });

    render(<PreviewModal result={mockQueryResult} onClose={vi.fn()} />);

    const copyBtn = screen.getByRole('button', { name: '复制原始 JSON 数据' });
    fireEvent.click(copyBtn);

    expect(writeTextSpy).toHaveBeenCalledTimes(1);
    const copiedText = writeTextSpy.mock.calls[0][0];
    expect(copiedText).toContain('cd-schools-2026');
    expect(copiedText).toContain('成都市实验小学');
    expect(await screen.findByText('已复制')).toBeInTheDocument();
  });

  it('calls onClose when close icon button is clicked or Escape is pressed', async () => {
    const user = userEvent.setup();
    const handleClose = vi.fn();
    render(<PreviewModal result={mockQueryResult} onClose={handleClose} />);

    const closeBtn = screen.getByRole('button', { name: '关闭' });
    await user.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);

    // Escape key on document (useDialogFocus listens on document)
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalledTimes(2);
  });
});
