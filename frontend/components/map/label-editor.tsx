'use client';
/**
 * G16（v2.1）：要素级标注编辑器（edit_labels 工具的画布手势面）。
 *
 * 交互纪律（与 sketch-editor 同款）：
 * - 只在工具激活时捕获手势（mousedown on `${id}-label` / `-label-pinned`
 *   子层）；正常 pan/select 不被抢占；
 * - 拖动本地轻量预览（pointermove 只更新本组件状态），pointerup 才提交
 *   一笔 revision（绝不每 move 一次服务端提交）；
 * - 方向键微调（±0.25em，Shift ×4），Enter/失焦提交，Esc 取消零写入；
 * - 全部决定经 commitLabelAuthoring 走权威 MapSpec（CAS + 撤销栈 +
 *   user-wins），刷新后保留、Agent 修改回流同步（spec reconcile）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import { useHudStore } from '@/lib/store/useHudStore';
import { useT } from '@/lib/i18n/useT';
import { commitLabelAuthoring } from '@/lib/mapspec/user-mutation';
import { recordCommand } from '@/lib/workbench/undo';
import { resolveFeatureIdentity } from '@/lib/mapspec-runtime/label-authoring';
import { getCommittedMapSpec } from '@/lib/mapspec/session-cursor';
import { registerEscapeHandler } from '@/lib/utils/escape-stack';
import { useToastStore } from '@/components/ui/toast';

/** 编辑中的目标（一个标签）。 */
interface EditTarget {
  layerId: string;
  specLayerId: string;
  featureId: string;
  idKey: string | null;
  /** 当前覆写（可无）。 */
  override: Record<string, unknown> | null;
  /** 拖动中的暂存 em 偏移（未提交）。 */
  pendingOffset: [number, number] | null;
  /** px→em 换算基准（layer.label.size，与 runtime 同源）。 */
  fontPx: number;
}

const LABEL_FONT_PX_FALLBACK = 12;
const NUDGE_EM = 0.25;

export function LabelEditor({ mapRef }: { mapRef: React.RefObject<MapRef | null> }) {
  const t = useT('map');
  const activeTool = useHudStore((s) => s.activeMapTool);
  const active = activeTool === 'edit_labels';
  const [target, setTarget] = useState<EditTarget | null>(null);
  const [textDraft, setTextDraft] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const dragState = useRef<{
    startPx: [number, number] | null;
    originOffset: [number, number];
    moved: boolean;
    wasDragging: boolean;
  }>({ startPx: null, originOffset: [0, 0], moved: false, wasDragging: false });
  const targetRef = useRef<EditTarget | null>(null);
  targetRef.current = target;

  const labelLayerIds = useCallback((): string[] => {
    const map = mapRef.current;
    if (!map) return [];
    const style = map.getStyle?.();
    const layers: Array<{ id?: string }> = style?.layers ?? [];
    return layers
      .map((l) => String(l.id ?? ''))
      .filter((id) => id.endsWith('-label') || id.endsWith('-label-pinned'));
  }, [mapRef]);

  /** 提交一组 ops（一笔 revision；原子拒绝 → toast）。 */
  const commit = useCallback(
    async (specLayerId: string, ops: Array<Record<string, unknown>>, undoLabel: string) => {
      if (ops.length === 0) return;
      setCommitting(true);
      try {
        // 撤销面：反向重放 reset(scope=all)（用户最新意图可再覆盖）；
        // redo 重放同 ops（CAS 保护并发）。均为 fire-and-forget 写路径。
        recordCommand({
          label: undoLabel,
          kind: 'label_authoring',
          actor: 'user',
          layerIds: [specLayerId],
          undo: () => {
            void commitLabelAuthoring(specLayerId, inverseReset(ops));
          },
          redo: () => {
            void commitLabelAuthoring(specLayerId, ops);
          },
        } as never);
        await commitLabelAuthoring(specLayerId, ops);
      } catch {
        /* toast 已由 mutation 客户端发出（原子拒绝/网络） */
      } finally {
        setCommitting(false);
      }
    },
    [],
  );

  /** 命中标签/要素 → 编辑目标（返回值供同 handler 内拖动初始化 ——
   * targetRef 同步更新，不等 re-render）。 */
  const pickTarget = useCallback(
    (e: { point: { x: number; y: number }; lngLat: { lng: number; lat: number } }): EditTarget | null => {
      const map = mapRef.current;
      if (!map) return null;
      const hits = (map as any).queryRenderedFeatures?.(e.point, {
        layers: labelLayerIds(),
      }) as Array<{
        layer?: { id?: string };
        properties?: Record<string, unknown> | null;
        id?: unknown;
        geometry?: { type?: string; coordinates?: unknown };
      }> | undefined;
      const hit = hits?.[0];
      if (!hit?.layer?.id) {
        useToastStore.getState().addToast(t('labelEditor.noSelection'), 'info');
        return null;
      }
      // 层 id 形如 `${specLayerId}-label[-pinned]`。
      const rawLayerId = hit.layer.id.replace(/-label(-pinned)?$/, '');
      const { featureId, idKey } = resolveFeatureIdentity({
        id: (hit as { id?: unknown }).id,
        properties: hit.properties ?? null,
      });
      if (!featureId) {
        useToastStore
          .getState()
          .addToast(`${t('labelEditor.noSelection')}（无稳定要素 id）`, 'warning');
        return null;
      }
      // 当前覆写 + 字号（committed spec 是权威 —— 与 runtime baseSize 同源）。
      const spec = getCommittedMapSpec() as {
        layers?: Array<{ id?: string; label?: { size?: unknown; authoring?: { overrides?: unknown[] } } }>;
      } | null;
      const specLayer = (spec?.layers ?? []).find((l) => l.id === rawLayerId);
      const existing = ((specLayer?.label?.authoring?.overrides ?? []) as Array<Record<string, unknown>>)
        .find((o) => o.featureId === featureId) ?? null;
      const fontPx = Number(specLayer?.label?.size ?? 12) || LABEL_FONT_PX_FALLBACK;
      const next: EditTarget = {
        layerId: rawLayerId,
        specLayerId: rawLayerId,
        featureId,
        idKey,
        override: existing,
        pendingOffset: null,
        fontPx,
      };
      targetRef.current = next;
      setTarget(next);
      setTextDraft(typeof existing?.text === 'string' ? existing.text : null);
      return next;
    },
    [labelLayerIds, mapRef, t],
  );

  // 手势注册：工具激活才挂（capture 语义不抢 pan/select —— 只在标签层
  // 的 mousedown 上启动拖动）。
  useEffect(() => {
    if (!active) return;
    const map = mapRef.current;
    if (!map) return;
    const mapAny = map as any;

    const onMouseDown = (e: { point: { x: number; y: number }; features?: unknown[] }) => {
      const tgt = pickTarget(e as never);
      if (!tgt) return;
      dragState.current = {
        startPx: [e.point.x, e.point.y],
        originOffset: readOffset(tgt),
        moved: false,
        wasDragging: true,
      };
      try {
        mapAny.dragPan.disable();
      } catch { /* mock */ }
    };
    const onMouseMove = (e: { point: { x: number; y: number } }) => {
      const ds = dragState.current;
      const tgt = targetRef.current;
      if (!ds.startPx || !tgt) return;
      const dxPx = e.point.x - ds.startPx[0];
      const dyPx = e.point.y - ds.startPx[1];
      if (dxPx === 0 && dyPx === 0) return;
      ds.moved = true;
      setTarget({
        ...tgt,
        pendingOffset: [
          ds.originOffset[0] + dxPx / tgt.fontPx,
          ds.originOffset[1] + dyPx / tgt.fontPx,
        ],
      });
    };
    const onMouseUp = async () => {
      const ds = dragState.current;
      const tgt = targetRef.current;
      dragState.current = { ...ds, startPx: null };
      try {
        mapAny.dragPan.enable();
      } catch { /* mock */ }
      if (!ds.moved || !tgt) return;
      const offset = tgt.pendingOffset ?? ds.originOffset;
      // 提交一笔：pin + offset（拖动即「固定在这里」）。
      setTarget({ ...tgt, pendingOffset: null });
      await commit(tgt.specLayerId, [
        {
          featureId: tgt.featureId,
          op: 'set',
          set: { pinned: true, offset: { dx: round2(offset[0]), dy: round2(offset[1]) } },
        },
      ], `固定标注 ${tgt.featureId}`);
    };

    mapAny.on('mousedown', labelLayerIds(), onMouseDown as never);
    mapAny.on('mousemove', onMouseMove as never);
    mapAny.on('mouseup', onMouseUp as never);
    return () => {
      mapAny.off?.('mousedown', labelLayerIds(), onMouseDown as never);
      mapAny.off?.('mousemove', onMouseMove as never);
      mapAny.off?.('mouseup', onMouseUp as never);
    };
  }, [active, labelLayerIds, mapRef, pickTarget, commit]);

  // Esc：取消未提交的拖动/草稿（零写入）。
  useEffect(() => {
    if (!active || !target?.pendingOffset) return;
    return registerEscapeHandler(40, () => {
      setTarget((prev) => (prev ? { ...prev, pendingOffset: null } : null));
      return true;
    });
  }, [active, target?.pendingOffset]);

  // 键盘微调（工具激活 + 有目标）：方向键 ±0.25em（Shift ×4）→ Enter 提交。
  useEffect(() => {
    if (!active || !target) return;
    let nudged: [number, number] | null = null;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && nudged) {
        e.preventDefault();
        const base = readOffset(target);
        const next: [number, number] = [
          round2(base[0] + (nudged?.[0] ?? 0)),
          round2(base[1] + (nudged?.[1] ?? 0)),
        ];
        nudged = null;
        void commit(target.specLayerId, [
          {
            featureId: target.featureId,
            op: 'set',
            set: { pinned: true, offset: { dx: next[0], dy: next[1] } },
          },
        ], '微调标注位置');
        return;
      }
      if (e.key === 'Escape' && nudged) {
        nudged = null;
        return;
      }
      const step = NUDGE_EM * (e.shiftKey ? 4 : 1);
      const dirs: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0], ArrowRight: [step, 0],
        ArrowUp: [0, -step], ArrowDown: [0, step],
      };
      const d = dirs[e.key];
      if (!d) return;
      e.preventDefault();
      nudged = nudged ?? [0, 0];
      nudged = [nudged[0] + d[0], nudged[1] + d[1]];
      setTarget((prev) => (prev ? {
        ...prev,
        pendingOffset: [
          round2(readOffset(prev)[0] + nudged![0]),
          round2(readOffset(prev)[1] + nudged![1]),
        ],
      } : null));
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [active, target, commit]);

  if (!active) return null;

  const ops = (set: Record<string, unknown>) =>
    target ? [{ featureId: target.featureId, op: 'set', set }] : [];
  const reset = (scope: string) =>
    target ? [{ featureId: target.featureId, op: 'reset', scope }] : [];

  return (
    <div
      className="pointer-events-auto absolute top-24 right-16 z-20 w-60 rounded-lg border border-edge-subtle bg-surface-raised p-3 shadow-lg"
      data-testid="label-editor-panel"
      role="group"
      aria-label={t('labelEditor.title')}
    >
      <div className="mb-2 text-xs font-medium">{t('labelEditor.title')}</div>
      {!target ? (
        <p className="text-xs text-text-muted">{t('labelEditor.hint')}</p>
      ) : (
        <div className="flex flex-col gap-2 text-xs">
          <div className="rounded bg-surface-subtle px-2 py-1 font-mono text-[11px]">
            {target.featureId}
            {target.pendingOffset
              ? ` → (${target.pendingOffset[0]}, ${target.pendingOffset[1]})em`
              : ''}
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              className="rounded border border-edge-subtle px-2 py-1 hover:bg-surface-subtle"
              disabled={committing}
              data-testid="label-editor-hide"
              onClick={() => commit(target.specLayerId, ops({ hidden: true }), '隐藏标注')}
            >
              {t('labelEditor.hide')}
            </button>
            <button
              type="button"
              className="rounded border border-edge-subtle px-2 py-1 hover:bg-surface-subtle"
              disabled={committing}
              onClick={() => commit(target.specLayerId, ops({ pinned: true, callout: true }), '固定标注')}
            >
              {t('labelEditor.pin')}
            </button>
            <button
              type="button"
              className="rounded border border-edge-subtle px-2 py-1 hover:bg-surface-subtle"
              disabled={committing}
              onClick={() => commit(target.specLayerId, ops({ pinned: null, offset: null, callout: null }), '解除固定')}
            >
              {t('labelEditor.unpin')}
            </button>
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-text-muted">
              {t('labelEditor.text')}
              <em className="ml-1 not-italic opacity-70">({t('labelEditor.textHint')})</em>
            </span>
            <div className="flex gap-1">
              <input
                className="min-w-0 flex-1 rounded border border-edge-subtle bg-surface px-2 py-1 text-xs"
                value={textDraft ?? ''}
                placeholder={t('labelEditor.text')}
                onChange={(e) => setTextDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && textDraft && textDraft.trim()) {
                    void commit(target.specLayerId, ops({ text: textDraft.trim() }), '改标注文字');
                  }
                }}
                data-testid="label-editor-text-input"
              />
              <button
                type="button"
                className="rounded border border-edge-subtle px-2 py-1 disabled:opacity-50"
                disabled={!textDraft?.trim() || committing}
                onClick={() => textDraft?.trim()
                  && commit(target.specLayerId, ops({ text: textDraft.trim() }), '改标注文字')}
              >
                ✓
              </button>
            </div>
          </label>
          <div className="flex flex-wrap gap-1 border-t border-edge-subtle pt-2">
            <button
              type="button"
              className="rounded px-2 py-1 text-text-muted hover:bg-surface-subtle"
              disabled={committing}
              data-testid="label-editor-reset-position"
              onClick={() => commit(target.specLayerId, reset('position'), '恢复自动位置')}
            >
              {t('labelEditor.resetPosition')}
            </button>
            <button
              type="button"
              className="rounded px-2 py-1 text-text-muted hover:bg-surface-subtle"
              disabled={committing}
              data-testid="label-editor-reset-text"
              onClick={() => commit(target.specLayerId, reset('text'), '恢复字段文字')}
            >
              {t('labelEditor.resetText')}
            </button>
            <button
              type="button"
              className="rounded px-2 py-1 text-text-muted hover:bg-surface-subtle"
              disabled={committing}
              data-testid="label-editor-reset-visibility"
              onClick={() => commit(target.specLayerId, reset('visibility'), '解除隐藏')}
            >
              {t('labelEditor.resetVisibility')}
            </button>
            <button
              type="button"
              className="rounded px-2 py-1 text-text-muted hover:bg-surface-subtle"
              disabled={committing}
              data-testid="label-editor-reset-all"
              onClick={() => commit(target.specLayerId, reset('all'), '清除全部覆写')}
            >
              {t('labelEditor.resetAll')}
            </button>
          </div>
          <p className="text-[10px] leading-4 text-text-muted">{t('labelEditor.keyboardHint')}</p>
        </div>
      )}
    </div>
  );
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function readOffset(tgt: EditTarget): [number, number] {
  const off = tgt.override?.offset as { dx?: number; dy?: number } | undefined;
  if (off && typeof off.dx === 'number' && typeof off.dy === 'number') {
    return [off.dx, off.dy];
  }
  if (tgt.pendingOffset) return tgt.pendingOffset;
  return [0, 0];
}


/** 撤销反向：set → reset(all)（保守粒度 —— 用户最新意图恒可再覆盖）。 */
function inverseReset(ops: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  return ops
    .filter((op) => op.op === 'set')
    .map((op) => ({ featureId: op.featureId, op: 'reset', scope: 'all' }));
}
