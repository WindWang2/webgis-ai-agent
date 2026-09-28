/**
 * DatasetManager 真实上传入口（#1555）：文件选择器 → uploadFile → ref 挂载。
 *
 * 合同：
 * - source_type=upload 时渲染真实 file input（不是提示文案）；
 * - 选择文件即触发 uploadFile（POST /api/v1/upload 的唯一客户端入口），
 *   完成前确认按钮禁用（不完整上传不得挂载）；
 * - 上传成功后以返回 ref（session_ref ?? id）挂载，无需用户手抄。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastStore = { toasts: [], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

const uploadApi = vi.hoisted(() => ({ uploadFile: vi.fn() }));
vi.mock('@/lib/api/upload', () => uploadApi);

const assetsHook = vi.hoisted(() => ({
  useProjectDatasets: vi.fn(),
  datasetDependencyWarning: vi.fn(() => ''),
}));
vi.mock('@/lib/hooks/use-project-assets', () => assetsHook);

import { DatasetManager } from './project/dataset-manager';

function makeDs() {
  return {
    datasets: [],
    total: 0,
    loading: false,
    busyId: null as string | null,
    error: null as string | null,
    schemaByDataset: {} as Record<string, unknown>,
    hasMore: false,
    attach: vi.fn(async (req: { name: string; source_type: string; source_ref?: string; crs?: string }) => ({
      id: 'ds-1',
      project_id: 'p1',
      name: req.name,
      source_type: req.source_type,
      source_ref: req.source_ref ?? null,
      crs: req.crs ?? null,
      quality_status: 'unchecked',
      created_at: '2026-01-01T08:30:00Z',
    })),
    detach: vi.fn(),
    reload: vi.fn(),
    loadMore: vi.fn(),
  };
}

function makeFile(name = 'schools.geojson'): File {
  return new File(['{"type":"FeatureCollection","features":[]}'], name, {
    type: 'application/geo+json',
  });
}

function renderManager() {
  const ds = makeDs();
  assetsHook.useProjectDatasets.mockReturnValue(ds);
  render(<DatasetManager projectId="p1" authed />);
  return ds;
}

describe('DatasetManager upload entry (#1555)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    uploadApi.uploadFile.mockResolvedValue({
      id: 7,
      original_name: 'schools.geojson',
      file_type: 'vector',
      format: 'geojson',
      crs: 'EPSG:4326',
      geometry_type: 'Point',
      feature_count: 3,
      bbox: [116.2, 39.7, 116.8, 40.2],
      file_size: 42,
      session_ref: 'ref:upload-7',
    });
  });

  it('upload 档渲染真实文件选择器（不再是"去别处上传"提示）', () => {
    renderManager();
    fireEvent.click(screen.getByRole('button', { name: '挂载数据集' }));
    fireEvent.change(screen.getByLabelText('来源类型'), {
      target: { value: 'upload' },
    });
    expect(screen.getByLabelText('选择数据文件…')).toBeTruthy();
    expect(screen.queryByText(/本面板不重复上传能力/)).toBeNull();
  });

  it('选文件即上传；完成前确认禁用，完成后以返回 ref 挂载', async () => {
    let resolveUpload: (v: unknown) => void = () => {};
    uploadApi.uploadFile.mockImplementation(
      () => new Promise((resolve) => { resolveUpload = resolve; }),
    );
    const ds = renderManager();
    fireEvent.click(screen.getByRole('button', { name: '挂载数据集' }));
    fireEvent.change(screen.getByLabelText('来源类型'), {
      target: { value: 'upload' },
    });
    const confirm = screen.getByRole('button', { name: '确认挂载' });
    expect(confirm.hasAttribute('disabled')).toBe(true);

    fireEvent.change(screen.getByLabelText('选择数据文件…'), {
      target: { files: [makeFile()] },
    });
    await waitFor(() => expect(uploadApi.uploadFile).toHaveBeenCalledTimes(1));
    // 上传进行中：确认仍禁用（不完整上传不得挂载）。
    expect(confirm.hasAttribute('disabled')).toBe(true);

    resolveUpload({
      id: 7, original_name: 'schools.geojson', session_ref: 'ref:upload-7',
    });
    await waitFor(() =>
      expect(screen.getByTestId('dataset-upload-done')).toBeTruthy());
    expect(confirm.hasAttribute('disabled')).toBe(false);

    fireEvent.click(confirm);
    await waitFor(() => expect(ds.attach).toHaveBeenCalledTimes(1));
    expect(ds.attach).toHaveBeenCalledWith(
      expect.objectContaining({ source_type: 'upload', source_ref: 'ref:upload-7' }),
    );
  });

  it('上传失败：错误可见 + 不得挂载（无 ref 不假成功）', async () => {
    uploadApi.uploadFile.mockRejectedValue(new Error('boom 500'));
    const ds = renderManager();
    fireEvent.click(screen.getByRole('button', { name: '挂载数据集' }));
    fireEvent.change(screen.getByLabelText('来源类型'), {
      target: { value: 'upload' },
    });
    fireEvent.change(screen.getByLabelText('选择数据文件…'), {
      target: { files: [makeFile()] },
    });
    await waitFor(() =>
      expect(toastStore.addToast).toHaveBeenCalledWith(
        expect.stringContaining('上传失败'),
        'error',
      ));
    const confirm = screen.getByRole('button', { name: '确认挂载' });
    expect(confirm.hasAttribute('disabled')).toBe(true);
    fireEvent.click(confirm);
    expect(ds.attach).not.toHaveBeenCalled();
  });

  it('切回其他来源类型：上传态清零，确认按钮恢复非 upload 语义', () => {
    renderManager();
    fireEvent.click(screen.getByRole('button', { name: '挂载数据集' }));
    fireEvent.change(screen.getByLabelText('来源类型'), {
      target: { value: 'layer' },
    });
    expect(screen.queryByLabelText('选择数据文件…')).toBeNull();
  });
});
