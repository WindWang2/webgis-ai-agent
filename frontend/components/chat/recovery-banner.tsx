'use client';

import { useEffect, useState } from 'react';
import { History } from 'lucide-react';
import { apiFetch } from '@/lib/api/transport';

/**
 * G22-P6（Pi 长会话状态连续性）—— 会话恢复可读性横幅。
 *
 * 会话恢复把 transcript 装回屏幕后，用户看不到「地图恢复到哪一版 / 哪些
 * 数据指针已失效 / 哪些人工约束仍保留 / 哪些任务还在跑」。本横幅把后端
 * `GET /chat/sessions/{id}?recovery=1` 附挂的摘要（build_recovery_summary，
 * 权威源现取、有界、不含载荷/凭据）拼成一行中文短句，挂在 transcript 上方。
 *
 * 诚实约定（与后端口径一致）：字段缺席 → 短语整条不出现（不撒谎）；
 * 技术 id（失效引用/任务 id/图层 id/active_frame）只进 title 诊断属性，
 * 绝不进可见文本。降级：无会话 / 未恢复 / 拉取失败 → 整条隐藏，绝不阻塞
 * 聊天面（同 SessionPlanPanel 的降级约定）。
 */

/** `?recovery=1` 附挂的摘要形状（app/services/chat/recovery_summary.py）。 */
export interface RecoverySummary {
  status?: string;
  recovered?: boolean;
  recovered_to_revision?: number | string | null;
  layer_count?: number;
  invalid_source_refs?: Array<{ source: string; ref: string }>;
  durable_jobs?: Array<{ id: string; status: string }>;
  user_constraints?: {
    locked_layers?: string[];
    locked_components?: number;
    hidden_layers?: string[];
    label_overrides?: number;
    active_frame?: string | null;
  };
}

interface Props {
  sessionId?: string | null;
  /** SEC-08：匿名会话经 X-Session-Token 回传所有权 token。 */
  ownerToken?: string | null;
}

/** 可见短句（每条一个元素，缺席即不渲染 —— 与「不撒谎」约定一一对应）。 */
function visiblePhrases(s: RecoverySummary): string[] {
  const phrases: string[] = [];
  const revision = s.recovered_to_revision;
  phrases.push(
    revision === null || revision === undefined
      ? '会话已恢复'
      : `会话已恢复 · 地图 revision ${revision}`,
  );
  const invalid = s.invalid_source_refs ?? [];
  if (invalid.length > 0) phrases.push(`${invalid.length} 个数据引用已失效`);
  const jobs = s.durable_jobs ?? [];
  if (jobs.length > 0) {
    // 非终态集合含 cancelling —— 如实披露「取消中」，不把取消伪装成运行。
    const cancelling = jobs.some((j) => j.status === 'cancelling');
    phrases.push(`${jobs.length} 个任务继续运行${cancelling ? '/取消中' : ''}`);
  }
  const uc = s.user_constraints ?? {};
  const hidden = uc.hidden_layers?.length ?? 0;
  const locked = uc.locked_layers?.length ?? 0;
  const labels = uc.label_overrides ?? 0;
  if (hidden > 0 || locked > 0 || labels > 0) {
    phrases.push(`已保留人工约束（隐藏 ${hidden} 层 · 锁定 ${locked} 层 · 标注 ${labels} 条）`);
  }
  return phrases;
}

/** title 诊断串：技术 id 只在这里出现（鼠标悬停 / 读屏细读时可见）。 */
function diagnosticsTitle(s: RecoverySummary): string {
  const uc = s.user_constraints ?? {};
  const bits: string[] = [`revision=${s.recovered_to_revision ?? '?'}`, `layers=${s.layer_count ?? 0}`];
  const invalid = s.invalid_source_refs ?? [];
  if (invalid.length > 0) {
    bits.push('失效引用: ' + invalid.map((r) => `${r.source}→${r.ref}`).join('、'));
  }
  const jobs = s.durable_jobs ?? [];
  if (jobs.length > 0) {
    bits.push('任务: ' + jobs.map((j) => `${j.id}:${j.status}`).join('、'));
  }
  if (uc.locked_layers?.length || uc.locked_components) {
    const comps = uc.locked_components ? `（组件锁 ${uc.locked_components} 个）` : '';
    bits.push(`锁定: ${(uc.locked_layers ?? []).join('、')}${comps}`);
  }
  if (uc.hidden_layers?.length) bits.push('隐藏: ' + uc.hidden_layers.join('、'));
  if (uc.active_frame) bits.push(`active_frame=${uc.active_frame}`);
  return bits.join('；');
}

export function RecoveryBanner({ sessionId, ownerToken }: Props) {
  const [summary, setSummary] = useState<RecoverySummary | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    // 会话切换先清空：陈旧会话的摘要绝不涂到新会话的横幅上。
    setSummary(null);
    apiFetch<{ recovery?: RecoverySummary }>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}?recovery=1`,
      { ownerToken },
    )
      .then((data) => {
        if (!cancelled) setSummary(data?.recovery ?? null);
      })
      .catch(() => {
        if (!cancelled) setSummary(null); // 降级：隐藏而非报错
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, ownerToken]);

  // 只在权威摘要明确说「已恢复」时出现 —— 未恢复 / 缺席 / 失败一律整条隐藏。
  if (!summary || summary.recovered !== true) return null;

  return (
    <div
      data-testid="recovery-banner"
      title={diagnosticsTitle(summary)}
      className="mx-panel mt-2 flex flex-wrap items-center gap-x-1.5 rounded-md border border-edge-subtle bg-surface-raised px-2.5 py-1.5"
    >
      <History className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
      {visiblePhrases(summary).map((phrase, idx) => (
        <span key={phrase} className="text-meta text-ink-muted">
          {idx > 0 && <span className="mr-1.5">·</span>}
          {phrase}
        </span>
      ))}
    </div>
  );
}

export default RecoveryBanner;
