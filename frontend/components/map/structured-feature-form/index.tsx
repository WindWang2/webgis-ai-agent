'use client';

/**
 * StructuredFeatureForm —— G44 业务字段合同的结构化要素属性表单。
 *
 * 诚实纪律（与合同语义一一对应）：
 * - missing / null / 值 三态显式分离：字段未触碰 = 不发射（absent）；
 *   nullable 字段提供显式「设为 null」开关，null ≠ 空串 ≠ missing；
 * - 数值输入保留中间键入态（空串绝不折算成 0；解析失败给 per-field 错误，
 *   不静默提交）；
 * - enum 用 select 渲染：占位项 disabled 且绝不自动选首个候选值；
 * - datetime 客户端校验 ISO 带时区偏移（Z 或 ±HH:MM，小数秒 ≤6 位）；
 * - create 模式默认值：非 null default 初始化控件并打「(default)」标记；
 *   绝不覆盖 initialProps 已提供的字段值；
 * - onChange 只发射用户（或默认值）设置的字段 —— 部分属性对象。
 *
 * 可访问性：label+id 关联、原生控件键盘可达；父组件经 ref 令牌
 * （focusFirstError）把焦点移到首个错误字段。
 */
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type {
  FieldContractDocument,
  FieldContractFieldRule,
} from '@/lib/types/field-contracts';
import { useT } from '@/lib/i18n/useT';

/** 单字段控件状态（三态 + 中间键入文本）。 */
interface FieldControlState {
  /** 显式 null（仅 nullable 字段可开）。 */
  explicitNull: boolean;
  /** 文本中间态；'' = 未设置（missing）。boolean 用 'true'/'false'。 */
  text: string;
  /** 用户是否动过（错误提示在 touch 后才显示）。 */
  touched: boolean;
  /** 初始值来自合同 default（未编辑时展示「(default)」标记）。 */
  fromDefault: boolean;
}

/** 校验错误键（i18n：fieldContracts.form.errors.*）。 */
type FieldErrorKey = 'required' | 'invalidNumber' | 'invalidInteger' | 'invalidDate' | 'invalidDatetime';

export interface StructuredFeatureFormHandle {
  /** 全量校验（显示所有错误）；返回是否存在错误。 */
  validate(): boolean;
  /** 焦点移到首个错误字段；返回是否找到错误字段。 */
  focusFirstError(): boolean;
  /** 当前发射的属性对象（与最近一次 onChange 一致）。 */
  getProps(): Record<string, unknown>;
}

export interface StructuredFeatureFormProps {
  rules: FieldContractDocument;
  /** 已有属性（create 场景一般为空；提供则优先于 default）。 */
  initialProps?: Record<string, unknown>;
  /** 用户/默认值产生的部分属性对象（absent = missing）。 */
  onChange?: (props: Record<string, unknown>) => void;
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
/** ISO 时刻：日期 + 时分秒（秒必填，与服务端一致）+ 小数秒 ≤6 位 +
 * 必带偏移（Z / ±HH:MM）。 */
const DATETIME_RE =
  /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
/** 数值文本：不允许尾随小数点（'12.' 是中间键入态，不发射）。 */
const NUMBER_RE = /^[+-]?(\d+(\.\d+)?|\.\d+)([eE][+-]?\d+)?$/;
const INTEGER_RE = /^[+-]?\d+$/;

function isNumericType(type: FieldContractFieldRule['type']): boolean {
  return type === 'number' || type === 'integer';
}

/** 控件选择：enum/form_control 优先，其次按 type 推导。 */
function controlKind(rule: FieldContractFieldRule): 'boolean' | 'select' | 'textarea' | 'date' | 'text' | 'number' {
  if (rule.type === 'boolean') return 'boolean';
  if (rule.enum?.length) return 'select';
  switch (rule.form_control) {
    case 'textarea': return 'textarea';
    case 'select': return rule.enum?.length ? 'select' : 'text';
    case 'date': return 'date';
    case 'text': return 'text';
    default: break;
  }
  switch (rule.type) {
    case 'number': case 'integer': return 'number';
    case 'date': return 'date';
    case 'datetime': return 'text'; // 期望带偏移的 ISO 文本（见校验）
    default: return 'text';
  }
}

function stateToValue(
  rule: FieldContractFieldRule,
  state: FieldControlState,
): unknown | undefined {
  if (state.explicitNull) return null;
  const text = state.text;
  if (text === '') return undefined; // missing：不发射
  switch (rule.type) {
    case 'number': return NUMBER_RE.test(text) ? Number(text) : undefined;
    case 'integer': return INTEGER_RE.test(text) ? Number(text) : undefined;
    case 'boolean': return text === 'true' ? true : text === 'false' ? false : undefined;
    case 'date': return DATE_RE.test(text) ? text : undefined;
    case 'datetime': return DATETIME_RE.test(text) ? text : undefined;
    default: return text;
  }
}

function validateField(
  rule: FieldContractFieldRule,
  state: FieldControlState,
): FieldErrorKey | null {
  if (state.explicitNull) return null; // 显式 null 不再要求取值
  const text = state.text;
  if (text === '') return rule.required ? 'required' : null;
  switch (rule.type) {
    case 'number': return NUMBER_RE.test(text) ? null : 'invalidNumber';
    case 'integer': return INTEGER_RE.test(text) ? null : 'invalidInteger';
    case 'date': return DATE_RE.test(text) ? null : 'invalidDate';
    case 'datetime': return DATETIME_RE.test(text) ? null : 'invalidDatetime';
    default: return null;
  }
}

function buildStates(
  rules: FieldContractDocument,
  initialProps?: Record<string, unknown>,
): Record<string, FieldControlState> {
  const out: Record<string, FieldControlState> = {};
  for (const rule of rules.fields) {
    const provided = initialProps ? initialProps[rule.field] : undefined;
    if (provided !== undefined) {
      // initialProps 优先：绝不被 default 静默改写
      if (provided === null) {
        out[rule.field] = { explicitNull: true, text: '', touched: true, fromDefault: false };
      } else if (typeof provided === 'boolean') {
        out[rule.field] = { explicitNull: false, text: provided ? 'true' : 'false', touched: true, fromDefault: false };
      } else {
        out[rule.field] = { explicitNull: false, text: String(provided), touched: true, fromDefault: false };
      }
      continue;
    }
    if (rule.default !== null && rule.default !== undefined) {
      const d = rule.default;
      out[rule.field] = {
        explicitNull: false,
        text: typeof d === 'boolean' ? (d ? 'true' : 'false') : String(d),
        touched: false,
        fromDefault: true, // 「(default)」披露：表单初始化即采用
      };
      continue;
    }
    out[rule.field] = { explicitNull: false, text: '', touched: false, fromDefault: false };
  }
  return out;
}

function buildProps(
  rules: FieldContractDocument,
  states: Record<string, FieldControlState>,
): Record<string, unknown> {
  const props: Record<string, unknown> = {};
  for (const rule of rules.fields) {
    const state = states[rule.field];
    if (!state) continue;
    const value = stateToValue(rule, state);
    if (value !== undefined) props[rule.field] = value;
  }
  return props;
}

/** 结构化要素属性表单（create 场景主输入；提交前经 validate/focusFirstError）。 */
export const StructuredFeatureForm = forwardRef<StructuredFeatureFormHandle, StructuredFeatureFormProps>(
  function StructuredFeatureForm({ rules, initialProps, onChange }, ref) {
    const t = useT('fieldContracts');
    const uid = useId();
    const [states, setStates] = useState<Record<string, FieldControlState>>(() =>
      buildStates(rules, initialProps));
    /** validate() 触发全量错误显示（未 touch 字段也显示）。 */
    const [showAllErrors, setShowAllErrors] = useState(false);
    const statesRef = useRef(states);
    statesRef.current = states;
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;
    const rulesRef = useRef(rules);
    rulesRef.current = rules;

    // 合同文档变化（换图层/换合同）→ 重建控件状态
    useEffect(() => {
      setStates(buildStates(rules, initialProps));
      setShowAllErrors(false);
    }, [rules, initialProps]);

    // 状态 → props 发射（含挂载时的 default 采用）
    useEffect(() => {
      onChangeRef.current?.(buildProps(rulesRef.current, states));
    }, [states]);

    useImperativeHandle(ref, () => ({
      validate: () => {
        setShowAllErrors(true);
        return !rulesRef.current.fields.some((r) => {
          const s = statesRef.current[r.field];
          return s ? validateField(r, s) !== null : r.required;
        });
      },
      focusFirstError: () => {
        const first = rulesRef.current.fields.find((r) => {
          const s = statesRef.current[r.field];
          return s ? validateField(r, s) !== null : r.required;
        });
        if (!first) return false;
        setShowAllErrors(true);
        const el = document.getElementById(fieldId(uid, first.field));
        if (el) { el.focus(); return true; }
        return false;
      },
      getProps: () => buildProps(rulesRef.current, statesRef.current),
    }), [uid]);

    const patch = useCallback((field: string, next: Partial<FieldControlState>) => {
      setStates((prev) => ({
        ...prev,
        [field]: { ...prev[field], ...next, fromDefault: false },
      }));
    }, []);

    const errorOf = (rule: FieldContractFieldRule): FieldErrorKey | null => {
      const s = states[rule.field];
      if (!s) return rule.required ? 'required' : null;
      if (!s.touched && !showAllErrors) return null;
      return validateField(rule, s);
    };

    return (
      <div className="space-y-1.5" data-testid="structured-feature-form">
        {rules.fields.map((rule) => {
          const state = states[rule.field] ?? { explicitNull: false, text: '', touched: false, fromDefault: false };
          const id = fieldId(uid, rule.field);
          const error = errorOf(rule);
          const kind = controlKind(rule);
          const disabled = state.explicitNull;
          const labelText = rule.label || rule.field;
          return (
            <div key={rule.field} className="grid grid-cols-[7.5rem_1fr] items-start gap-1.5">
              <label htmlFor={id} className="pt-0.5 text-right text-micro text-ink-secondary">
                {labelText}
                {rule.required && (
                  <span className="pl-0.5 text-status-error" aria-hidden>*</span>
                )}
                {rule.required && <span className="sr-only">{t('form.requiredAria')}</span>}
                {state.fromDefault && (
                  <span className="pl-1 text-micro text-ink-muted" data-testid={`sff-default-tag-${rule.field}`}>
                    {t('form.defaultTag')}
                  </span>
                )}
              </label>
              <div className="min-w-0 space-y-0.5">
                {kind === 'boolean' ? (
                  // 三态布尔：unset（占位 disabled）/ false / true；null 走显式开关
                  <select
                    id={id}
                    value={state.text}
                    disabled={disabled}
                    onChange={(e) => patch(rule.field, { text: e.target.value, touched: true })}
                    data-testid={`sff-input-${rule.field}`}
                    className="w-full rounded-xs border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro disabled:opacity-50"
                  >
                    <option value="" disabled>{t('form.placeholder')}</option>
                    <option value="true">{t('form.booleanTrue')}</option>
                    <option value="false">{t('form.booleanFalse')}</option>
                  </select>
                ) : kind === 'select' ? (
                  <select
                    id={id}
                    value={state.text}
                    disabled={disabled}
                    onChange={(e) => patch(rule.field, { text: e.target.value, touched: true })}
                    data-testid={`sff-input-${rule.field}`}
                    className="w-full rounded-xs border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro disabled:opacity-50"
                  >
                    {/* 占位项 disabled：绝不自动选中首个枚举值 */}
                    <option value="" disabled>{t('form.placeholder')}</option>
                    {(rule.enum ?? []).map((v) => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                ) : kind === 'textarea' ? (
                  <textarea
                    id={id}
                    value={state.text}
                    disabled={disabled}
                    rows={2}
                    onChange={(e) => patch(rule.field, { text: e.target.value, touched: true })}
                    data-testid={`sff-input-${rule.field}`}
                    className="w-full rounded-xs border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro disabled:opacity-50"
                  />
                ) : (
                  <input
                    id={id}
                    type={kind === 'number' ? 'text' : kind === 'date' ? 'date' : 'text'}
                    inputMode={isNumericType(rule.type) ? 'decimal' : undefined}
                    value={state.text}
                    disabled={disabled}
                    placeholder={rule.type === 'datetime' ? '2026-01-02T03:04:05+08:00' : undefined}
                    onChange={(e) => patch(rule.field, { text: e.target.value, touched: true })}
                    aria-invalid={error ? true : undefined}
                    data-testid={`sff-input-${rule.field}`}
                    className="w-full rounded-xs border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro disabled:opacity-50"
                  />
                )}
                <div className="flex items-center justify-between gap-1.5">
                  {error ? (
                    <p className="text-micro text-status-error" data-testid={`sff-error-${rule.field}`} role="alert">
                      {t(`form.errors.${error}`)}
                    </p>
                  ) : (
                    <p className="text-micro text-ink-muted truncate">
                      {rule.description || (rule.unit ? `${t('form.unitPrefix')} ${rule.unit}` : '')}
                    </p>
                  )}
                  {rule.nullable && (
                    <label className="flex shrink-0 items-center gap-1 text-micro text-ink-muted">
                      <input
                        type="checkbox"
                        checked={state.explicitNull}
                        onChange={(e) => patch(rule.field, { explicitNull: e.target.checked, touched: true })}
                        data-testid={`sff-null-${rule.field}`}
                        className="h-3 w-3"
                      />
                      {t('form.setNull')}
                    </label>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  },
);

function fieldId(uid: string, field: string): string {
  return `sff-${uid}-${field}`;
}

export default StructuredFeatureForm;
