'use client';

/**
 * 数据准备工作台 —— 一屏「检查 → 映射确认 → 准备 → 入图」流（M7）。
 *
 * 生产链路：本会话上传（POST /upload 落 session_ref）→ inspect（收据+提案+
 * 有界样本）→ 用户复核/修正字段角色（低置信度显式必选）→ 计划预览+幂等执行
 * （原件不可变，输出 ref:prep-*）→ 加载到地图（ref 水合，与 data-sources
 * 物化入图同一范式）。
 *
 * 诚实纪律：阻断/需澄清时禁用执行按钮并给出指引 —— 绝不把「未准备好」
 * 呈现为可继续。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { FlaskConical, RefreshCw } from 'lucide-react';

import { dataPreparationApi, type FieldProposal, type InspectResponse, type PreparationReceipt, type PreviewResponse } from '@/lib/api/data-preparation';
import { listUploads } from '@/lib/api/upload';
import { dataFabricApi } from '@/lib/api/data-fabric';
import { describeApiError, isApiError } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { useT } from '@/lib/i18n/useT';
import type { GeoJSONFeatureCollection } from '@/lib/types';

/** 全部提案角色（与后端 vocabulary.PROPOSAL_ROLES 镜像；下拉用）。 */
const ALL_ROLES = [
  'id', 'name', 'address', 'x', 'y', 'lon', 'lat',
  'time', 'category', 'value', 'admin_code', 'denominator', 'weight',
] as const;

const SEVERITY_CLASS: Record<string, string> = {
  blocked: 'bg-red-100 text-red-800 border-red-300',
  warn: 'bg-amber-100 text-amber-800 border-amber-300',
  ok: 'bg-slate-100 text-slate-700 border-slate-300',
};

interface PrepareFlowProps {
  /** tab 传参可选（undefined 归一为 null，组件内一律按 string|null 处理）。 */
  sessionId?: string | null | undefined;
  ownerToken?: string | null | undefined;
}

interface UploadOption {
  ref: string;
  label: string;
}

export function PrepareFlow({ sessionId: sessionIdProp, ownerToken }: PrepareFlowProps) {
  const sessionId = sessionIdProp ?? null;
  const t = useT('dataPreparation');
  const tAny = useT();
  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);

  const [uploads, setUploads] = useState<UploadOption[]>([]);
  const [sourceRef, setSourceRef] = useState('');
  const [inspection, setInspection] = useState<InspectResponse | null>(null);
  const [roleChoices, setRoleChoices] = useState<Record<string, string>>({});
  const [inspecting, setInspecting] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [receipt, setReceipt] = useState<PreparationReceipt | null>(null);

  const refreshUploads = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await listUploads(sessionId, { ownerToken });
      const options = (res.uploads ?? [])
        .filter((u) => u.session_ref)
        .map((u) => ({
          ref: u.session_ref as string,
          label: `${u.original_name} · ${u.feature_count ?? '?'} 行`,
        }));
      setUploads(options);
      return options;
    } catch {
      return [];
    }
  }, [sessionId, ownerToken]);

  useEffect(() => {
    setInspection(null);
    setReceipt(null);
    setPreview(null);
    setRoleChoices({});
    setSourceRef('');
    void refreshUploads();
  }, [refreshUploads]);

  /** 需澄清字段（无候选或后端标记）且用户未显式选择 → 执行前置闸。 */
  const unresolved = useMemo(() => {
    if (!inspection) return [];
    return inspection.proposals
      .filter((p) => (p.needs_clarification || p.candidates.length === 0)
        && !roleChoices[p.field])
      .map((p) => p.field);
  }, [inspection, roleChoices]);

  const hardBlocked = useMemo(
    () => (inspection?.receipt.findings ?? []).some((f) => f.severity === 'blocked'),
    [inspection],
  );

  const handleInspect = async () => {
    if (!sessionId || !sourceRef) return;
    setInspecting(true);
    setReceipt(null);
    setPreview(null);
    try {
      const res = await dataPreparationApi.inspect(
        { session_id: sessionId, source_ref: sourceRef, user_hints: roleChoices },
        { ownerToken },
      );
      setInspection(res);
      // 预填高置信提案为默认选择（用户可改）。
      const defaults: Record<string, string> = {};
      for (const p of res.proposals) {
        if (p.candidates.length > 0 && !p.needs_clarification) {
          defaults[p.field] = p.candidates[0].role;
        }
      }
      setRoleChoices(defaults);
    } catch (err) {
      addToast(describeApiError(err, 'inspect failed'), 'error');
    } finally {
      setInspecting(false);
    }
  };

  const handlePrepare = async () => {
    if (!sessionId || !inspection || unresolved.length > 0) return;
    setPreparing(true);
    try {
      // 步集 = 用户确认的角色映射（全部 source=user）+ auto 安全步（越界过滤）。
      const steps = Object.entries(roleChoices)
        .filter(([, role]) => role)
        .map(([field, role]) => ({ type: 'field_mapping', role, field, source: 'user' }));
      const planRes = await dataPreparationApi.createPlan(
        { session_id: sessionId, source_ref: sourceRef, steps, auto_steps: true },
        { ownerToken },
      );
      const pv = await dataPreparationApi.previewPlan(planRes.plan.plan_id, sessionId, { ownerToken });
      setPreview(pv);
      const exec = await dataPreparationApi.executePlan(planRes.plan.plan_id, sessionId, { ownerToken });
      setReceipt(exec.receipt);
      // 确认角色写回 descriptor（版本化）—— 输出 ref 执行时已携带，此处
      // 对原件同键写回，跨会话恢复链可读。
      try {
        await dataPreparationApi.bindFields(
          { session_id: sessionId, source_ref: sourceRef, roles: roleChoices },
          { ownerToken },
        );
      } catch {
        // 绑定写回是增值面（receipt 已携带确认）—— 失败只提示不回滚。
        addToast(t('clarifyHint'), 'warning');
      }
    } catch (err) {
      const detail = isApiError(err) ? (err.body as Record<string, unknown> | null)?.detail : undefined;
      const msg = detail !== undefined ? String(detail) : describeApiError(err, 'prepare failed');
      addToast(msg, 'error');
    } finally {
      setPreparing(false);
    }
  };

  const handleLoadToMap = async () => {
    if (!sessionId || !receipt) return;
    const layerId = `prep-${receipt.plan_hash}`;
    const placeholder: GeoJSONFeatureCollection = {
      type: 'FeatureCollection',
      features: [],
      metadata: { ref_id: receipt.output_ref },
    };
    addLayer({
      id: layerId,
      name: `prepared:${receipt.plan_hash}`,
      type: 'vector',
      visible: true,
      opacity: 1,
      group: 'reference',
      source: placeholder,
      _refId: receipt.output_ref,
      style: { color: '#16a34a' },
    });
    try {
      const geojson = await dataFabricApi.fetchRefGeoJSON(receipt.output_ref, sessionId, { ownerToken });
      if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
        updateLayer(layerId, { source: geojson });
      }
      addToast(t('loadSuccess'), 'success');
    } catch {
      addToast(t('loadFailed'), 'warning');
    }
  };

  const sampleFields = useMemo(() => {
    if (!inspection?.sample?.length) return [];
    return Object.keys(inspection.sample[0].properties).slice(0, 8);
  }, [inspection]);

  if (!sessionId) {
    return (
      <div className="p-3 text-meta text-ink-600">
        {tAny('sidebar.ds.noActiveSessionMaterialize')}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3" data-testid="prepare-flow">
      {/* ── 步骤 1：数据选择 + 检查 ─────────────────────────────── */}
      <section className="rounded border border-line">
        <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <span className="flex items-center gap-1.5 text-meta font-semibold text-ink">
            <FlaskConical size={14} />
            {t('title')}
          </span>
          <button
            type="button"
            className="flex items-center gap-1 text-meta text-ink-600 hover:text-ink"
            onClick={() => void refreshUploads()}
            data-testid="dp-refresh"
          >
            <RefreshCw size={12} />
            {t('refreshUploads')}
          </button>
        </header>
        <div className="flex flex-col gap-2 p-3">
          {uploads.length === 0 ? (
            <p className="text-meta text-ink-600" data-testid="dp-no-uploads">
              {t('noUploads')}
            </p>
          ) : (
            <label className="flex flex-col gap-1 text-meta text-ink-600">
              {t('selectSource')}
              <select
                className="rounded border border-line bg-white px-2 py-1 text-ink"
                value={sourceRef}
                onChange={(e) => setSourceRef(e.target.value)}
                data-testid="dp-source-select"
              >
                <option value="">—</option>
                {uploads.map((u) => (
                  <option key={u.ref} value={u.ref}>{u.label}</option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="btn btn-primary text-meta"
            disabled={!sourceRef || inspecting}
            onClick={() => void handleInspect()}
            data-testid="dp-inspect"
          >
            {inspecting ? t('inspecting') : t('inspect')}
          </button>
        </div>
      </section>

      {inspection && (
        <>
          {/* ── 步骤 2：收据 + 字段映射确认 ─────────────────────── */}
          <section className="rounded border border-line" data-testid="dp-receipt">
            <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
              {t('receiptTitle')}
            </header>
            <div className="flex flex-col gap-1.5 p-3 text-meta text-ink-700">
              <span>{t('rows', { count: inspection.receipt.row_count })}</span>
              {inspection.receipt.srs && (
                <span>{t('crs', { srs: inspection.receipt.srs, source: inspection.receipt.crs_source || '—' })}</span>
              )}
              {inspection.receipt.pk_field && (
                <span>{t('pk', { field: inspection.receipt.pk_field, status: inspection.receipt.pk_status })}</span>
              )}
              {inspection.receipt.findings.length > 0 && (
                <ul className="flex flex-col gap-1" data-testid="dp-findings">
                  {inspection.receipt.findings.map((f, i) => (
                    <li key={`${f.code}-${i}`} className={`rounded border px-2 py-1 ${SEVERITY_CLASS[f.severity] ?? SEVERITY_CLASS.ok}`}>
                      <b>{t(`severity${f.severity.charAt(0).toUpperCase()}${f.severity.slice(1)}`)}</b>
                      {' · '}
                      {f.message_zh || f.code}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="rounded border border-line" data-testid="dp-mapping">
            <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
              {t('mappingTitle')}
            </header>
            <div className="flex flex-col gap-1.5 p-3">
              {inspection.proposals.map((p) => (
                <FieldRoleRow
                  key={p.field}
                  proposal={p}
                  value={roleChoices[p.field] ?? ''}
                  onChange={(role) => setRoleChoices((prev) => ({ ...prev, [p.field]: role }))}
                />
              ))}
            </div>
          </section>

          {/* ── 步骤 3：样本预览 ────────────────────────────────── */}
          {inspection.sample.length > 0 && (
            <section className="rounded border border-line" data-testid="dp-sample">
              <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
                {t('sampleTitle', { count: inspection.sample.length })}
                {inspection.sample_truncated ? ` · ${t('sampleTruncated')}` : ''}
              </header>
              <div className="overflow-x-auto p-3">
                <table className="min-w-full text-meta">
                  <thead>
                    <tr className="text-left text-ink-600">
                      {sampleFields.map((f) => <th key={f} className="px-2 py-1">{f}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {inspection.sample.slice(0, 10).map((row, i) => (
                      <tr key={i} className="border-t border-line">
                        {sampleFields.map((f) => (
                          <td key={f} className="px-2 py-1 text-ink-800">
                            {String(row.properties[f] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* ── 步骤 4：准备执行 + 入图 ─────────────────────────── */}
          <section className="rounded border border-line" data-testid="dp-execute">
            <div className="flex flex-col gap-2 p-3">
              {hardBlocked && (
                <p className="rounded border border-red-300 bg-red-50 px-2 py-1 text-meta text-red-800" data-testid="dp-blocked-hint">
                  {t('blockedHint')}
                </p>
              )}
              {!hardBlocked && unresolved.length > 0 && (
                <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-meta text-amber-800" data-testid="dp-clarify-hint">
                  {t('clarifyHint')}{t('clarifyFields', { fields: unresolved.join(', ') })}
                </p>
              )}
              <button
                type="button"
                className="btn btn-primary text-meta"
                disabled={hardBlocked || unresolved.length > 0 || preparing}
                onClick={() => void handlePrepare()}
                data-testid="dp-prepare"
              >
                {preparing ? t('preparing') : t('prepare')}
              </button>

              {preview && (
                <p className="text-meta text-ink-600" data-testid="dp-preview">
                  {t('previewTitle', { rows: preview.sampled_rows })}
                  {Object.keys(preview.quality_delta_sample).length > 0 && (
                    <>
                      {' · '}
                      {t('qualityDelta', {
                        delta: Object.entries(preview.quality_delta_sample)
                          .map(([k, v]) => `${k}:${v > 0 ? '+' : ''}${v}`)
                          .join(' '),
                      })}
                    </>
                  )}
                </p>
              )}

              {receipt && (
                <div className="flex flex-col gap-2" data-testid="dp-receipt-done">
                  <p className="text-meta text-ink-800">
                    {t('receiptDone', { before: receipt.row_before, after: receipt.row_after })}
                  </p>
                  <p className="text-meta text-ink-600">{t('outputRef', { ref: receipt.output_ref })}</p>
                  <button
                    type="button"
                    className="btn btn-secondary text-meta"
                    onClick={() => void handleLoadToMap()}
                    data-testid="dp-load"
                  >
                    {t('loadToMap')}
                  </button>
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function FieldRoleRow({
  proposal, value, onChange,
}: {
  proposal: FieldProposal;
  value: string;
  onChange: (role: string) => void;
}) {
  const t = useT('dataPreparation');
  const needsPick = (proposal.needs_clarification || proposal.candidates.length === 0) && !value;
  const options = Array.from(new Set([
    ...proposal.candidates.map((c) => c.role),
    ...ALL_ROLES,
  ]));
  return (
    <label
      className={`flex items-center justify-between gap-2 rounded border px-2 py-1 text-meta ${
        needsPick ? 'border-amber-300 bg-amber-50' : 'border-line'
      }`}
      data-testid={`dp-role-${proposal.field}`}
    >
      <span className="flex items-center gap-2 text-ink-800">
        <b>{proposal.field}</b>
        <span className="text-ink-500">{proposal.dtype}</span>
        {needsPick && (
          <span className="rounded bg-amber-200 px-1 text-amber-900">
            {t('needsClarification')}
          </span>
        )}
      </span>
      <span className="flex items-center gap-1">
        {proposal.candidates[0] && (
          <span className="text-ink-500">
            {t('confidence', { pct: Math.round(proposal.candidates[0].confidence * 100) })}
          </span>
        )}
        <select
          className="rounded border border-line bg-white px-1 py-0.5 text-ink"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{t('noCandidate')}</option>
          {options.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </span>
    </label>
  );
}
