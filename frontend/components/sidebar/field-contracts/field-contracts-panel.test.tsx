/**
 * FieldContractsPanel 冒烟：无会话时面板渲染诚实提示（不发起任何请求）；
 * 结构化表单冒烟在 structured-feature-form.test.tsx。
 *
 * 草稿保存闸门（评审 P2）：buildDocument 的 typed 校验错误码必须如实展示
 * （字段名为空 / 删光字段），且新增跨字段规则的 rule_id 不得与剩余项重名。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { FieldContractsPanel } from './field-contracts-panel';
import { useFieldContractsPanelStore } from '@/lib/hooks/use-field-contracts';

const mockSaveDraft = vi.hoisted(() => vi.fn());
const mockError = vi.hoisted(() => ({ current: null as null | { code: string; message: string } }));

vi.mock('@/lib/hooks/use-field-contracts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/use-field-contracts')>();
  return {
    ...actual,
    useFieldContracts: () => ({
      binding: null,
      observe: null,
      contracts: [],
      draft: null,
      active: null,
      history: [],
      overviewLoading: false,
      refresh: async () => {},
      saving: false,
      saveDraft: mockSaveDraft,
      assessment: null,
      assessing: false,
      runAssessment: async () => {},
      findings: null,
      findingsLoading: false,
      codeFilter: '',
      setCodeFilter: () => {},
      loadFindingsPage: async () => {},
      nextFindingsPage: async () => {},
      prevFindingsPage: async () => {},
      hasPrevFindingsPage: false,
      canActivate: false,
      activateGateKey: null,
      activateGateParams: null,
      activating: false,
      activate: async () => null,
      retiring: false,
      retire: async () => null,
      batchRunning: false,
      batchResult: null,
      runBatchFill: async () => null,
      error: mockError.current,
      clearError: () => {},
    }),
  };
});

function openPanel() {
  useFieldContractsPanelStore.getState().openWith('ref:data-1');
  return render(<FieldContractsPanel sessionId="s1" ownerToken={null} />);
}

function crossRuleIds(): string[] {
  return screen.getAllByLabelText('rule_id').map((el) => (el as HTMLInputElement).value);
}

describe('FieldContractsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockError.current = null;
    useFieldContractsPanelStore.getState().close();
  });

  it('无 sessionId：渲染面板 + noSession 提示，不发请求', () => {
    useFieldContractsPanelStore.getState().openWith();
    render(<FieldContractsPanel sessionId={null} ownerToken={null} />);
    expect(screen.getByTestId('field-contracts-panel')).toBeTruthy();
    expect(screen.getByTestId('fc-ref-input')).toBeTruthy();
    expect(screen.getByText('无活动会话 —— 请先开启对话再使用合同工作台')).toBeTruthy();
    useFieldContractsPanelStore.getState().close();
  });

  it('关闭态返回 null（单例面板开关）', () => {
    const { container } = render(<FieldContractsPanel sessionId="s1" ownerToken={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('字段名为空时保存草稿：展示 typed 校验码，不发保存请求', async () => {
    openPanel();
    fireEvent.click(screen.getByTestId('fc-save-draft'));
    await waitFor(() => expect(screen.getByTestId('fc-draft-error')).toBeTruthy());
    expect(screen.getByTestId('fc-draft-error').textContent).toContain('field_name_required');
    expect(mockSaveDraft).not.toHaveBeenCalled();
  });

  it('删光字段后保存草稿：展示 no_fields，不发保存请求', async () => {
    openPanel();
    fireEvent.click(screen.getByTestId('fc-field-0').querySelector('button')!);
    fireEvent.click(screen.getByTestId('fc-save-draft'));
    await waitFor(() => expect(screen.getByTestId('fc-draft-error')).toBeTruthy());
    expect(screen.getByTestId('fc-draft-error').textContent).toContain('no_fields');
    expect(mockSaveDraft).not.toHaveBeenCalled();
  });

  it('删掉任一跨字段规则后再新增：rule_id 不与剩余项重名', () => {
    openPanel();
    for (let i = 0; i < 3; i += 1) {
      fireEvent.click(screen.getByTestId('fc-add-cross'));
    }
    expect(crossRuleIds()).toEqual(['rule_1', 'rule_2', 'rule_3']);
    // 删掉第一条 → 剩余 rule_2 / rule_3
    fireEvent.click(screen.getByTestId('fc-cross-0').querySelector('button')!);
    expect(crossRuleIds()).toEqual(['rule_2', 'rule_3']);
    // 再新增：必须是未占用的 rule_4（旧实现按长度编号会得到 rule_3 → 重名）
    fireEvent.click(screen.getByTestId('fc-add-cross'));
    expect(crossRuleIds()).toEqual(['rule_2', 'rule_3', 'rule_4']);
  });

  it('批量填值错误文案格式漂移：显示原始 message，不静默显示 0', () => {
    mockError.current = { code: 'selection_too_large', message: '超过上限（无法解析为数字）' };
    openPanel();
    expect(screen.getByTestId('fc-batch-error').textContent).toContain('超过上限（无法解析为数字）');
  });
});
