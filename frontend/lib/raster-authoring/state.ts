/** G20 面板纯状态机：预览/待提交/已提交/失败 + 快速点击的过期响应抑制。 */
import type {
  RasterErrorPayload,
  RasterExpressionDraft,
  RasterProbeResult,
  RasterReceipt,
} from './contracts';
import type { RasterExpressionSpec } from './contracts';

export type RasterPhase = 'idle' | 'loading' | 'preview' | 'pending' | 'committed' | 'failed';

export interface RasterAuthoringState {
  phase: RasterPhase;
  draft: RasterExpressionDraft | null;
  committed: RasterExpressionSpec | null;
  committedReceipt: RasterReceipt | null;
  previewReceipt: RasterReceipt | null;
  error: RasterErrorPayload | null;
  errorField: string | null;
  /** 单调递增的像元请求序列；只接受与最新 seq 相同的响应。 */
  probeSeq: number;
  probe: RasterProbeResult | null;
  probePending: boolean;
}

export function initialState(): RasterAuthoringState {
  return {
    phase: 'idle', draft: null, committed: null,
    committedReceipt: null, previewReceipt: null,
    error: null, errorField: null,
    probeSeq: 0, probe: null, probePending: false,
  };
}

export type RasterAction =
  | { type: 'reset' }
  | { type: 'descriptor-loaded'; draft: RasterExpressionDraft; committed: RasterExpressionSpec | null }
  | { type: 'external-committed'; draft: RasterExpressionDraft; committed: RasterExpressionSpec }
  | { type: 'draft-changed'; draft: RasterExpressionDraft }
  | { type: 'preview-start' }
  | { type: 'preview-ok'; receipt: RasterReceipt }
  | { type: 'commit-start' }
  | { type: 'commit-ok'; receipt: RasterReceipt }
  | { type: 'failed'; error: RasterErrorPayload }
  | { type: 'restore-committed'; draft: RasterExpressionDraft }
  | { type: 'probe-start'; seq: number }
  | { type: 'probe-ok'; seq: number; result: RasterProbeResult }
  | { type: 'probe-failed'; seq: number; error: RasterErrorPayload };

export function reduceRasterAuthoring(
  state: RasterAuthoringState,
  action: RasterAction,
): RasterAuthoringState {
  switch (action.type) {
    case 'reset':
      return initialState();
    case 'descriptor-loaded':
      return {
        ...state, phase: action.committed ? 'committed' : 'idle',
        draft: action.draft, committed: action.committed,
        error: null, errorField: null,
      };
    case 'external-committed':
      // 刷新 / 撤销 / 另一通道提交：只接受权威 style_id，临时草稿丢弃。
      return {
        ...state, phase: 'committed', draft: action.draft,
        committed: action.committed, previewReceipt: null,
        error: null, errorField: null,
      };
    case 'draft-changed':
      return {
        ...state, draft: action.draft,
        // 拖动 = 临时预览，不污染持久版本；只有提交成功才回到 committed。
        phase: 'preview', previewReceipt: null,
        error: null, errorField: null,
      };
    case 'preview-start':
      return { ...state, phase: 'pending', error: null, errorField: null };
    case 'preview-ok':
      return { ...state, phase: 'preview', previewReceipt: action.receipt };
    case 'commit-start':
      return { ...state, phase: 'pending', error: null, errorField: null };
    case 'commit-ok':
      return {
        ...state, phase: 'committed',
        committed: action.receipt.expression,
        committedReceipt: action.receipt,
        previewReceipt: null,
        error: null, errorField: null,
      };
    case 'failed':
      return {
        ...state, phase: 'failed',
        error: action.error,
        errorField: action.error.field ?? null,
      };
    case 'restore-committed':
      if (!state.committed) return state;
      // 失败/预览回滚到**最后一次确认**的权威表达，临时草稿不残留。
      return {
        ...state, phase: 'committed', draft: action.draft,
        previewReceipt: null, error: null, errorField: null,
      };
    case 'probe-start':
      return { ...state, probeSeq: action.seq, probePending: true };
    case 'probe-ok':
      // 快速点击 A、B：A 后返回时 seq 已过期 → 面板仍显示 B，且 A 不进入解释。
      if (action.seq !== state.probeSeq) return state;
      return { ...state, probe: action.result, probePending: false };
    case 'probe-failed':
      if (action.seq !== state.probeSeq) return state;
      return {
        ...state, probePending: false,
        error: action.error, errorField: action.error.field ?? null,
      };
    default:
      return state;
  }
}

/** 用户可见状态文案键（UI 与 Pi 回执语义一致）。 */
export function phaseLabelKey(state: RasterAuthoringState): string {
  switch (state.phase) {
    case 'committed': return 'rasterAuthoring.state.committed';
    case 'pending': return 'rasterAuthoring.state.pending';
    case 'failed': return 'rasterAuthoring.state.failed';
    case 'preview': return 'rasterAuthoring.state.preview';
    case 'loading': return 'rasterAuthoring.state.loading';
    default: return 'rasterAuthoring.state.idle';
  }
}
