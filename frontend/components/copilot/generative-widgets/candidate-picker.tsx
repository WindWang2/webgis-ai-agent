'use client';

/**
 * CandidatePickerWidget — 空间候选范围确认卡（ADR-0194 kind=candidate_picker）。
 * 单选/多选 + 确认 → widget_reply {selected: string[]}。
 */
import { useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import { sendWidgetReply } from '@/lib/copilot/widget-binding';

interface Candidate {
  id: string;
  label: string;
  stats?: Record<string, string | number | boolean>;
}

interface PickerPayload {
  candidates: Candidate[];
  selection_mode: 'single' | 'multi';
}

export function CandidatePickerWidget({
  spec,
  onReply,
}: {
  spec: WidgetSpec;
  onReply?: (value: unknown) => void;
}) {
  const payload = spec.payload as unknown as PickerPayload;
  const [selected, setSelected] = useState<string[]>([]);
  const t = useT('copilot');

  const toggle = (id: string) => {
    setSelected((prev) =>
      payload.selection_mode === 'single'
        ? prev.includes(id)
          ? []
          : [id]
        : prev.includes(id)
          ? prev.filter((x) => x !== id)
          : [...prev, id],
    );
  };

  return (
    <div data-testid="candidate-picker-body">
      <ul className="space-y-1">
        {payload.candidates.map((c) => (
          <li key={c.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs text-ink hover:bg-surface-hover">
              <input
                type={payload.selection_mode === 'single' ? 'radio' : 'checkbox'}
                name={`candidate-${spec.widget_id}`}
                data-testid={`candidate-${c.id}`}
                checked={selected.includes(c.id)}
                onChange={() => toggle(c.id)}
              />
              <span>{c.label}</span>
              {c.stats && (
                <span className="ml-auto text-[10px] text-ink-muted">
                  {Object.entries(c.stats)
                    .slice(0, 3)
                    .map(([k, v]) => `${k}:${String(v)}`)
                    .join(' ')}
                </span>
              )}
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid="candidate-confirm"
        disabled={selected.length === 0}
        onClick={() => sendWidgetReply(spec, { selected }, onReply)}
        className="mt-2 w-full rounded-md bg-sky-600 px-2 py-1 text-xs text-white hover:bg-sky-500 disabled:opacity-40"
      >
        {t('confirmCandidates', { count: selected.length })}
      </button>
    </div>
  );
}
