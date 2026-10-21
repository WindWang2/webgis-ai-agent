/**
 * #1747 项 5：SOURCE_TYPES 键名错配致空白下拉项。
 *
 * postgis 条目曾写成 `labelKey: 'ds.type.postgis'` 而渲染处只读 `label`
 * —— 「PostGIS 数据库」在下拉里渲染成完全空白的 option（且该键缺
 * `sidebar.` 前缀，即便读 labelKey 也解析不到）。本测试锁定：
 *   1. 每个 option 都有非空可见文案（空白项回归）；
 *   2. postgis 项走 i18n catalog（zh-CN/en-US 两侧都有键）；
 *   3. option value 仍是后端契约的 `postgis`（提交不被键修改变形）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nProvider } from '@/lib/i18n/i18n-provider';
import { AddSourceForm } from './add-source-form';

const toastStore = { addToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

vi.mock('@/lib/api/data-fabric', () => ({
  dataFabricApi: { createDataSource: vi.fn() },
}));

import { dataFabricApi } from '@/lib/api/data-fabric';

beforeEach(() => {
  vi.clearAllMocks();
  // 固定 locale：I18nProvider 初值读 window.__GEOAGENT_LOCALE__（jsdom 缺省
  // 即 zh-CN，显式钉死防 ambient 漂移）。
  (window as unknown as Record<string, unknown>).__GEOAGENT_LOCALE__ = 'zh-CN';
  window.localStorage.clear();
});

function renderForm() {
  return render(
    <I18nProvider>
      <AddSourceForm onCreated={() => {}} />
    </I18nProvider>,
  );
}

describe('AddSourceForm 协议下拉（#1747）', () => {
  it('postgis 项渲染本地化 label，无空白 option', () => {
    renderForm();
    const select = screen.getByLabelText('协议类型') as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll('option'));
    expect(options.length).toBe(7);
    // 任何 option 都不得渲染成空白（键名错配的既有故障形态）
    for (const opt of options) {
      expect(opt.textContent?.trim() ?? '', `option value=${opt.value} 文案为空`).not.toBe('');
    }
    const postgis = options.find((o) => o.value === 'postgis');
    expect(postgis).toBeDefined();
    expect(postgis!.textContent).toBe('PostGIS 数据库');
  });

  it('选中 postgis 提交 → createDataSource 收 source_type=postgis（value 未被键修改变形）', async () => {
    vi.mocked(dataFabricApi.createDataSource).mockResolvedValue(undefined as never);
    renderForm();
    fireEvent.change(screen.getByLabelText('数据源名称'), { target: { value: 'pg-1' } });
    fireEvent.change(screen.getByLabelText('Endpoint URL / 连接地址'), {
      target: { value: 'postgres://db' },
    });
    fireEvent.change(screen.getByLabelText('协议类型'), { target: { value: 'postgis' } });
    fireEvent.click(screen.getByText('提交注册并同步'));
    await waitFor(() =>
      expect(dataFabricApi.createDataSource).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'pg-1', source_type: 'postgis' }),
      ),
    );
  });
});
