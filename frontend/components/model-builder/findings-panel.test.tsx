import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { defaultTranslator } from '@/lib/i18n/translator';
import { FindingsPanel } from './findings-panel';
import { cycleFindings } from '@/test/fixtures/model-authoring-fixtures';

function renderPanel(overrides: Partial<Parameters<typeof FindingsPanel>[0]> = {}) {
  const props: Parameters<typeof FindingsPanel>[0] = {
    findings: [],
    valid: null,
    fingerprint: '',
    validating: false,
    publishing: false,
    publishedVersion: null,
    publishError: null,
    onValidate: vi.fn(),
    onPublish: vi.fn(),
    onJumpToNode: vi.fn(),
    ...overrides,
  };
  render(<FindingsPanel {...props} />);
  return props;
}

describe('FindingsPanel', () => {
  it('未验证时显示引导空态；发布按钮禁用', () => {
    renderPanel();
    expect(screen.getByText(defaultTranslator('modelBuilder.findings.empty'))).toBeInTheDocument();
    expect(screen.getByTestId('publish-btn')).toBeDisabled();
  });

  it('typed findings 渲染：severity 色块 + code + 双语 detail 按 locale 取一', () => {
    // 无 provider 裸渲染回落 zh → detail_zh。
    renderPanel({ findings: cycleFindings, valid: false });
    expect(screen.getByText('MODEL_CYCLE')).toBeInTheDocument();
    expect(screen.getByText(defaultTranslator('modelBuilder.findings.invalidNotice', { count: 2 }))).toBeInTheDocument();
    expect(screen.getAllByText('存在环依赖').length).toBeGreaterThan(0);
    // error/warning 色块 token。
    expect(document.querySelector('.bg-status-critical')).not.toBeNull();
  });

  it('node_id 点击 → onJumpToNode（画布选中联动）', () => {
    const onJump = vi.fn();
    renderPanel({ findings: cycleFindings, valid: false, onJumpToNode: onJump });
    fireEvent.click(screen.getAllByRole('button', { name: defaultTranslator('modelBuilder.findings.jumpToNode', { node: 'buffered' }) })[0]);
    expect(onJump).toHaveBeenCalledWith('buffered');
  });

  it('valid=true 启用发布；发布结果渲染新版本号', () => {
    const onPublish = vi.fn();
    renderPanel({ valid: true, publishedVersion: '1.0.0', onPublish });
    expect(screen.getByTestId('publish-btn')).toBeEnabled();
    fireEvent.click(screen.getByTestId('publish-btn'));
    expect(onPublish).toHaveBeenCalledWith('minor');
    expect(screen.getByTestId('publish-result')).toHaveTextContent(
      defaultTranslator('modelBuilder.findings.published', { version: '1.0.0' }),
    );
  });

  it('验证通过无发现 → validNotice；指纹披露', () => {
    renderPanel({ valid: true, findings: [], fingerprint: 'fp_123' });
    expect(screen.getByText(defaultTranslator('modelBuilder.findings.validNotice'))).toBeInTheDocument();
    expect(screen.getByText(defaultTranslator('modelBuilder.findings.fingerprint', { fingerprint: 'fp_123' }))).toBeInTheDocument();
  });

  it('发布错误展示（422 落 findings 由宿主注入）', () => {
    renderPanel({ findings: cycleFindings, valid: false, publishError: '发布失败：MODEL_INVALID' });
    expect(screen.getByText('发布失败：MODEL_INVALID')).toBeInTheDocument();
    expect(screen.getByText('MODEL_CYCLE')).toBeInTheDocument();
  });
});
