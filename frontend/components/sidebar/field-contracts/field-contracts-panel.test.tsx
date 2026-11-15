/**
 * FieldContractsPanel 冒烟：无会话时面板渲染诚实提示（不发起任何请求）；
 * 结构化表单冒烟在 structured-feature-form.test.tsx。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { FieldContractsPanel } from './field-contracts-panel';
import { useFieldContractsPanelStore } from '@/lib/hooks/use-field-contracts';

describe('FieldContractsPanel', () => {
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
});
