'use client';

/**
 * 计划（Plan）控制台（P4）。
 *
 * - Plan JSON 编辑器 + 客户端 JSON 预检（错误行内提示）；
 * - POST /plans/validate：结果结构化展示（waves / 指纹 / wired categories），
 *   校验失败把错误定位到分区条目（node_id + field + issue）；
 * - 提交：POST /plans/runs（cluster 202）→ 进度视图（游标事件轮询 +
 *   阶段瀑布图 + cancel）；POST /plans/execute（内存路径）→ evidence 快照表。
 * 编辑器内容是用户输入，不预置假数据；「示例模板」按钮显式插入可编辑样例。
 */
import { useMemo, useState } from 'react';
import { ShieldCheck, Play, Upload, Ban } from 'lucide-react';
import { InlineNotice } from '@/components/shared/inline-notice';
import {
  validatePlan,
  executePlan,
  submitClusterRun,
  cancelClusterRun,
  GeoComputeApiError,
  type ExecutionPlan,
  type PlanValidation,
  type ExecutionRun,
  type ClusterSubmitResult,
} from '@/lib/api/geocompute';
import { useClusterRunEvents } from '@/lib/hooks/use-cluster-run-events';
import { PlanWaterfall } from './plan-waterfall';
import { OpsCard, formatBytes, formatDuration } from './ops-shared';

const SAMPLE_PLAN = `{
  "plan_id": "plan-demo-1",
  "description": "裁剪 → 缓冲 → 合并（示例模板，可编辑）",
  "nodes": [
    { "node_id": "clip_a", "category": "vector", "operation": "clip", "inputs": [], "parameters": {} },
    { "node_id": "buffer_b", "category": "vector", "operation": "buffer", "inputs": ["clip_a"], "parameters": {} },
    { "node_id": "merge_c", "category": "vector", "operation": "merge", "inputs": ["clip_a", "buffer_b"], "reuse": "allow" }
  ]
}`;

type SubmitPhase =
  | { kind: 'idle' }
  | { kind: 'cluster'; run: ClusterSubmitResult }
  | { kind: 'memory'; run: ExecutionRun };

export function PlanConsole({
  ownerToken,
  sessionId,
}: {
  ownerToken?: string | null;
  sessionId?: string | null;
}) {
  const [text, setText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [validation, setValidation] = useState<PlanValidation | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [validationDetails, setValidationDetails] = useState<{ node_id?: string; field?: string; issue?: string }[]>([]);
  const [phase, setPhase] = useState<SubmitPhase>({ kind: 'idle' });
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const parsed = useMemo<{ plan: ExecutionPlan | null }>(() => {
    if (!text.trim()) return { plan: null };
    try {
      const plan = JSON.parse(text) as ExecutionPlan;
      if (!plan || typeof plan !== 'object' || typeof plan.plan_id !== 'string' || !Array.isArray(plan.nodes)) {
        return { plan: null };
      }
      return { plan };
    } catch (err) {
      return { plan: null, error: err instanceof Error ? err.message : 'JSON 解析失败' } as { plan: null };
    }
  }, [text]);

  const onTextChange = (next: string) => {
    setText(next);
    setJsonError(null);
    if (!next.trim()) return;
    try {
      JSON.parse(next);
    } catch (err) {
      setJsonError(err instanceof Error ? err.message : 'JSON 语法错误');
    }
  };

  const doValidate = async () => {
    if (!parsed.plan) return;
    setBusy(true);
    setValidationError(null);
    setValidationDetails([]);
    try {
      const result = await validatePlan(parsed.plan, { ownerToken });
      setValidation(result);
    } catch (err) {
      setValidation(null);
      if (err instanceof GeoComputeApiError) {
        setValidationError(`${err.code}：${err.message}`);
        const details = (err.details as { errors?: unknown[] } | undefined)?.errors;
        if (Array.isArray(details)) {
          setValidationDetails(
            details.filter((d): d is { node_id?: string; field?: string; issue?: string } =>
              !!d && typeof d === 'object'),
          );
        }
      } else {
        setValidationError('校验请求失败');
      }
    } finally {
      setBusy(false);
    }
  };

  const doSubmitCluster = async () => {
    if (!parsed.plan) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const run = await submitClusterRun(parsed.plan, { ownerToken, sessionId });
      setPhase({ kind: 'cluster', run });
    } catch (err) {
      setSubmitError(err instanceof GeoComputeApiError ? `${err.code}：${err.message}` : '提交失败');
    } finally {
      setBusy(false);
    }
  };

  const doExecuteMemory = async () => {
    if (!parsed.plan) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const run = await executePlan(parsed.plan, { ownerToken, sessionId });
      setPhase({ kind: 'memory', run });
    } catch (err) {
      setSubmitError(err instanceof GeoComputeApiError ? `${err.code}：${err.message}` : '执行失败');
    } finally {
      setBusy(false);
    }
  };

  // cluster 路径：游标事件轮询 + 瀑布（终态/404 即停，纪律见 hook）。
  const events = useClusterRunEvents({
    runId: phase.kind === 'cluster' ? phase.run.run_id : null,
    ownerToken,
  });

  const canSubmit = parsed.plan != null && !jsonError && !busy;

  return (
    <div className="flex flex-col gap-3" data-testid="ops-plan-console">
      <OpsCard
        title="计划编辑器"
        sub="ExecutionPlanIn JSON —— 先校验后提交"
        actions={
          <button
            type="button"
            onClick={() => {
              setText(SAMPLE_PLAN);
              setJsonError(null);
            }}
            className="rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
          >
            插入示例模板
          </button>
        }
      >
        <label htmlFor="ops-plan-json" className="sr-only">
          计划 JSON
        </label>
        <textarea
          id="ops-plan-json"
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          spellCheck={false}
          rows={10}
          aria-invalid={jsonError != null}
          aria-describedby={jsonError ? 'ops-plan-json-error' : undefined}
          className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1.5 font-mono text-micro text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-status-accent"
          placeholder='{"plan_id": "…", "nodes": […]}'
        />
        {jsonError && (
          <div id="ops-plan-json-error" role="alert" className="text-micro text-status-critical">
            JSON 语法错误：{jsonError}
          </div>
        )}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void doValidate()}
            data-testid="plan-validate"
            className="flex items-center gap-1 rounded-sm border border-status-info-border bg-status-info-soft px-2 py-1 text-micro font-medium text-status-info disabled:opacity-50"
          >
            <ShieldCheck size={11} aria-hidden />
            校验
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void doSubmitCluster()}
            data-testid="plan-submit"
            className="flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro font-medium text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
          >
            <Upload size={11} aria-hidden />
            提交集群执行
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void doExecuteMemory()}
            className="flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro font-medium text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
          >
            <Play size={11} aria-hidden />
            内存执行
          </button>
        </div>
      </OpsCard>

      {/* 校验结果 */}
      {validation && (
        <OpsCard title="校验通过" sub={`graph ${validation.graph_fingerprint}`} testId="plan-validation-ok">
          <p className="text-micro text-ink-secondary">
            分波执行 {validation.waves.length} 波 · 接线类别 {validation.wired_categories.join('、') || '—'}
          </p>
          <ul className="flex flex-col gap-0.5">
            {validation.waves.map((wave, i) => (
              <li key={i} className="text-micro text-ink-muted">
                波 {i + 1}：{Array.isArray(wave) ? wave.join('、') : String(wave)}
              </li>
            ))}
          </ul>
        </OpsCard>
      )}
      {validationError && (
        <OpsCard title="校验失败" sub="错误已定位到分区条目" testId="plan-validation-error">
          <InlineNotice variant="error">{validationError}</InlineNotice>
          {validationDetails.length > 0 && (
            <ul className="flex flex-col gap-0.5" aria-label="校验错误明细">
              {validationDetails.map((d, i) => (
                <li key={i} className="rounded-sm border border-status-critical-border bg-status-critical-soft px-2 py-1 text-micro text-status-critical">
                  <span className="font-mono">{d.node_id ?? '?'}</span>
                  {d.field ? ` · ${d.field}` : ''} — {d.issue ?? '不合法'}
                </li>
              ))}
            </ul>
          )}
        </OpsCard>
      )}

      {/* 内存执行 evidence 快照 */}
      {phase.kind === 'memory' && (
        <OpsCard title="内存执行完成" sub={`${phase.run.run_id} · ${formatDuration(phase.run.wall_time_s)}`} testId="plan-memory-run">
          <ul className="flex flex-col gap-0.5">
            {Object.entries(phase.run.evidence ?? {}).map(([nodeId, ev]) => (
              <li key={nodeId} className="flex items-center justify-between gap-2 text-micro text-ink-secondary">
                <span className="truncate font-mono">{nodeId}</span>
                <span className="flex shrink-0 items-center gap-2 text-ink-muted">
                  <span>{ev.status}</span>
                  <span>{ev.rows_emitted ?? '—'} 行</span>
                  <span>{formatBytes(ev.bytes_emitted)}</span>
                  <span>{formatDuration(ev.duration_s)}</span>
                </span>
              </li>
            ))}
          </ul>
        </OpsCard>
      )}

      {/* cluster 进度视图：瀑布 + 取消 */}
      {phase.kind === 'cluster' && (
        <OpsCard
          title="集群执行进度"
          sub={`${phase.run.run_id} · ${phase.run.status} · 游标 {after_id=${events.cursor}}`}
          actions={
            <button
              type="button"
              data-testid="plan-cancel-run"
              onClick={() => {
                void cancelClusterRun(phase.run.run_id, { ownerToken }).catch(() => undefined);
              }}
              className="flex items-center gap-1 rounded-sm border border-status-critical-border bg-status-critical-soft px-1.5 py-0.5 text-micro font-medium text-status-critical"
            >
              <Ban size={11} aria-hidden />
              取消 run
            </button>
          }
          testId="plan-progress"
        >
          {events.error && <InlineNotice variant="error">{events.error}</InlineNotice>}
          {events.channel === 'notfound' ? (
            <InlineNotice variant="info">事件已随 run 行清理（retention），进度不可用。</InlineNotice>
          ) : (
            <PlanWaterfall events={events.events} runId={phase.run.run_id} />
          )}
        </OpsCard>
      )}

      {submitError && <InlineNotice variant="error">{submitError}</InlineNotice>}
    </div>
  );
}
