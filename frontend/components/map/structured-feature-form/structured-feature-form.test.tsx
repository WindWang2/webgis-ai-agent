/**
 * StructuredFeatureForm 单测 —— 合同语义的表单不变量：
 * - enum select 占位项 disabled，绝不自动选首个候选值；
 * - 数值空串不是 0（missing 不发射；解析失败 per-field 错误）；
 * - null（显式开关）与 missing（未触碰）严格分离；
 * - datetime 必须带时区偏移（Z / ±HH:MM；小数秒 ≤6 位）；
 * - 非 null default 初始化控件并披露「(default)」，不覆盖 initialProps。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { act } from 'react';

import { StructuredFeatureForm, type StructuredFeatureFormHandle } from './index';
import type { FieldContractDocument } from '@/lib/types/field-contracts';
import { createRef } from 'react';

function doc(fields: FieldContractDocument['fields']): FieldContractDocument {
  return { version: 1, title: 't', fields, cross_rules: [] };
}

describe('enum select 语义', () => {
  it('占位项 disabled 且不自动选中首个枚举值（missing 不发射）', async () => {
    const onChange = vi.fn();
    const rules = doc([
      { field: 'category', type: 'string', required: false, nullable: false, empty_string: 'reject', enum: ['A', 'B'] },
    ]);
    render(<StructuredFeatureForm rules={rules} onChange={onChange} />);
    const select = screen.getByTestId('sff-input-category') as HTMLSelectElement;
    expect(select.value).toBe('');
    const placeholder = select.options[0] as HTMLOptionElement;
    expect(placeholder.disabled).toBe(true);
    // 未选择 = missing：初始发射为空对象（无 category 键）
    expect(onChange).toHaveBeenLastCalledWith({});

    await userEvent.selectOptions(select, 'B');
    expect(onChange).toHaveBeenLastCalledWith({ category: 'B' });
  });
});

describe('数值输入语义', () => {
  it('空串不折算为 0：键入后清空 = missing（键缺席）', async () => {
    const onChange = vi.fn();
    const rules = doc([
      { field: 'height', type: 'number', required: false, nullable: false, empty_string: 'reject' },
    ]);
    render(<StructuredFeatureForm rules={rules} onChange={onChange} />);
    const input = screen.getByTestId('sff-input-height');
    await userEvent.type(input, '3.5');
    expect(onChange).toHaveBeenLastCalledWith({ height: 3.5 });
    await userEvent.clear(input);
    expect(onChange).toHaveBeenLastCalledWith({});
    // 中间键入态保留原文（不强制格式化 / 不 coerce）
    await userEvent.type(input, '12.');
    expect((input as HTMLInputElement).value).toBe('12.');
    expect(onChange).toHaveBeenLastCalledWith({}); // 解析失败不发射数值
  });

  it('非数字文本 → per-field 错误 + validate() 失败', async () => {
    const onChange = vi.fn();
    const ref = createRef<StructuredFeatureFormHandle>();
    const rules = doc([
      { field: 'count', type: 'integer', required: true, nullable: false, empty_string: 'reject' },
    ]);
    render(<StructuredFeatureForm ref={ref} rules={rules} onChange={onChange} />);
    await userEvent.type(screen.getByTestId('sff-input-count'), 'abc');
    expect(screen.getByTestId('sff-error-count')).toBeTruthy();
    expect(ref.current?.validate()).toBe(false);
    // 缺失必填也报错（空串 = missing = required 违规）
    await userEvent.clear(screen.getByTestId('sff-input-count'));
    expect(ref.current?.validate()).toBe(false);
  });
});

describe('null 与 missing 的区分', () => {
  it('nullable 字段：显式 null 发射 null；未触碰字段缺席', async () => {
    const onChange = vi.fn();
    const rules = doc([
      { field: 'note', type: 'string', required: false, nullable: true, empty_string: 'allow' },
      { field: 'tag', type: 'string', required: false, nullable: false, empty_string: 'allow' },
    ]);
    render(<StructuredFeatureForm rules={rules} onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith({}); // 初始全 missing
    await userEvent.click(screen.getByTestId('sff-null-note'));
    expect(onChange).toHaveBeenLastCalledWith({ note: null });
    await userEvent.type(screen.getByTestId('sff-input-tag'), 'x');
    expect(onChange).toHaveBeenLastCalledWith({ note: null, tag: 'x' });
    // 关闭 null 开关回到 missing（不是空串）
    await userEvent.click(screen.getByTestId('sff-null-note'));
    expect(onChange).toHaveBeenLastCalledWith({ tag: 'x' });
  });

  it('非 nullable 字段不渲染「设为 null」开关；显式 null 后必填不再缺值', async () => {
    const onChange = vi.fn();
    const ref = createRef<StructuredFeatureFormHandle>();
    const rules = doc([
      { field: 'a', type: 'string', required: false, nullable: false, empty_string: 'reject' },
      { field: 'b', type: 'number', required: true, nullable: true, empty_string: 'reject' },
    ]);
    render(<StructuredFeatureForm ref={ref} rules={rules} onChange={onChange} />);
    expect(screen.queryByTestId('sff-null-a')).toBeNull();
    expect(screen.getByTestId('sff-null-b')).toBeTruthy();
    expect(ref.current?.validate()).toBe(false); // b 必填缺失
    await userEvent.click(screen.getByTestId('sff-null-b'));
    expect(onChange).toHaveBeenLastCalledWith({ b: null });
    expect(ref.current?.validate()).toBe(true); // 显式 null 满足必填（后端语义）
  });
});

describe('datetime 偏移校验', () => {
  it('无偏移 / 偏移格式错 / 小数秒超 6 位均报错；合法值发射原文', async () => {
    const onChange = vi.fn();
    const ref = createRef<StructuredFeatureFormHandle>();
    const rules = doc([
      { field: 'observed_at', type: 'datetime', required: true, nullable: false, empty_string: 'reject' },
    ]);
    render(<StructuredFeatureForm ref={ref} rules={rules} onChange={onChange} />);
    const input = screen.getByTestId('sff-input-observed_at');

    await userEvent.type(input, '2026-01-02T03:04:05'); // 无偏移
    expect(screen.getByTestId('sff-error-observed_at')).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith({}); // 非法值不发射

    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04:05+0800'); // ±HHMM 不接受
    expect(screen.getByTestId('sff-error-observed_at')).toBeTruthy();

    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04:05.1234567+08:00'); // 小数 7 位
    expect(screen.getByTestId('sff-error-observed_at')).toBeTruthy();
    expect(ref.current?.validate()).toBe(false);

    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04:05.123456+08:00');
    expect(screen.queryByTestId('sff-error-observed_at')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({ observed_at: '2026-01-02T03:04:05.123456+08:00' });

    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04:05Z'); // 空格分隔 + Z 同样合法
    expect(screen.queryByTestId('sff-error-observed_at')).toBeNull();
    // R1-P2：秒位必填（与服务端一致）—— 无秒值校验失败且不发射
    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04+08:00');
    expect(ref.current?.validate()).toBe(false);
    expect(onChange).toHaveBeenLastCalledWith({});
    // 恢复合法值 → 整表校验通过
    await userEvent.clear(input);
    await userEvent.type(input, '2026-01-02T03:04:05+08:00');
    expect(ref.current?.validate()).toBe(true);
  });
});

describe('默认值采用披露', () => {
  it('非 null default 初始化控件 + (default) 标记；initialProps 优先且不打标', async () => {
    const onChange = vi.fn();
    const rules = doc([
      { field: 'status', type: 'string', required: false, nullable: false, empty_string: 'reject', default: 'pending' },
      { field: 'level', type: 'integer', required: false, nullable: false, empty_string: 'reject', default: 2 },
      { field: 'owner', type: 'string', required: false, nullable: false, empty_string: 'reject', default: 'bot' },
    ]);
    render(
      <StructuredFeatureForm
        rules={rules}
        initialProps={{ owner: 'human' }}
        onChange={onChange}
      />,
    );
    expect((screen.getByTestId('sff-input-status') as HTMLSelectElement | HTMLInputElement).value).toBe('pending');
    expect((screen.getByTestId('sff-input-level') as HTMLInputElement).value).toBe('2');
    // 「(default)/(默认)」披露标记在场（非空文案，随 locale）
    expect(screen.getByTestId('sff-default-tag-status').textContent || '').not.toBe('');
    expect(screen.getByTestId('sff-default-tag-level').textContent || '').not.toBe('');
    // initialProps 提供的 owner 不被 default 改写，也不打 default 标记
    expect((screen.getByTestId('sff-input-owner') as HTMLInputElement).value).toBe('human');
    expect(screen.queryByTestId('sff-default-tag-owner')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({ status: 'pending', level: 2, owner: 'human' });
    // 用户编辑 default 字段后标记消失
    await userEvent.clear(screen.getByTestId('sff-input-level'));
    await userEvent.type(screen.getByTestId('sff-input-level'), '5');
    expect(screen.queryByTestId('sff-default-tag-level')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({ status: 'pending', level: 5, owner: 'human' });
  });

  it('default:null 不初始化控件（无标记，missing）', () => {
    const onChange = vi.fn();
    const rules = doc([
      { field: 'x', type: 'string', required: false, nullable: true, empty_string: 'allow', default: null },
    ]);
    render(<StructuredFeatureForm rules={rules} onChange={onChange} />);
    expect(screen.queryByTestId('sff-default-tag-x')).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({});
  });
});

describe('imperative handle', () => {
  it('focusFirstError 聚焦首个错误字段（布尔未选占位）', async () => {
    const ref = createRef<StructuredFeatureFormHandle>();
    const rules = doc([
      { field: 'ok', type: 'string', required: false, nullable: false, empty_string: 'reject' },
      { field: 'flag', type: 'boolean', required: true, nullable: false, empty_string: 'reject' },
    ]);
    render(<StructuredFeatureForm ref={ref} rules={rules} onChange={vi.fn()} />);
    expect(ref.current?.validate()).toBe(false);
    let focused = false;
    act(() => { focused = ref.current!.focusFirstError(); });
    expect(focused).toBe(true);
    expect(screen.getByTestId('sff-input-flag')).toHaveFocus();
    // 布尔三态：选 true 后通过
    await userEvent.selectOptions(screen.getByTestId('sff-input-flag') as HTMLSelectElement, 'true');
    expect(ref.current?.validate()).toBe(true);
    expect(ref.current?.getProps()).toEqual({ flag: true });
  });
});
