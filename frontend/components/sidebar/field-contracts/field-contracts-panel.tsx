'use client';

/**
 * G44 业务字段合同工作台面板 —— 声明 / 评估 / 激活 / 批量填值闭环。
 *
 * 用户闭环：数据集绑定 → 观测 vs 声明对照 → 规则草稿（字段 + 跨字段，
 * 客户端上限计数）→ 全量评估（违规分页）→ 激活（零违规 + digest 一致才
 * 放开；确认弹窗披露强制执行影响）→ 退役 / 历史 → 批量填值（G24 selection
 * handle 全集；总数未知 / 不完整 / >200 一律拒绝）。
 * 诚实纪律：observed 词汇只是证据（采用前披露覆盖）；激活闸门不放宽；
 * typed 错误码原样展示（assessment_stale → 引导重评）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileCheck2, PlayCircle, RefreshCw, ShieldCheck, X } from 'lucide-react';

import {
  MAX_BATCH_TARGETS,
  MAX_CROSS_RULES,
  MAX_DRAFT_FIELDS,
  MAX_ENUM_VALUES,
  useFieldContracts,
  useFieldContractsPanelStore,
} from '@/lib/hooks/use-field-contracts';
import type {
  FieldContractConditionOp,
  FieldContractCompareOp,
  FieldContractCrossRule,
  FieldContractDocument,
  FieldContractFieldRule,
  FieldContractFieldType,
} from '@/lib/types/field-contracts';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { useT } from '@/lib/i18n/useT';

interface FieldContractsPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

const FIELD_TYPES: FieldContractFieldType[] = ['string', 'number', 'integer', 'boolean', 'date', 'datetime'];
const CONDITION_OPS: FieldContractConditionOp[] = ['eq', 'ne', 'in', 'not_in', 'gt', 'gte', 'lt', 'lte'];
const COMPARE_OPS: FieldContractCompareOp[] = ['lt', 'lte', 'gt', 'gte'];
const INPUT_CLS = 'rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm text-ink outline-none focus:border-status-accent';
const LABEL_CLS = 'text-caption text-ink-muted';

/** 草稿编辑态：数值/枚举等复合输入以文本维护（空 = 不设置，绝不折算 0）。 */
interface EditableField {
  field: string;
  type: FieldContractFieldType;
  required: boolean;
  nullable: boolean;
  empty_string: 'reject' | 'allow';
  enumText: string;
  minText: string;
  maxText: string;
  exclusive_min: boolean;
  exclusive_max: boolean;
  minLengthText: string;
  maxLengthText: string;
  label: string;
  unit: string;
  description: string;
  defaultText: string;
}

interface EditableCross {
  rule_id: string;
  kind: FieldContractCrossRule['kind'];
  whenField: string;
  whenOp: FieldContractConditionOp;
  whenValueText: string;
  requireText: string;
  leftField: string;
  compareOp: FieldContractCompareOp;
  rightField: string;
  uniqueFieldsText: string;
  nullPolicy: 'ignore' | 'participate';
  note: string;
}

function emptyField(): EditableField {
  return {
    field: '', type: 'string', required: false, nullable: false, empty_string: 'reject',
    enumText: '', minText: '', maxText: '', exclusive_min: false, exclusive_max: false,
    minLengthText: '', maxLengthText: '', label: '', unit: '', description: '', defaultText: '',
  };
}

function emptyCross(ruleId: string): EditableCross {
  return {
    rule_id: ruleId, kind: 'conditional_required',
    whenField: '', whenOp: 'eq', whenValueText: '', requireText: '',
    leftField: '', compareOp: 'lt', rightField: '', uniqueFieldsText: '',
    nullPolicy: 'ignore', note: '',
  };
}

/**
 * 新增跨字段规则的 rule_id：取现有 `rule_<n>` 的最大 n + 1。
 * 以数组长度编号会在「删掉任一规则再新增」时与剩余项重名（如 [r1,r2,r3]
 * 删 r1 后新增得 rule_3）—— 后端按 duplicate rule_id 拒收（评审 P2）。
 */
function nextCrossRuleId(existing: EditableCross[]): string {
  let max = 0;
  for (const c of existing) {
    const m = /^rule_(\d+)$/.exec(c.rule_id.trim());
    if (m) max = Math.max(max, Number(m[1]));
  }
  return `rule_${max + 1}`;
}

function toEditable(rule: FieldContractFieldRule): EditableField {
  return {
    field: rule.field,
    type: rule.type,
    required: rule.required,
    nullable: rule.nullable,
    empty_string: rule.empty_string ?? 'reject',
    enumText: (rule.enum ?? []).join('\n'),
    minText: rule.min != null ? String(rule.min) : '',
    maxText: rule.max != null ? String(rule.max) : '',
    exclusive_min: Boolean(rule.exclusive_min),
    exclusive_max: Boolean(rule.exclusive_max),
    minLengthText: rule.min_length != null ? String(rule.min_length) : '',
    maxLengthText: rule.max_length != null ? String(rule.max_length) : '',
    label: rule.label ?? '',
    unit: rule.unit ?? '',
    description: rule.description ?? '',
    defaultText: rule.default != null ? JSON.stringify(rule.default) : '',
  };
}

function numOrNull(text: string): number | null | undefined {
  const v = text.trim();
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function intOrNull(text: string): number | null | undefined {
  const v = text.trim();
  if (!v) return undefined;
  return /^\d+$/.test(v) ? Number(v) : undefined;
}

function fromEditable(ef: EditableField): FieldContractFieldRule | string {
  if (!ef.field.trim()) return 'field_name_required';
  const rule: FieldContractFieldRule = {
    field: ef.field.trim(),
    type: ef.type,
    required: ef.required,
    nullable: ef.nullable,
    empty_string: ef.empty_string,
  };
  const enumLines = ef.enumText.split('\n').map((s) => s.trim()).filter(Boolean);
  if (enumLines.length) {
    if (enumLines.length > MAX_ENUM_VALUES) return 'enum_too_long';
    rule.enum = enumLines;
  }
  const min = numOrNull(ef.minText);
  const max = numOrNull(ef.maxText);
  if (min !== undefined) rule.min = min;
  if (max !== undefined) rule.max = max;
  if (ef.exclusive_min) rule.exclusive_min = true;
  if (ef.exclusive_max) rule.exclusive_max = true;
  const minLength = intOrNull(ef.minLengthText);
  const maxLength = intOrNull(ef.maxLengthText);
  if (minLength !== undefined) rule.min_length = minLength;
  if (maxLength !== undefined) rule.max_length = maxLength;
  if (ef.label.trim()) rule.label = ef.label.trim();
  if (ef.unit.trim()) rule.unit = ef.unit.trim();
  if (ef.description.trim()) rule.description = ef.description.trim();
  if (ef.defaultText.trim()) {
    try { rule.default = JSON.parse(ef.defaultText); } catch { return 'default_invalid_json'; }
  }
  return rule;
}

function fromEditableCross(ec: EditableCross): FieldContractCrossRule | string {
  const rule: FieldContractCrossRule = { rule_id: ec.rule_id.trim() || `rule_${Date.now()}`, kind: ec.kind };
  if (ec.kind === 'conditional_required') {
    if (!ec.whenField.trim()) return 'cross_when_field_required';
    const when: FieldContractCrossRule['when'] = { field: ec.whenField.trim(), op: ec.whenOp };
    if (ec.whenValueText.trim()) {
      try {
        when.value = ec.whenOp === 'in' || ec.whenOp === 'not_in'
          ? undefined
          : JSON.parse(ec.whenValueText);
      } catch { return 'cross_when_value_invalid'; }
    }
    if (ec.whenOp === 'in' || ec.whenOp === 'not_in') {
      // in/not_in 的多值：行/逗号分隔的 JSON 数组或裸值列表
      const raw = ec.whenValueText.trim();
      if (raw) {
        try {
          const arr = JSON.parse(raw);
          when.values = Array.isArray(arr) ? arr : [arr];
        } catch {
          when.values = raw.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
        }
      }
    }
    rule.when = when;
    const require = ec.requireText.split(',').map((s) => s.trim()).filter(Boolean);
    if (!require.length) return 'cross_require_list_required';
    rule.require = require;
  } else if (ec.kind === 'compare') {
    if (!ec.leftField.trim() || !ec.rightField.trim()) return 'cross_compare_fields_required';
    rule.left = ec.leftField.trim();
    rule.op = ec.compareOp;
    rule.right = ec.rightField.trim();
  } else {
    const fields = ec.uniqueFieldsText.split(',').map((s) => s.trim()).filter(Boolean);
    if (!fields.length || fields.length > 4) return 'cross_unique_fields_invalid';
    rule.fields = fields;
    rule.null_policy = ec.nullPolicy;
  }
  if (ec.note.trim()) rule.note = ec.note.trim();
  return rule;
}

export function FieldContractsPanel({ sessionId, ownerToken }: FieldContractsPanelProps): React.ReactElement | null {
  const t = useT('fieldContracts');
  const open = useFieldContractsPanelStore((s) => s.open);
  const presetRef = useFieldContractsPanelStore((s) => s.presetRef);
  const close = useFieldContractsPanelStore((s) => s.close);

  const [refText, setRefText] = useState('');
  useEffect(() => {
    if (open) setRefText(presetRef ?? '');
  }, [open, presetRef]);

  const fc = useFieldContracts({ sessionId, ownerToken, refId: refText.trim() || null });

  /* ── 草稿编辑器状态（draft 加载时同步；用户编辑后以本地为准） ── */
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [fields, setFields] = useState<EditableField[]>([emptyField()]);
  const [crossRules, setCrossRules] = useState<EditableCross[]>([]);
  const [savedInfo, setSavedInfo] = useState<{ revision: number; digest: string } | null>(null);
  /** 草稿校验失败码（buildDocument 的 typed 拒绝）—— 必须如实展示，绝不静默。 */
  const [draftError, setDraftError] = useState<string | null>(null);
  const editorSyncedRef = useRef<string | null>(null);

  useEffect(() => {
    const draft = fc.draft;
    const key = draft ? `${draft.contract_id}:${draft.revision}` : null;
    if (key && editorSyncedRef.current !== key) {
      editorSyncedRef.current = key;
      setTitle(draft!.rules.title ?? '');
      setNotes(draft!.rules.notes ?? '');
      setFields(draft!.rules.fields.map(toEditable));
      setCrossRules((draft!.rules.cross_rules ?? []).map((r) => ({
        rule_id: r.rule_id,
        kind: r.kind,
        whenField: r.when?.field ?? '',
        whenOp: r.when?.op ?? 'eq',
        whenValueText: r.when?.values != null ? JSON.stringify(r.when.values) : r.when?.value != null ? JSON.stringify(r.when.value) : '',
        requireText: (r.require ?? []).join(', '),
        leftField: r.left ?? '',
        compareOp: r.op ?? 'lt',
        rightField: r.right ?? '',
        uniqueFieldsText: (r.fields ?? []).join(', '),
        nullPolicy: r.null_policy ?? 'ignore',
        note: r.note ?? '',
      })));
      setSavedInfo({ revision: draft!.revision, digest: draft!.normalized_digest });
    }
  }, [fc.draft]);

  const patchField = useCallback((i: number, patch: Partial<EditableField>) => {
    setFields((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  }, []);
  const patchCross = useCallback((i: number, patch: Partial<EditableCross>) => {
    setCrossRules((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }, []);

  /** 观测词汇 → enum：证据披露后显式采用（不静默复制）。 */
  const adoptObservedEnum = useCallback((fieldName: string, values: string[]) => {
    setFields((prev) => {
      const idx = prev.findIndex((f) => f.field === fieldName);
      if (idx >= 0) {
        return prev.map((f, i) => (i === idx ? { ...f, enumText: values.join('\n') } : f));
      }
      return [...prev, { ...emptyField(), field: fieldName, enumText: values.join('\n') }];
    });
  }, []);

  const buildDocument = useCallback((): FieldContractDocument | string => {
    const fieldRules: FieldContractFieldRule[] = [];
    for (const ef of fields) {
      const rule = fromEditable(ef);
      if (typeof rule === 'string') return rule;
      fieldRules.push(rule);
    }
    if (!fieldRules.length) return 'no_fields';
    const cross: FieldContractCrossRule[] = [];
    for (const ec of crossRules) {
      const rule = fromEditableCross(ec);
      if (typeof rule === 'string') return rule;
      cross.push(rule);
    }
    return { version: 1, title: title.trim(), notes: notes.trim() || null, fields: fieldRules, cross_rules: cross };
  }, [fields, crossRules, title, notes]);

  const doSaveDraft = useCallback(async () => {
    const doc = buildDocument();
    // 评审 P2：校验错误码曾被 `typeof doc === 'string'` 静默丢弃（字段名为空 /
    // 默认值 JSON 非法 / enum 超上限 / 删光字段后点保存零反馈）。typed 码原样展示。
    if (typeof doc === 'string') { setDraftError(doc); return; }
    setDraftError(null);
    const view = await fc.saveDraft(doc);
    if (view) setSavedInfo({ revision: view.revision, digest: view.normalized_digest });
  }, [buildDocument, fc]);

  /* ── 激活/退役确认弹窗 ── */
  const [confirmActivate, setConfirmActivate] = useState(false);
  const [confirmRetire, setConfirmRetire] = useState(false);

  /* ── 批量填值 ── */
  const [batchField, setBatchField] = useState('');
  const [batchValue, setBatchValue] = useState('');
  const batchFields = useMemo(() => {
    const rules = fc.draft?.rules.fields ?? fc.active?.rules.fields ?? [];
    return rules.map((r) => r.field);
  }, [fc.draft, fc.active]);

  const containerRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-fc-focus]' });

  if (!open) return null;

  const binding = fc.binding;
  const assessCounts = fc.assessment?.counts;

  const renderFieldEditor = (ef: EditableField, i: number) => (
    <div key={i} className="space-y-1.5 rounded border border-edge-subtle bg-surface-sunken p-2" data-testid={`fc-field-${i}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <input
          value={ef.field} onChange={(e) => patchField(i, { field: e.target.value })}
          placeholder={t('draft.fieldName')} aria-label={t('draft.fieldName')}
          className={`${INPUT_CLS} w-32`} data-testid={`fc-field-name-${i}`}
        />
        <select
          value={ef.type} onChange={(e) => patchField(i, { type: e.target.value as FieldContractFieldType })}
          aria-label={t('draft.fieldType')} className={`${INPUT_CLS} w-24`}
        >
          {FIELD_TYPES.map((ft) => <option key={ft} value={ft}>{ft}</option>)}
        </select>
        <label className="flex items-center gap-1 text-caption">
          <input type="checkbox" checked={ef.required} onChange={(e) => patchField(i, { required: e.target.checked })} />
          {t('draft.required')}
        </label>
        <label className="flex items-center gap-1 text-caption">
          <input type="checkbox" checked={ef.nullable} onChange={(e) => patchField(i, { nullable: e.target.checked })} />
          {t('draft.nullable')}
        </label>
        {ef.type === 'string' && (
          <select
            value={ef.empty_string} onChange={(e) => patchField(i, { empty_string: e.target.value as 'reject' | 'allow' })}
            aria-label={t('draft.emptyString')} className={`${INPUT_CLS} w-28`}
          >
            <option value="reject">{t('draft.emptyReject')}</option>
            <option value="allow">{t('draft.emptyAllow')}</option>
          </select>
        )}
        <button
          type="button" onClick={() => setFields((prev) => prev.filter((_, idx) => idx !== i))}
          className="ml-auto rounded border border-edge-subtle px-1.5 py-0.5 text-caption text-ink-secondary hover:bg-surface-hover"
        >
          {t('draft.remove')}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {(ef.type === 'number' || ef.type === 'integer') && (
          <>
            <label className="space-y-0.5">
              <span className={LABEL_CLS}>{t('draft.min')}</span>
              <input value={ef.minText} onChange={(e) => patchField(i, { minText: e.target.value })} className={`${INPUT_CLS} w-full`} inputMode="decimal" />
            </label>
            <label className="flex items-end gap-1 pb-1 text-caption">
              <input type="checkbox" checked={ef.exclusive_min} onChange={(e) => patchField(i, { exclusive_min: e.target.checked })} />
              {t('draft.exclusiveMin')}
            </label>
            <label className="space-y-0.5">
              <span className={LABEL_CLS}>{t('draft.max')}</span>
              <input value={ef.maxText} onChange={(e) => patchField(i, { maxText: e.target.value })} className={`${INPUT_CLS} w-full`} inputMode="decimal" />
            </label>
            <label className="flex items-end gap-1 pb-1 text-caption">
              <input type="checkbox" checked={ef.exclusive_max} onChange={(e) => patchField(i, { exclusive_max: e.target.checked })} />
              {t('draft.exclusiveMax')}
            </label>
          </>
        )}
        {ef.type === 'string' && (
          <>
            <label className="space-y-0.5">
              <span className={LABEL_CLS}>{t('draft.minLength')}</span>
              <input value={ef.minLengthText} onChange={(e) => patchField(i, { minLengthText: e.target.value })} className={`${INPUT_CLS} w-full`} inputMode="numeric" />
            </label>
            <label className="space-y-0.5">
              <span className={LABEL_CLS}>{t('draft.maxLength')}</span>
              <input value={ef.maxLengthText} onChange={(e) => patchField(i, { maxLengthText: e.target.value })} className={`${INPUT_CLS} w-full`} inputMode="numeric" />
            </label>
          </>
        )}
        <label className="space-y-0.5">
          <span className={LABEL_CLS}>{t('draft.label')}</span>
          <input value={ef.label} onChange={(e) => patchField(i, { label: e.target.value })} className={`${INPUT_CLS} w-full`} />
        </label>
        <label className="space-y-0.5">
          <span className={LABEL_CLS}>{t('draft.unit')}</span>
          <input value={ef.unit} onChange={(e) => patchField(i, { unit: e.target.value })} className={`${INPUT_CLS} w-full`} />
        </label>
        <label className="col-span-2 space-y-0.5">
          <span className={LABEL_CLS}>{t('draft.description')}</span>
          <input value={ef.description} onChange={(e) => patchField(i, { description: e.target.value })} className={`${INPUT_CLS} w-full`} />
        </label>
        <label className="col-span-2 space-y-0.5">
          <span className={LABEL_CLS}>{t('draft.default')}</span>
          <input
            value={ef.defaultText} onChange={(e) => patchField(i, { defaultText: e.target.value })}
            placeholder='&quot;pending&quot; / 2 / true' className={`${INPUT_CLS} w-full`} data-testid={`fc-field-default-${i}`}
          />
        </label>
      </div>
      <label className="block space-y-0.5">
        <span className={LABEL_CLS}>{t('draft.enumValues', { max: MAX_ENUM_VALUES })}</span>
        <textarea
          value={ef.enumText} onChange={(e) => patchField(i, { enumText: e.target.value })}
          rows={Math.min(3, Math.max(1, ef.enumText.split('\n').length))}
          className={`${INPUT_CLS} w-full font-mono text-caption`}
        />
      </label>
    </div>
  );

  const renderCrossEditor = (ec: EditableCross, i: number) => (
    <div key={i} className="space-y-1.5 rounded border border-edge-subtle bg-surface-sunken p-2" data-testid={`fc-cross-${i}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <input
          value={ec.rule_id} onChange={(e) => patchCross(i, { rule_id: e.target.value })}
          placeholder="rule_id" aria-label="rule_id" className={`${INPUT_CLS} w-28`}
        />
        <select
          value={ec.kind} onChange={(e) => patchCross(i, { kind: e.target.value as EditableCross['kind'] })}
          aria-label={t('draft.crossKind')} className={`${INPUT_CLS} w-28`}
        >
          <option value="conditional_required">{t('draft.kindConditional')}</option>
          <option value="compare">{t('draft.kindCompare')}</option>
          <option value="unique">{t('draft.kindUnique')}</option>
        </select>
        <button
          type="button" onClick={() => setCrossRules((prev) => prev.filter((_, idx) => idx !== i))}
          className="ml-auto rounded border border-edge-subtle px-1.5 py-0.5 text-caption text-ink-secondary hover:bg-surface-hover"
        >
          {t('draft.remove')}
        </button>
      </div>
      {ec.kind === 'conditional_required' ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <select value={ec.whenField} onChange={(e) => patchCross(i, { whenField: e.target.value })} aria-label={t('draft.whenField')} className={`${INPUT_CLS} w-28`}>
            <option value="">{t('draft.whenField')}</option>
            {fields.map((f) => f.field).filter(Boolean).map((fn) => <option key={fn} value={fn}>{fn}</option>)}
          </select>
          <select value={ec.whenOp} onChange={(e) => patchCross(i, { whenOp: e.target.value as FieldContractConditionOp })} aria-label={t('draft.whenOp')} className={`${INPUT_CLS} w-20`}>
            {CONDITION_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <input
            value={ec.whenValueText} onChange={(e) => patchCross(i, { whenValueText: e.target.value })}
            placeholder={t('draft.whenValue')} aria-label={t('draft.whenValue')} className={`${INPUT_CLS} w-28 font-mono`}
          />
          <input
            value={ec.requireText} onChange={(e) => patchCross(i, { requireText: e.target.value })}
            placeholder={t('draft.requireList')} aria-label={t('draft.requireList')} className={`${INPUT_CLS} w-36`}
          />
        </div>
      ) : ec.kind === 'compare' ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <select value={ec.leftField} onChange={(e) => patchCross(i, { leftField: e.target.value })} aria-label={t('draft.leftField')} className={`${INPUT_CLS} w-28`}>
            <option value="">{t('draft.leftField')}</option>
            {fields.map((f) => f.field).filter(Boolean).map((fn) => <option key={fn} value={fn}>{fn}</option>)}
          </select>
          <select value={ec.compareOp} onChange={(e) => patchCross(i, { compareOp: e.target.value as FieldContractCompareOp })} aria-label={t('draft.compareOp')} className={`${INPUT_CLS} w-16`}>
            {COMPARE_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <select value={ec.rightField} onChange={(e) => patchCross(i, { rightField: e.target.value })} aria-label={t('draft.rightField')} className={`${INPUT_CLS} w-28`}>
            <option value="">{t('draft.rightField')}</option>
            {fields.map((f) => f.field).filter(Boolean).map((fn) => <option key={fn} value={fn}>{fn}</option>)}
          </select>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            value={ec.uniqueFieldsText} onChange={(e) => patchCross(i, { uniqueFieldsText: e.target.value })}
            placeholder={t('draft.uniqueFields')} aria-label={t('draft.uniqueFields')} className={`${INPUT_CLS} w-44`}
          />
          <select value={ec.nullPolicy} onChange={(e) => patchCross(i, { nullPolicy: e.target.value as 'ignore' | 'participate' })} aria-label={t('draft.nullPolicy')} className={`${INPUT_CLS} w-36`}>
            <option value="ignore">{t('draft.nullIgnore')}</option>
            <option value="participate">{t('draft.nullParticipate')}</option>
          </select>
        </div>
      )}
      <input
        value={ec.note} onChange={(e) => patchCross(i, { note: e.target.value })}
        placeholder={t('draft.note')} aria-label={t('draft.note')} className={`${INPUT_CLS} w-full`}
      />
    </div>
  );

  const observedEntries = Object.values(fc.observe?.observed ?? {});
  const declaredByField = new Map((fc.observe?.declared ?? []).map((d) => [d.field, d]));
  const allFieldNames = new Set([...observedEntries.map((o) => o.name), ...declaredByField.keys()]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-ink/40 pt-[8vh]"
      data-testid="field-contracts-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        data-testid="field-contracts-panel"
        className="flex max-h-[86vh] w-full max-w-[960px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-edge-subtle px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-body-md font-semibold text-ink">
            <FileCheck2 size={15} aria-hidden /> {t('title')}
          </h2>
          <button
            type="button" data-fc-focus onClick={close} aria-label={t('closeAria')}
            className="rounded-sm p-1 text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {/* 1 数据集与绑定 */}
          <section className="space-y-2" data-testid="fc-step-dataset">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('stepDataset')}</h3>
              <button
                type="button" onClick={() => void fc.refresh()} disabled={fc.overviewLoading || !sessionId || !refText.trim()}
                className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-2.5 py-1 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
              >
                <RefreshCw size={12} className={fc.overviewLoading ? 'animate-spin' : ''} aria-hidden />
                {t('dataset.refresh')}
              </button>
            </div>
            <div className="flex gap-1.5">
              <input
                value={refText}
                onChange={(e) => setRefText(e.target.value)}
                placeholder="ref:…" aria-label={t('dataset.refLabel')}
                className={`${INPUT_CLS} w-72`} data-testid="fc-ref-input"
              />
            </div>
            {!sessionId && <p className="text-caption text-status-error">{t('noSession')}</p>}
            {binding && (
              <div className="rounded-md border border-edge-subtle bg-surface-sunken p-2 text-caption" data-testid="fc-binding">
                <span className={binding.status === 'ok' ? 'text-status-success' : binding.status === 'stale' ? 'text-status-warning' : 'text-ink-muted'}>
                  {t(`dataset.binding${binding.status.charAt(0).toUpperCase()}${binding.status.slice(1)}`)}
                </span>
                {binding.contract && (
                  <span className="pl-1.5 text-ink-secondary">
                    {t('dataset.activeContract', { title: binding.contract.title || binding.contract.contract_key, revision: binding.contract.revision })}
                  </span>
                )}
                {fc.observe && (
                  <span className="pl-1.5 text-ink-muted">· {t('dataset.featureCount', { count: fc.observe.feature_count })}</span>
                )}
              </div>
            )}
          </section>

          {/* 2 观测 vs 声明 */}
          <section className="space-y-2" data-testid="fc-step-observe">
            <h3 className="text-body-sm font-medium text-ink">{t('stepObserve')}</h3>
            {observedEntries.length === 0 && !fc.overviewLoading ? (
              <p className="text-caption text-ink-muted">{t('observe.empty')}</p>
            ) : (
              <div className="max-h-56 overflow-auto rounded border border-edge-subtle" data-testid="fc-observe-table">
                <table className="w-full text-caption">
                  <thead className="sticky top-0 bg-surface-sunken text-ink-muted">
                    <tr>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.observedCol')}</th>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.observedTypes')}</th>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.observedEnum')}</th>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.declaredCol')}</th>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.declaredType')}</th>
                      <th className="px-2 py-1 text-left font-medium">{t('observe.declaredEnum')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...allFieldNames].map((name) => {
                      const obs = fc.observe?.observed[name];
                      const dec = declaredByField.get(name);
                      const distinct = obs?.enum?.length ?? 0;
                      return (
                        <tr key={name} className="border-t border-edge-subtle/60">
                          <td className="px-2 py-1 font-mono text-ink">{name}</td>
                          <td className="px-2 py-1 text-ink-secondary">
                            {obs ? Object.entries(obs.types).map(([k, v]) => `${k}:${v}`).join(' ') : '—'}
                          </td>
                          <td className="px-2 py-1 text-ink-secondary">
                            {distinct > 0 ? (
                              <div className="flex flex-col gap-0.5">
                                <span className="text-ink-muted">{t('observe.useAsEnumNote', { distinct, scanned: Math.max(distinct, 24) })}</span>
                                <button
                                  type="button"
                                  onClick={() => adoptObservedEnum(name, obs!.enum!)}
                                  className="w-fit rounded border border-edge-subtle px-1.5 py-0.5 text-caption text-ink-secondary hover:bg-surface-hover"
                                  data-testid={`fc-use-enum-${name}`}
                                >
                                  {t('observe.useAsEnum')}
                                </button>
                              </div>
                            ) : '—'}
                          </td>
                          <td className="px-2 py-1 font-mono text-ink">{dec ? dec.field : '—'}</td>
                          <td className="px-2 py-1 text-ink-secondary">
                            {dec ? `${dec.type}${dec.required ? ' · req' : ''}${dec.nullable ? ' · null' : ''}` : '—'}
                          </td>
                          <td className="px-2 py-1 text-ink-secondary">{dec?.enum?.length ? dec.enum.slice(0, 6).join(', ') : '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 3 规则草稿 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="fc-step-rules">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-body-sm font-medium text-ink">{t('stepRules')}</h3>
              <div className="flex items-center gap-2 text-caption text-ink-muted">
                <span>{t('draft.fieldCounter', { count: fields.length, max: MAX_DRAFT_FIELDS })}</span>
                <span>{t('draft.crossCounter', { count: crossRules.length, max: MAX_CROSS_RULES })}</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <input
                value={title} onChange={(e) => setTitle(e.target.value)}
                placeholder={t('draft.titleLabel')} aria-label={t('draft.titleLabel')}
                className={`${INPUT_CLS} w-56`}
              />
              <input
                value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder={t('draft.notesLabel')} aria-label={t('draft.notesLabel')}
                className={`${INPUT_CLS} flex-1`}
              />
            </div>
            <div className="max-h-72 space-y-1.5 overflow-y-auto">
              {fields.map(renderFieldEditor)}
              {crossRules.map(renderCrossEditor)}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setFields((prev) => (prev.length >= MAX_DRAFT_FIELDS ? prev : [...prev, emptyField()]))}
                disabled={fields.length >= MAX_DRAFT_FIELDS}
                className="rounded-md border border-edge-subtle px-2.5 py-1 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
                data-testid="fc-add-field"
              >
                {fields.length >= MAX_DRAFT_FIELDS ? t('draft.capReached', { max: MAX_DRAFT_FIELDS }) : t('draft.addField')}
              </button>
              <button
                type="button"
                onClick={() => setCrossRules((prev) => (prev.length >= MAX_CROSS_RULES ? prev : [...prev, emptyCross(nextCrossRuleId(prev))]))}
                disabled={crossRules.length >= MAX_CROSS_RULES}
                className="rounded-md border border-edge-subtle px-2.5 py-1 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
                data-testid="fc-add-cross"
              >
                {crossRules.length >= MAX_CROSS_RULES ? t('draft.capReached', { max: MAX_CROSS_RULES }) : t('draft.addCross')}
              </button>
              <button
                type="button" onClick={() => void doSaveDraft()} disabled={fc.saving || !sessionId || !refText.trim()}
                className="ml-auto rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                data-testid="fc-save-draft"
              >
                {fc.saving ? t('draft.saving') : t('draft.save')}
              </button>
            </div>
            {savedInfo && (
              <p className="text-caption text-ink-muted" data-testid="fc-draft-meta">
                {t('draft.saved', { revision: savedInfo.revision })} · {t('draft.digest', { digest: savedInfo.digest.slice(0, 12) })}
              </p>
            )}
            {draftError && (
              <p className="text-caption text-status-error" data-testid="fc-draft-error">
                {draftError}
              </p>
            )}
          </section>

          {/* 4 评估 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="fc-step-assess">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('stepAssess')}</h3>
              <button
                type="button"
                onClick={() => fc.draft && void fc.runAssessment(fc.draft.contract_id)}
                disabled={!fc.draft || fc.assessing}
                className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:opacity-60"
                data-testid="fc-assess"
              >
                <PlayCircle size={14} aria-hidden />
                {fc.assessment ? t('assess.rerun') : t('assess.start')}
              </button>
            </div>
            {fc.assessing && (
              <p className="text-caption text-ink-muted" data-testid="fc-assessing">
                {t('assess.starting', { status: fc.assessment?.status ?? 'queued' })}
              </p>
            )}
            {fc.assessment && !fc.assessing && assessCounts && (
              <div className="grid grid-cols-2 gap-1.5 text-caption sm:grid-cols-4" data-testid="fc-assess-summary">
                <div className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                  {t('assess.totalFeatures')} <span className="pl-1 font-mono text-ink">{assessCounts.total_features ?? '—'}</span>
                </div>
                <div className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                  {t('assess.checkedFeatures')} <span className="pl-1 font-mono text-ink">{assessCounts.checked_features ?? '—'}</span>
                </div>
                <div className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                  {t('assess.violations')} <span className="pl-1 font-mono text-status-error">{assessCounts.violations_total ?? 0}</span>
                </div>
                <div className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                  {fc.assessment.sampled ? t('assess.sampled') : t('assess.complete')}
                </div>
                {fc.assessment.status === 'failed' && fc.assessment.error && (
                  <div className="col-span-full text-status-error">{t('assess.failed', { error: fc.assessment.error })}</div>
                )}
              </div>
            )}
            {fc.findings && (
              <div className="space-y-1.5" data-testid="fc-findings">
                <div className="flex items-center justify-between">
                  <span className="text-caption font-medium text-ink">{t('assess.findingsTitle')}</span>
                  <select
                    value={fc.codeFilter} onChange={(e) => fc.setCodeFilter(e.target.value)}
                    aria-label={t('assess.filterCode')} className={`${INPUT_CLS} w-44`}
                    data-testid="fc-findings-filter"
                  >
                    <option value="">{t('assess.allCodes')}</option>
                    {Object.keys(assessCounts?.counts_by_code ?? {}).map((code) => (
                      <option key={code} value={code}>{code} ({assessCounts!.counts_by_code![code]})</option>
                    ))}
                  </select>
                </div>
                {fc.findings.findings.length === 0 ? (
                  <p className="text-caption text-ink-muted">{t('assess.noFindings')}</p>
                ) : (
                  <div className="max-h-44 overflow-auto rounded border border-edge-subtle">
                    <table className="w-full text-caption">
                      <thead className="sticky top-0 bg-surface-sunken text-ink-muted">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium">{t('assess.locator')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('assess.code')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('assess.field')}</th>
                        </tr>
                      </thead>
                      <tbody data-testid="fc-findings-rows">
                        {fc.findings.findings.map((f, idx) => (
                          <tr key={idx} className="border-t border-edge-subtle/60">
                            <td className="px-2 py-1 font-mono text-ink-secondary">
                              {f.wfid ?? (f.row != null ? `row ${f.row}` : '—')}
                            </td>
                            <td className="px-2 py-1 font-mono text-ink">{f.code}</td>
                            <td className="px-2 py-1 font-mono text-ink-secondary">{f.field ?? f.rule_id ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-caption text-ink-muted">
                    {t('assess.pageInfo', { loaded: fc.findings.findings.length, filtered: fc.findings.filtered_total, total: fc.findings.total_violations })}
                  </span>
                  <div className="flex gap-1.5">
                    <button
                      type="button" disabled={!fc.hasPrevFindingsPage || fc.findingsLoading}
                      onClick={() => void fc.prevFindingsPage()}
                      className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                    >
                      {t('assess.prevPage')}
                    </button>
                    <button
                      type="button" disabled={!fc.findings.next_cursor || fc.findingsLoading}
                      onClick={() => void fc.nextFindingsPage()}
                      className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                      data-testid="fc-findings-next"
                    >
                      {t('assess.nextPage')}
                    </button>
                  </div>
                </div>
                {fc.findings.overflow_note && (
                  <p className="text-caption text-status-warning" data-testid="fc-findings-overflow">
                    {t('assess.overflowNote', { n: assessCounts?.overflow_count ?? 0 })}
                  </p>
                )}
              </div>
            )}
          </section>

          {/* 5 激活 / 退役 / 历史 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="fc-step-lifecycle">
            <h3 className="text-body-sm font-medium text-ink">{t('stepLifecycle')}</h3>
            {fc.error && (
              <div className="rounded-md border border-status-error/40 bg-status-error/10 p-2 text-body-sm text-status-error" data-testid="fc-error">
                <span className="font-mono text-caption">{fc.error.code}</span> — {fc.error.message}
                {fc.error.code === 'assessment_stale' && <p className="pt-0.5 text-caption">{t('activate.staleHint')}</p>}
                {fc.error.code === 'activation_race' && <p className="pt-0.5 text-caption">{t('activate.race')}</p>}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setConfirmActivate(true)}
                disabled={!fc.canActivate || fc.activating || !fc.draft}
                className="inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                data-testid="fc-activate"
              >
                <ShieldCheck size={14} aria-hidden /> {t('activate.button')}
              </button>
              {!fc.canActivate && fc.activateGateKey && (
                <span className="text-caption text-ink-muted" data-testid="fc-activate-gate">
                  {t(fc.activateGateKey, fc.activateGateParams ?? undefined)}
                </span>
              )}
              <button
                type="button"
                onClick={() => setConfirmRetire(true)}
                disabled={!fc.active || fc.retiring}
                className="rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:opacity-60"
                data-testid="fc-retire"
              >
                {t('retire.button')}
              </button>
            </div>
            <div className="space-y-0.5">
              <p className="text-caption font-medium text-ink-secondary">{t('history.title')}</p>
              {fc.history.length === 0 ? (
                <p className="text-caption text-ink-muted">{t('history.empty')}</p>
              ) : (
                fc.history.map((c) => (
                  <p key={c.contract_id} className="text-caption text-ink-muted">
                    {t('history.item', { title: c.title || c.contract_key, revision: c.revision, status: c.status })}
                  </p>
                ))
              )}
            </div>
          </section>

          {/* 6 批量填值 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="fc-step-batch">
            <h3 className="text-body-sm font-medium text-ink">{t('stepBatch')}</h3>
            <div className="flex flex-wrap items-center gap-1.5">
              <select
                value={batchField} onChange={(e) => setBatchField(e.target.value)}
                aria-label={t('batch.fieldLabel')} className={`${INPUT_CLS} w-40`}
                data-testid="fc-batch-field"
              >
                <option value="">{t('batch.fieldLabel')}</option>
                {batchFields.map((fn) => <option key={fn} value={fn}>{fn}</option>)}
              </select>
              <input
                value={batchValue} onChange={(e) => setBatchValue(e.target.value)}
                placeholder={t('batch.valuePlaceholder')} aria-label={t('batch.valueLabel')}
                className={`${INPUT_CLS} w-52 font-mono`} data-testid="fc-batch-value"
              />
              <button
                type="button"
                onClick={() => { if (batchField) void fc.runBatchFill(batchField, batchValue); }}
                disabled={fc.batchRunning || !batchField || !sessionId || !refText.trim()}
                className="rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                data-testid="fc-batch-run"
              >
                {fc.batchRunning ? t('batch.running') : t('batch.run')}
              </button>
            </div>
            {fc.error && fc.error.code.startsWith('selection_') && (
              <div className="rounded-md border border-status-warning/40 bg-status-warning/10 p-2 text-caption text-ink" data-testid="fc-batch-error">
                {(() => {
                  switch (fc.error.code) {
                    case 'selection_too_large': {
                      const count = Number(fc.error.message);
                      return fc.error.message.trim() && Number.isFinite(count)
                        ? t('batch.tooLarge', { count, max: MAX_BATCH_TARGETS })
                        : fc.error.message;
                    }
                    case 'selection_incomplete': {
                      const [got, total] = fc.error.message.split('/').map((part) => Number(part));
                      return Number.isFinite(got) && Number.isFinite(total)
                        ? t('batch.incomplete', { got, total })
                        : fc.error.message;
                    }
                    case 'selection_count_unknown': return t('batch.countUnknown');
                    case 'selection_ref_mismatch': return t('batch.selectionRefMismatch', { ref: fc.error.message });
                    case 'invalid_value': return t('batch.invalidValue', { value: fc.error.message });
                    case 'selection_scope_unavailable': return t('batch.scopeUnavailable', { kind: fc.error.message });
                    default: return t('batch.noSelection');
                  }
                })()}
              </div>
            )}
            {fc.batchResult && (
              <p className="text-caption text-ink-secondary" data-testid="fc-batch-result">
                {t('batch.result', {
                  targets: fc.batchResult.targets,
                  changed: fc.batchResult.changed,
                  violations: fc.batchResult.violations,
                  unmatched: fc.batchResult.unmatched,
                })}
              </p>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={confirmActivate}
        title={t('activate.confirmTitle')}
        description={t('activate.confirmBody')}
        confirmLabel={t('activate.confirm')}
        cancelLabel={t('activate.cancel')}
        onConfirm={() => {
          setConfirmActivate(false);
          void fc.activate();
        }}
        onCancel={() => setConfirmActivate(false)}
      />
      <ConfirmDialog
        open={confirmRetire}
        title={t('retire.confirmTitle')}
        description={t('retire.confirmBody')}
        confirmLabel={t('retire.confirm')}
        onConfirm={() => {
          setConfirmRetire(false);
          void fc.retire();
        }}
        onCancel={() => setConfirmRetire(false)}
      />
    </div>
  );
}
