'use client';

import { useState } from 'react';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { InlineNotice } from '@/components/shared/inline-notice';
import type { ReplayMode, WorkflowRunDetail } from '@/lib/api/project';
import { useAuthUser } from '@/lib/auth/use-auth-user';
import { REPLAY_MODE_COPY, shouldOfferResume } from '@/lib/workflow/recovery';

export interface RecoveryActionsProps {
  run: WorkflowRunDetail | null;
  busy: boolean;
  error: string | null;
  onReplay: (mode: ReplayMode) => void;
  onResume: () => void;
}

export function RecoveryActions({ run, busy, error, onReplay, onResume }: RecoveryActionsProps) {
  const [mode, setMode] = useState<ReplayMode>('exact');
  const offerResume = shouldOfferResume(run);
  // #528：回放/续跑走后端写路径（#501 要求认证）——匿名用户禁用并提示登录，
  // 避免裸 401 无引导的 toast。
  const authUser = useAuthUser();
  const writeLocked = !authUser;

  return (
    <section aria-labelledby="wf-recovery-heading" className="space-y-2">
      <h3 id="wf-recovery-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
        恢复
      </h3>
      {writeLocked && (
        <p className="text-[11px] text-[var(--theme-text-muted)]">
          回放 / 续跑需要登录账号 — 请先在 设置 → 账户 登录
        </p>
      )}
      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="sr-only">回放模式</legend>
        {(Object.keys(REPLAY_MODE_COPY) as ReplayMode[]).map((key) => {
          const copy = REPLAY_MODE_COPY[key];
          const id = `replay-mode-${key}`;
          return (
            <label
              key={key}
              htmlFor={id}
              className="flex cursor-pointer gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-input)] px-2 py-1.5"
            >
              <input
                id={id}
                type="radio"
                name="replay-mode"
                value={key}
                checked={mode === key}
                onChange={() => setMode(key)}
                className="mt-0.5"
              />
              <span className="min-w-0">
                <span className="block text-[12px] font-medium text-[var(--theme-text-primary)]">{copy.label}</span>
                <span className="block text-[11px] leading-snug text-[var(--theme-text-muted)]">{copy.description}</span>
              </span>
            </label>
          );
        })}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <ConfirmAction
          label={REPLAY_MODE_COPY[mode].label}
          confirmLabel={`确认${REPLAY_MODE_COPY[mode].label}？`}
          onConfirm={() => onReplay(mode)}
          disabled={busy || writeLocked || !run}
          title={writeLocked ? '需要登录账号（设置 → 账户）' : undefined}
          className="border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] text-[var(--theme-text-primary)] hover:bg-[var(--theme-bg-hover)] hover:text-[var(--theme-text-primary)] dark:text-[var(--theme-text-primary)]"
        />
        {offerResume && (
          <ConfirmAction
            label="尝试续跑"
            confirmLabel="确认从已完成步骤续跑？"
            onConfirm={onResume}
            disabled={busy || writeLocked || !run}
            title={writeLocked ? '需要登录账号（设置 → 账户）' : undefined}
          />
        )}
      </div>
      {busy && (
        <p role="status" className="text-[11px] text-[var(--theme-text-muted)]">
          正在等待后端确认…
        </p>
      )}
      {error && <InlineNotice variant="warning">{error}</InlineNotice>}
    </section>
  );
}

export default RecoveryActions;
