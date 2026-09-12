'use client';

import { Play } from 'lucide-react';
import { InlineNotice } from '@/components/shared/inline-notice';

/**
 * Lakehouse 查询/构建表单（P4）。
 *
 * 表单 → 请求体的映射带 schema 预校验（与后端 Pydantic 约束对齐）：
 * - window：至少一个有限切片、索引非负、start ≤ stop（路由 422 的前端等价）；
 * - labeled：标签/bbox/index_slices 至少一种选择；
 * - scan：bbox 四元、max_rows ≤ 200_000；
 * - revise：≥1 条修订（band/time_index/source）；
 * - rs：≥1 个源，role ∈ optical|sar|cloud_mask|quality_mask。
 * 校验失败本地渲染 InlineNotice，不出网络请求。
 */

export type QueryMode = 'window' | 'labeled' | 'scan' | 'revise' | 'rs';

export const QUERY_MODES: Array<{ key: QueryMode; label: string }> = [
  { key: 'window', label: '窗口读' },
  { key: 'labeled', label: '标签读' },
  { key: 'scan', label: '矢量扫描' },
  { key: 'revise', label: '修订' },
  { key: 'rs', label: 'RS 组装' },
];

const RS_ROLES = ['optical', 'sar', 'cloud_mask', 'quality_mask'] as const;

const inputClass =
  'min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption text-ink';
const labelClass = 'flex items-center gap-1.5 text-caption text-ink-secondary';

interface SliceInputProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

/** 索引切片输入：`start, stop` 文本形态（如 `0, 8`）。 */
function SliceInput({ label, value, onChange, placeholder }: SliceInputProps) {
  return (
    <label className={labelClass}>
      <span className="w-10 shrink-0">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? 'start, stop'}
        aria-label={label}
        className={inputClass}
      />
    </label>
  );
}

export function parseSlice(text: string): [number, number] | null {
  const parts = text.split(',').map((s) => s.trim()).filter(Boolean);
  if (parts.length !== 2) return null;
  const start = Number(parts[0]);
  const stop = Number(parts[1]);
  if (!Number.isInteger(start) || !Number.isInteger(stop)) return null;
  return [start, stop];
}

export function parseTags(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function parseBbox(text: string): [number, number, number, number] | null {
  const parts = text.split(',').map((s) => s.trim()).filter(Boolean);
  if (parts.length !== 4) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return [nums[0], nums[1], nums[2], nums[3]];
}

export interface QueryFormValue {
  mode: QueryMode;
  ref: string;
  window: { time: string; y: string; x: string };
  labeled: {
    time: string;
    band: string;
    polarization: string;
    vertical: string;
    model: string;
    scenario: string;
    bbox: string;
    indexSlices: string;
    maxCells: number;
  };
  scan: { ref: string; bbox: string; columns: string; maxRows: number };
  revise: { title: string; band: string; timeIndex: number; source: string };
  rs: { title: string; time: string; source: string; role: string; band: string; polarization: string };
}

export const EMPTY_FORM: QueryFormValue = {
  mode: 'window',
  ref: '',
  window: { time: '', y: '', x: '' },
  labeled: {
    time: '',
    band: '',
    polarization: '',
    vertical: '',
    model: '',
    scenario: '',
    bbox: '',
    indexSlices: '',
    maxCells: 8_000_000,
  },
  scan: { ref: '', bbox: '', columns: '', maxRows: 50_000 },
  revise: { title: 'cube 修订', band: '', timeIndex: 0, source: '' },
  rs: { title: 'rs cube', time: '', source: '', role: 'optical', band: '', polarization: '' },
};

export interface BuiltRequest {
  kind: QueryFormValue['mode'];
  /** 请求路径（lakehouseApi 之外的描述性标签，历史记录用）。 */
  ref: string;
  label: string;
  payload: Record<string, unknown>;
}

/** 表单 → 请求体（带校验）。返回 {error} 或 {request}。 */
export function buildRequest(form: QueryFormValue, sessionId: string): { error?: string; request?: BuiltRequest } {
  switch (form.mode) {
    case 'window': {
      if (!form.ref) return { error: '请填写 cube ref（ref:cube/…）' };
      const slices: Record<string, [number, number]> = {};
      for (const [dim, text] of Object.entries(form.window)) {
        if (!text.trim()) continue;
        const parsed = parseSlice(text);
        if (!parsed) return { error: `${dim} 切片格式应为「start, stop」整数` };
        if (parsed[0] < 0 || parsed[1] < 0) return { error: `${dim} 切片必须非负` };
        if (parsed[0] > parsed[1]) return { error: `${dim} 切片 start 不能大于 stop` };
        slices[dim] = parsed;
      }
      if (Object.keys(slices).length === 0) {
        return { error: '窗口读至少要给一个有限切片（time/y/x）——整 cube 读取被拒绝' };
      }
      return {
        request: {
          kind: 'window',
          ref: form.ref,
          label: `窗口读 ${Object.entries(slices).map(([d, [s, e]]) => `${d}=[${s},${e})`).join(' ')}`,
          payload: { session_id: sessionId, ref: form.ref, ...slices },
        },
      };
    }
    case 'labeled': {
      if (!form.ref) return { error: '请填写 cube ref（ref:cube/…）' };
      const l = form.labeled;
      const payload: Record<string, unknown> = { session_id: sessionId, ref: form.ref };
      const dims: Array<[string, string]> = [
        ['time', l.time],
        ['band', l.band],
        ['polarization', l.polarization],
        ['vertical', l.vertical],
        ['model', l.model],
        ['scenario', l.scenario],
      ];
      let selected = 0;
      for (const [dim, text] of dims) {
        const tags = parseTags(text);
        if (tags.length) {
          payload[dim] = tags;
          selected += 1;
        }
      }
      if (l.bbox.trim()) {
        const bbox = parseBbox(l.bbox);
        if (!bbox) return { error: 'bbox 应为「minx, miny, maxx, maxy」四个数字' };
        payload.bbox = bbox;
        selected += 1;
      }
      if (l.indexSlices.trim()) {
        const slices: Record<string, [number, number]> = {};
        for (const part of l.indexSlices.split(';')) {
          const [dim, range] = part.split(':').map((s) => s.trim());
          if (!dim || !range) return { error: '索引切片格式应为「dim:start, stop」，多组用分号分隔' };
          const parsed = parseSlice(range);
          if (!parsed) return { error: `索引切片 ${dim} 格式应为「start, stop」整数` };
          slices[dim] = parsed;
        }
        payload.index_slices = slices;
        selected += 1;
      }
      if (selected === 0) return { error: '标签读至少需要标签 / bbox / 索引切片之一' };
      if (l.maxCells < 1 || l.maxCells > 8_000_000) return { error: 'max_cells 范围 1 – 8,000,000' };
      payload.max_cells = l.maxCells;
      return {
        request: {
          kind: 'labeled',
          ref: form.ref,
          label: `标签读 ${dims.filter(([, t]) => t.trim()).map(([d]) => d).join('/')}${l.bbox.trim() ? ' +bbox' : ''}`,
          payload,
        },
      };
    }
    case 'scan': {
      const s = form.scan;
      const ref = s.ref || form.ref;
      if (!ref) return { error: '请填写矢量 ref（ref:fabric-parquet/…）' };
      const bbox = parseBbox(s.bbox);
      if (!bbox) return { error: 'bbox 应为「minx, miny, maxx, maxy」四个数字' };
      if (s.maxRows < 1 || s.maxRows > 200_000) return { error: 'max_rows 范围 1 – 200,000' };
      return {
        request: {
          kind: 'scan',
          ref,
          label: `扫描 ${ref}`,
          payload: {
            session_id: sessionId,
            ref,
            bbox,
            max_rows: s.maxRows,
            ...(parseTags(s.columns).length ? { columns: parseTags(s.columns) } : {}),
          },
        },
      };
    }
    case 'revise': {
      if (!form.ref) return { error: '请填写要修订的 cube ref' };
      if (!form.revise.band || !form.revise.source) return { error: '修订需要 band 与 source' };
      if (form.revise.timeIndex < 0) return { error: 'time_index 必须非负' };
      return {
        request: {
          kind: 'revise',
          ref: form.ref,
          label: `修订 ${form.revise.band}@${form.revise.timeIndex}`,
          payload: {
            session_id: sessionId,
            ref: form.ref,
            title: form.revise.title || 'cube revision',
            updates: [
              {
                band: form.revise.band,
                time_index: form.revise.timeIndex,
                source: form.revise.source,
              },
            ],
          },
        },
      };
    }
    case 'rs': {
      const r = form.rs;
      if (!r.time || !r.source) return { error: 'RS 组装至少需要一组 time + source' };
      if (!RS_ROLES.includes(r.role as (typeof RS_ROLES)[number])) {
        return { error: `role 必须是 ${RS_ROLES.join(' / ')} 之一` };
      }
      return {
        request: {
          kind: 'rs',
          ref: r.source,
          label: `RS 组装（${r.role}）`,
          payload: {
            session_id: sessionId,
            title: r.title || 'rs cube',
            sources: [
              {
                time: r.time,
                source: r.source,
                role: r.role,
                ...(r.band ? { band: r.band } : {}),
                ...(r.polarization ? { polarization: r.polarization } : {}),
              },
            ],
          },
        },
      };
    }
  }
}

export interface QueryFormProps {
  value: QueryFormValue;
  onChange: (v: QueryFormValue) => void;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
  /** ref 解析失败等操作级提示。 */
  hint: string | null;
}

const MODE_DESC: Record<QueryMode, string> = {
  window: '索引切片窗口读（zarr chunk 粒度）——至少一个有限切片',
  labeled: '标签级窗口读（time/band/polarization/vertical/model/scenario）',
  scan: 'fabric-parquet bbox 窗口扫描（row-group 剪枝）',
  revise: '修订时间片（硬链接 CoW fork，源 store 不动）',
  rs: '光学/SAR/掩膜多源 → 对齐 labeled cube',
};

export function QueryForm({ value, onChange, onSubmit, submitting, error, hint }: QueryFormProps) {
  const patch = (p: Partial<QueryFormValue>) => onChange({ ...value, ...p });
  return (
    <div className="space-y-2 border-b border-edge-subtle px-panel py-2" data-testid="lakehouse-query-form">
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="查询类型">
        {QUERY_MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            role="radio"
            aria-checked={value.mode === m.key}
            onClick={() => patch({ mode: m.key })}
            className={`rounded-pill px-2 py-0.5 text-caption transition-colors ${
              value.mode === m.key
                ? 'bg-status-accent-soft font-medium text-status-accent'
                : 'bg-surface-sunken text-ink-secondary hover:bg-surface-hover'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <p className="text-micro text-ink-muted">{MODE_DESC[value.mode]}</p>

      {(value.mode === 'window' || value.mode === 'labeled' || value.mode === 'revise') && (
        <label className={labelClass}>
          <span className="w-10 shrink-0">ref</span>
          <input
            type="text"
            value={value.ref}
            onChange={(e) => patch({ ref: e.target.value })}
            placeholder="ref:cube/…"
            aria-label="cube ref"
            className={`${inputClass} font-mono`}
          />
        </label>
      )}

      {value.mode === 'window' && (
        <div className="space-y-1.5">
          <SliceInput label="time" value={value.window.time} onChange={(v) => patch({ window: { ...value.window, time: v } })} />
          <SliceInput label="y" value={value.window.y} onChange={(v) => patch({ window: { ...value.window, y: v } })} />
          <SliceInput label="x" value={value.window.x} onChange={(v) => patch({ window: { ...value.window, x: v } })} />
        </div>
      )}

      {value.mode === 'labeled' && (
        <div className="space-y-1.5">
          {(
            [
              ['time', 'time（逗号分隔）'],
              ['band', 'band（逗号分隔）'],
              ['polarization', 'polarization（≤16 项）'],
              ['vertical', 'vertical'],
              ['model', 'model'],
              ['scenario', 'scenario'],
            ] as Array<[keyof QueryFormValue['labeled'], string]>
          ).map(([key, label]) => (
            <label key={key} className={labelClass}>
              <span className="w-24 shrink-0 truncate" title={label}>{label}</span>
              <input
                type="text"
                value={String(value.labeled[key])}
                onChange={(e) => patch({ labeled: { ...value.labeled, [key]: e.target.value } })}
                aria-label={label}
                className={inputClass}
              />
            </label>
          ))}
          <SliceInput
            label="bbox"
            value={value.labeled.bbox}
            onChange={(v) => patch({ labeled: { ...value.labeled, bbox: v } })}
            placeholder="minx, miny, maxx, maxy"
          />
          <SliceInput
            label="idx"
            value={value.labeled.indexSlices}
            onChange={(v) => patch({ labeled: { ...value.labeled, indexSlices: v } })}
            placeholder="time:0, 8; y:0, 64"
          />
        </div>
      )}

      {value.mode === 'scan' && (
        <div className="space-y-1.5">
          <label className={labelClass}>
            <span className="w-10 shrink-0">ref</span>
            <input
              type="text"
              value={value.scan.ref}
              onChange={(e) => patch({ scan: { ...value.scan, ref: e.target.value } })}
              placeholder="ref:fabric-parquet/…"
              aria-label="矢量 ref"
              className={`${inputClass} font-mono`}
            />
          </label>
          <SliceInput
            label="bbox"
            value={value.scan.bbox}
            onChange={(v) => patch({ scan: { ...value.scan, bbox: v } })}
            placeholder="minx, miny, maxx, maxy"
          />
          <label className={labelClass}>
            <span className="w-10 shrink-0">rows</span>
            <input
              type="number"
              value={value.scan.maxRows}
              min={1}
              max={200000}
              onChange={(e) => patch({ scan: { ...value.scan, maxRows: Number(e.target.value) } })}
              aria-label="最大行数"
              className={inputClass}
            />
          </label>
        </div>
      )}

      {value.mode === 'revise' && (
        <div className="space-y-1.5">
          <label className={labelClass}>
            <span className="w-16 shrink-0">band</span>
            <input
              type="text"
              value={value.revise.band}
              onChange={(e) => patch({ revise: { ...value.revise, band: e.target.value } })}
              aria-label="修订 band"
              className={inputClass}
            />
          </label>
          <label className={labelClass}>
            <span className="w-16 shrink-0">time_idx</span>
            <input
              type="number"
              value={value.revise.timeIndex}
              min={0}
              onChange={(e) => patch({ revise: { ...value.revise, timeIndex: Number(e.target.value) } })}
              aria-label="修订时间步"
              className={inputClass}
            />
          </label>
          <label className={labelClass}>
            <span className="w-16 shrink-0">source</span>
            <input
              type="text"
              value={value.revise.source}
              onChange={(e) => patch({ revise: { ...value.revise, source: e.target.value } })}
              placeholder="新时间片来源路径 / ref"
              aria-label="修订来源"
              className={`${inputClass} font-mono`}
            />
          </label>
        </div>
      )}

      {value.mode === 'rs' && (
        <div className="space-y-1.5">
          <label className={labelClass}>
            <span className="w-16 shrink-0">role</span>
            <select
              value={value.rs.role}
              onChange={(e) => patch({ rs: { ...value.rs, role: e.target.value } })}
              aria-label="源角色"
              className={inputClass}
            >
              {RS_ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            <span className="w-16 shrink-0">time</span>
            <input
              type="text"
              value={value.rs.time}
              onChange={(e) => patch({ rs: { ...value.rs, time: e.target.value } })}
              placeholder="2026-09-11"
              aria-label="源时间"
              className={inputClass}
            />
          </label>
          <label className={labelClass}>
            <span className="w-16 shrink-0">source</span>
            <input
              type="text"
              value={value.rs.source}
              onChange={(e) => patch({ rs: { ...value.rs, source: e.target.value } })}
              placeholder="路径 / ref:fabric-parquet/…"
              aria-label="源位置"
              className={`${inputClass} font-mono`}
            />
          </label>
          {value.rs.role === 'optical' && (
            <label className={labelClass}>
              <span className="w-16 shrink-0">band</span>
              <input
                type="text"
                value={value.rs.band}
                onChange={(e) => patch({ rs: { ...value.rs, band: e.target.value } })}
                placeholder="B04"
                aria-label="波段"
                className={inputClass}
              />
            </label>
          )}
          {value.rs.role === 'sar' && (
            <label className={labelClass}>
              <span className="w-16 shrink-0">pol</span>
              <input
                type="text"
                value={value.rs.polarization}
                onChange={(e) => patch({ rs: { ...value.rs, polarization: e.target.value } })}
                placeholder="VV"
                aria-label="极化"
                className={inputClass}
              />
            </label>
          )}
        </div>
      )}

      {hint && <InlineNotice variant="info">{hint}</InlineNotice>}
      {error && <InlineNotice variant="error">{error}</InlineNotice>}
      <button
        type="button"
        onClick={onSubmit}
        disabled={submitting}
        data-testid="lakehouse-query-submit"
        className="flex w-full items-center justify-center gap-1.5 rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
      >
        <Play size={12} aria-hidden />
        {submitting ? '执行中…' : '执行查询'}
      </button>
    </div>
  );
}
