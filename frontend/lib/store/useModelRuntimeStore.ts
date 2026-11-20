/**
 * G37（会话模型运行配置）：model_status SSE 事件的会话级最新投影。
 *
 * 纪律：这只是「本 turn 实际生效了什么」的实时旁证；权威事实仍在服务端
 * 回执（GET /chat/sessions/{id}/model 的 last_turn 读真实 DB 行）。这里
 * 绝不保存模型选择本身 —— 选择只来自用户经 PUT 的已验证请求。
 */
'use client';

import { create } from 'zustand';

export interface ModelStatusPayload {
  session_id: string;
  turn_id: string;
  status: string;
  ok: boolean;
  mode: string;
  requested: { provider: string; model: string } | null;
  effective: { provider: string; model: string } | null;
  thinking: {
    requested: string | null;
    effective: string | null;
    controllable: boolean;
  };
  reason_code: string | null;
  reason_detail: string | null;
  catalog_generation: string;
}

interface ModelRuntimeState {
  /** sessionId → 最近一次 model_status（null = 尚未观测）。 */
  bySession: Record<string, ModelStatusPayload>;
  observe: (payload: ModelStatusPayload) => void;
  clear: (sessionId: string) => void;
}

export const useModelRuntimeStore = create<ModelRuntimeState>()((set) => ({
  bySession: {},
  observe: (payload) =>
    set((state) => ({
      bySession: { ...state.bySession, [payload.session_id]: payload },
    })),
  clear: (sessionId) =>
    set((state) => {
      if (!(sessionId in state.bySession)) return state;
      const next = { ...state.bySession };
      delete next[sessionId];
      return { ...state, bySession: next };
    }),
}));

/** 宽松解析（未知字段/坏形状 → null，绝不抛给 SSE 消费循环）。 */
export function parseModelStatus(data: unknown): ModelStatusPayload | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (typeof d.session_id !== 'string' || typeof d.turn_id !== 'string') return null;
  const pair = (v: unknown) =>
    v && typeof v === 'object' &&
    typeof (v as Record<string, unknown>).model === 'string'
      ? {
          provider: String((v as Record<string, unknown>).provider ?? ''),
          model: String((v as Record<string, unknown>).model),
        }
      : null;
  return {
    session_id: d.session_id,
    turn_id: d.turn_id,
    status: String(d.status ?? ''),
    ok: d.ok === true,
    mode: String(d.mode ?? ''),
    requested: pair(d.requested),
    effective: pair(d.effective),
    thinking: {
      requested:
        d.thinking && typeof d.thinking === 'object'
          ? ((d.thinking as Record<string, unknown>).requested as string | null) ?? null
          : null,
      effective:
        d.thinking && typeof d.thinking === 'object'
          ? ((d.thinking as Record<string, unknown>).effective as string | null) ?? null
          : null,
      controllable:
        d.thinking && typeof d.thinking === 'object'
          ? (d.thinking as Record<string, unknown>).controllable === true
          : false,
    },
    reason_code: typeof d.reason_code === 'string' ? d.reason_code : null,
    reason_detail: typeof d.reason_detail === 'string' ? d.reason_detail : null,
    catalog_generation: String(d.catalog_generation ?? ''),
  };
}
