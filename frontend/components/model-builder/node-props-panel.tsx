'use client';

/**
 * NodePropsPanel — 选中节点属性表单 + 模型级参数分区。
 *
 * 编辑纪律：一切修改走 onChangeNode/onChangeParams 回调（宿主本地 draft
 * 状态），本组件不发起任何请求；「保存」按钮（CAS PUT）归宿主工具条。
 * 下拉词表来自能力目录（role/transform_op/capability）；模型级参数
 * （增删改、kind 下拉、min/max/choices/default）是独立分区。
 */

import { memo, useCallback } from 'react';
import { Trash2, MousePointerClick } from 'lucide-react';
import clsx from 'clsx';
import { useT } from '@/lib/i18n/useT';
import {
  isValidSlug,
  type ModelDocument,
  type ModelNode,
  type ModelParamKind,
  type ModelParamSpec,
} from '@/lib/workflow/model-graph';
import type { ModelCapabilities } from '@/lib/api/model-authoring';
import { EmptyState } from '@/components/shared/empty-state';

const FIELD = 'w-full rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink focus:border-status-accent focus:outline-none';
const LABEL = 'text-micro font-medium text-ink-muted';
const SECTION_HEAD = 'text-meta font-semibold text-ink-secondary';

export interface NodePropsPanelProps {
  document: ModelDocument;
  selectedNodeId: string | null;
  capabilities: ModelCapabilities | null;
  onChangeNode: (nodeId: string, patch: Partial<ModelNode>) => void;
  onChangeParams: (nodeId: string, values: Record<string, unknown>) => void;
  onChangeModelParams: (params: ModelParamSpec[]) => void;
  onDeleteNode: (nodeId: string) => void;
}

/** 节点静态参数键值编辑（值以文本输入；标量/短列表语义由后端 validate 兜）。 */
const NodeParamsEditor = memo(function NodeParamsEditor({
  nodeId,
  values,
  onChange,
}: {
  nodeId: string;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  const t = useT('modelBuilder');
  const entries = Object.entries(values);

  const setKey = useCallback(
    (oldKey: string, newKey: string) => {
      const next: Record<string, unknown> = {};
      for (const [k, v] of entries) next[k === oldKey ? newKey : k] = v;
      onChange(next);
    },
    [entries, onChange],
  );

  return (
    <div className="space-y-1.5">
      <p className={LABEL}>{t('props.nodeParams')}</p>
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center gap-1">
          <input
            aria-label={t('props.paramKey')}
            value={k}
            onChange={(e) => setKey(k, e.target.value)}
            className={clsx(FIELD, 'w-2/5')}
          />
          <input
            aria-label={t('props.paramValue')}
            defaultValue={typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '')}
            onBlur={(e) => onChange({ ...values, [k]: e.target.value })}
            className={clsx(FIELD, 'w-3/5')}
          />
          <button
            type="button"
            aria-label={t('props.removeParam', { name: k })}
            onClick={() => {
              const next = { ...values };
              delete next[k];
              onChange(next);
            }}
            className="shrink-0 p-1 text-ink-muted hover:text-status-critical"
          >
            <Trash2 size={12} aria-hidden />
          </button>
        </div>
      ))}
      <button
        type="button"
        data-testid={`add-node-param-${nodeId}`}
        onClick={() => onChange({ ...values, param_new: '' })}
        className="text-meta text-status-accent hover:underline"
      >
        + {t('props.addParam')}
      </button>
    </div>
  );
});

/** 模型级参数分区（增删改、kind 下拉、约束字段）。 */
const ModelParamsEditor = memo(function ModelParamsEditor({
  params,
  dataInputIds,
  onChange,
}: {
  params: ModelParamSpec[];
  dataInputIds: string[];
  onChange: (params: ModelParamSpec[]) => void;
}) {
  const t = useT('modelBuilder');
  const kinds: ModelParamKind[] = ['number', 'integer', 'string', 'enum', 'boolean', 'field', 'unit', 'time_window'];
  // 全字面量键表（kind 值 → catalog 键；拼接会产生 kindTime_window 这类
  // 与 catalog 键 kindTimeWindow 不符的路径）。
  const KIND_KEYS: Record<ModelParamKind, string> = {
    number: 'props.kindNumber',
    integer: 'props.kindInteger',
    string: 'props.kindString',
    enum: 'props.kindEnum',
    boolean: 'props.kindBoolean',
    field: 'props.kindField',
    unit: 'props.kindUnit',
    time_window: 'props.kindTimeWindow',
  };
  const kindLabel = (k: ModelParamKind) => t(KIND_KEYS[k]);

  const patch = (idx: number, p: Partial<ModelParamSpec>) => {
    const next = params.map((spec, i) => (i === idx ? { ...spec, ...p } : spec));
    onChange(next);
  };

  return (
    <div className="space-y-2 border-t border-edge-subtle pt-2">
      <p className={SECTION_HEAD}>{t('props.modelParams')}</p>
      {params.map((spec, idx) => (
        <div
          key={spec.name}
          data-testid={`model-param-${spec.name}`}
          className="space-y-1 rounded-sm border border-edge-subtle bg-surface-raised p-2"
        >
          <div className="flex items-center gap-1">
            <input
              aria-label={t('props.paramName')}
              value={spec.name}
              onChange={(e) => patch(idx, { name: e.target.value })}
              className={clsx(FIELD, 'flex-1')}
            />
            <select
              aria-label={t('props.paramKind')}
              value={spec.kind}
              onChange={(e) => patch(idx, { kind: e.target.value as ModelParamKind })}
              className={clsx(FIELD, 'w-28')}
            >
              {kinds.map((k) => (
                <option key={k} value={k}>{kindLabel(k)}</option>
              ))}
            </select>
            <button
              type="button"
              aria-label={t('props.removeModelParam', { name: spec.name })}
              onClick={() => onChange(params.filter((_, i) => i !== idx))}
              className="shrink-0 p-1 text-ink-muted hover:text-status-critical"
            >
              <Trash2 size={12} aria-hidden />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1">
            <input
              aria-label={t('props.paramLabel')}
              placeholder={t('props.paramLabel')}
              value={spec.label ?? ''}
              onChange={(e) => patch(idx, { label: e.target.value })}
              className={FIELD}
            />
            <label className="flex items-center gap-1 text-micro text-ink-secondary">
              <input
                type="checkbox"
                aria-label={t('props.paramRequired')}
                checked={spec.required ?? true}
                onChange={(e) => patch(idx, { required: e.target.checked })}
              />
              {t('props.paramRequired')}
            </label>
            {(spec.kind === 'number' || spec.kind === 'integer') && (
              <>
                <input
                  aria-label={t('props.paramMin')}
                  placeholder={t('props.paramMin')}
                  type="number"
                  value={spec.minimum ?? ''}
                  onChange={(e) => patch(idx, { minimum: e.target.value === '' ? null : Number(e.target.value) })}
                  className={FIELD}
                />
                <input
                  aria-label={t('props.paramMax')}
                  placeholder={t('props.paramMax')}
                  type="number"
                  value={spec.maximum ?? ''}
                  onChange={(e) => patch(idx, { maximum: e.target.value === '' ? null : Number(e.target.value) })}
                  className={FIELD}
                />
              </>
            )}
            {spec.kind === 'enum' && (
              <input
                aria-label={t('props.paramChoices')}
                placeholder={t('props.paramChoices')}
                value={(spec.choices ?? []).join(',')}
                onChange={(e) =>
                  patch(idx, { choices: e.target.value.split(',').map((c) => c.trim()).filter(Boolean) })
                }
                className={clsx(FIELD, 'col-span-2')}
              />
            )}
            {spec.kind === 'field' && (
              dataInputIds.length > 0 ? (
                <select
                  aria-label={t('props.paramInputNode')}
                  value={spec.input_node ?? ''}
                  onChange={(e) => patch(idx, { input_node: e.target.value })}
                  className={clsx(FIELD, 'col-span-2')}
                >
                  <option value="">—</option>
                  {dataInputIds.map((id) => (
                    <option key={id} value={id}>{id}</option>
                  ))}
                </select>
              ) : (
                <p className="col-span-2 text-micro text-status-warn">{t('props.noDataInputs')}</p>
              )
            )}
            <input
              aria-label={t('props.paramDefault')}
              placeholder={t('props.paramDefault')}
              value={spec.default?.value == null ? '' : String(spec.default.value)}
              onChange={(e) => {
                const raw = e.target.value;
                let value: unknown = raw;
                if (spec.kind === 'number') value = raw === '' ? null : Number(raw);
                else if (spec.kind === 'integer') value = raw === '' ? null : Math.trunc(Number(raw));
                else if (spec.kind === 'boolean') value = raw === 'true';
                patch(idx, { default: raw === '' && spec.kind !== 'boolean' ? null : { value } });
              }}
              className={FIELD}
            />
            {spec.kind === 'number' && (
              <input
                aria-label={t('props.paramUnit')}
                placeholder={t('props.paramUnit')}
                value={spec.unit ?? ''}
                onChange={(e) => patch(idx, { unit: e.target.value })}
                className={FIELD}
              />
            )}
          </div>
        </div>
      ))}
      <button
        type="button"
        data-testid="add-model-param"
        onClick={() => onChange([...params, { name: `param_${params.length + 1}`, kind: 'number', required: true }])}
        className="text-meta text-status-accent hover:underline"
      >
        + {t('props.addModelParam')}
      </button>
    </div>
  );
});

export function NodePropsPanel({
  document: doc,
  selectedNodeId,
  capabilities,
  onChangeNode,
  onChangeParams,
  onChangeModelParams,
  onDeleteNode,
}: NodePropsPanelProps) {
  const t = useT('modelBuilder');
  const node = doc.nodes.find((n) => n.node_id === selectedNodeId) ?? null;

  if (!node) {
    return (
      <EmptyState icon={MousePointerClick} title={t('props.nothingSelected')} />
    );
  }

  const roleOptions = capabilities?.data_roles ?? [];
  const opOptions = capabilities?.transform_ops ?? [];
  const capOptions = capabilities?.capabilities ?? [];
  const acceptedInputs =
    capOptions.find((c) => c.capability === node.capability)?.input_artifact_types ?? [];

  const showField = (label: string, value: React.ReactNode) => (
    <div className="space-y-0.5">
      <p className={LABEL}>{label}</p>
      {value}
    </div>
  );

  return (
    <div className="space-y-3" data-testid="node-props-panel">
      {showField(
        t('props.nodeId'),
        <div className="flex items-center gap-1">
          <input
            value={node.node_id}
            readOnly
            aria-label={t('props.nodeId')}
            className={clsx(FIELD, 'flex-1 bg-surface-sunken text-ink-muted')}
          />
          {!isValidSlug(node.node_id) && (
            <span className="text-micro text-status-critical" role="alert">{t('props.invalidSlug')}</span>
          )}
          <button
            type="button"
            aria-label={t('props.deleteNode')}
            data-testid="delete-node-btn"
            onClick={() => onDeleteNode(node.node_id)}
            className="shrink-0 p-1 text-ink-muted hover:text-status-critical"
          >
            <Trash2 size={14} aria-hidden />
          </button>
        </div>,
      )}
      {showField(t('props.kind'), (
        <input value={t(`node.kind${node.kind.charAt(0).toUpperCase()}${node.kind.slice(1)}`)} readOnly aria-label={t('props.kind')} className={clsx(FIELD, 'bg-surface-sunken text-ink-muted')} />
      ))}
      {showField(t('props.nodeTitle'), (
        <input
          aria-label={t('props.nodeTitle')}
          value={node.title ?? ''}
          onChange={(e) => onChangeNode(node.node_id, { title: e.target.value })}
          className={FIELD}
        />
      ))}
      {showField(t('props.description'), (
        <textarea
          aria-label={t('props.description')}
          value={node.description ?? ''}
          onChange={(e) => onChangeNode(node.node_id, { description: e.target.value })}
          rows={2}
          className={FIELD}
        />
      ))}
      {node.kind === 'data_input' && showField(t('props.role'), (
        <select
          aria-label={t('props.role')}
          value={node.role ?? ''}
          onChange={(e) => onChangeNode(node.node_id, { role: e.target.value })}
          className={FIELD}
        >
          {!roleOptions.includes(node.role ?? '') && node.role ? (
            <option value={node.role}>{node.role}</option>
          ) : null}
          {roleOptions.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      ))}
      {node.kind === 'transform' && showField(t('props.transformOp'), (
        <select
          aria-label={t('props.transformOp')}
          value={node.transform_op ?? ''}
          onChange={(e) => onChangeNode(node.node_id, { transform_op: e.target.value })}
          className={FIELD}
        >
          {!opOptions.includes(node.transform_op ?? '') && node.transform_op ? (
            <option value={node.transform_op}>{node.transform_op}</option>
          ) : null}
          {opOptions.map((op) => (
            <option key={op} value={op}>{op}</option>
          ))}
        </select>
      ))}
      {node.kind === 'analysis' && (
        <>
          {showField(t('props.capability'), (
            <select
              aria-label={t('props.capability')}
              value={node.capability ?? ''}
              onChange={(e) => onChangeNode(node.node_id, { capability: e.target.value })}
              className={FIELD}
            >
              {!capOptions.some((c) => c.capability === node.capability) && node.capability ? (
                <option value={node.capability}>{node.capability}</option>
              ) : null}
              {capOptions.map((c) => (
                <option key={c.capability} value={c.capability}>{c.capability}</option>
              ))}
            </select>
          ))}
          {acceptedInputs.length > 0 && showField(t('props.inputArtifactType'), (
            <select
              aria-label={t('props.inputArtifactType')}
              value={node.input_artifact_type ?? ''}
              onChange={(e) => onChangeNode(node.node_id, { input_artifact_type: e.target.value })}
              className={FIELD}
            >
              <option value="">{t('props.inputArtifactTypeDefault')}</option>
              {acceptedInputs.map((at) => (
                <option key={at} value={at}>{at}</option>
              ))}
            </select>
          ))}
        </>
      )}
      <NodeParamsEditor
        nodeId={node.node_id}
        values={Object.fromEntries(
          Object.entries(node.params?.values ?? {}).map(([k, v]) => [k, v.value]),
        )}
        onChange={(values) =>
          onChangeParams(
            node.node_id,
            Object.fromEntries(
              Object.entries(values).map(([k, v]) => [k, { value: v as never }]),
            ),
          )
        }
      />
      {node.kind !== 'data_input' && (
        <label className="flex items-center gap-1.5 text-meta text-ink-secondary">
          <input
            type="checkbox"
            aria-label={t('props.optional')}
            checked={node.optional ?? false}
            onChange={(e) => onChangeNode(node.node_id, { optional: e.target.checked })}
          />
          {t('props.optional')}
        </label>
      )}
      <ModelParamsEditor
        params={doc.params ?? []}
        dataInputIds={doc.nodes.filter((n) => n.kind === 'data_input').map((n) => n.node_id)}
        onChange={onChangeModelParams}
      />
    </div>
  );
}
