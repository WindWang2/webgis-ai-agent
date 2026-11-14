/**
 * G36 视觉附图草稿 store（ADR-0291）。
 *
 * 会话内聊天附件的**唯一前端事实源**：上传中/已就绪/失败状态、缩略图
 * URL、移除。发送时由 use-sse-stream 一次性消费（consume）—— 消费后
 * 草稿清空，evidence 转为 bound（服务端绑定消息）。
 *
 * 纪律：cap 4（与服务端 ChatRequest.attachments max_length 一致）；
 * 会话切换清空（旧会话草稿不串入新会话 —— 与 cartographic observation
 * 的 INV-2 同语义）。
 */
import { create } from 'zustand';

export interface VisualAttachmentDraft {
  evidenceId: string;
  sourceType: 'map_viewport' | 'map_region' | 'reference_upload';
  label: string;
  /** 本地预览（objectURL；仅 UI 用，不入请求体）。 */
  thumbUrl?: string;
  /** 上传中占位（attach 时置 true，回执后替换/剔除）。 */
  uploading?: boolean;
  error?: string;
  mapspecRevision?: number | null;
  viewportHash?: string;
}

interface VisualAttachState {
  sessionId: string | null;
  drafts: VisualAttachmentDraft[];
  /** 发起一次草稿采集（上传完成由调用方 add/update）。 */
  begin: (sessionId: string) => void;
  setSession: (sessionId: string | null) => void;
  addDraft: (draft: VisualAttachmentDraft) => void;
  updateDraft: (evidenceId: string, patch: Partial<VisualAttachmentDraft>) => void;
  removeDraft: (evidenceId: string) => void;
  /** 发送时消费：返回当前草稿并清空（原子）。 */
  consumeDrafts: () => VisualAttachmentDraft[];
}

const MAX_DRAFTS = 4;

export const useVisualAttachStore = create<VisualAttachState>((set, get) => ({
  sessionId: null,
  drafts: [],
  begin: (sessionId) => {
    if (get().sessionId !== sessionId) {
      set({ sessionId, drafts: [] });
    }
  },
  setSession: (sessionId) => {
    if (get().sessionId === sessionId) return;
    set({ sessionId, drafts: [] });
  },
  addDraft: (draft) => {
    const { sessionId, drafts } = get();
    if (!sessionId) return;
    if (drafts.some((d) => d.evidenceId === draft.evidenceId)) return;
    if (drafts.length >= MAX_DRAFTS) return;
    set({ drafts: [...drafts, draft].slice(0, MAX_DRAFTS) });
  },
  updateDraft: (evidenceId, patch) => {
    set({
      drafts: get().drafts.map((d) =>
        d.evidenceId === evidenceId ? { ...d, ...patch } : d),
    });
  },
  removeDraft: (evidenceId) => {
    set({ drafts: get().drafts.filter((d) => d.evidenceId !== evidenceId) });
  },
  consumeDrafts: () => {
    // review P3#17：只消费就绪草稿 —— 上传中/失败项保留在 store（上传完成
    // 后仍是本会话可用的 draft 证据，不成为孤儿）。
    const all = get().drafts;
    const ready = all.filter((d) => !d.uploading && !d.error);
    if (ready.length) set({ drafts: all.filter((d) => d.uploading || d.error) });
    return ready;
  },
}));
