'use client';

/**
 * ModelBuilderTab — 可视化 GIS 模型构建器面板（WP3）。
 *
 * 布局：草稿选择 + 工具条（撤销/恢复 · 保存 CAS · 验证/发布 · 实例化运行）
 * + 三区（目录侧栏 / SVG 画布 / 属性与发现）。
 *
 * 状态纪律：
 * - 一切图编辑走本地 draft 状态（doc）；「保存」按钮才 CAS PUT
 *   （expected_revision；409 REVISION_CONFLICT → InlineNotice + 刷新）；
 * - 连线预检用 model-graph.canConnect 纯函数（不调后端）；非法松手弹
 *   InlineNotice，不落 store；
 * - 图编辑命令（加/删节点、连线、参数修改）push undo 栈（WorkbenchCommand
 *   快照对）；拖动 layout 只改 UI 坐标不进 undo 栈；
 * - 运行链：instantiate → run → getInstance 有界轮询（3s，终态即停）；
 *   实例节点态经 run-overlay 纯函数映射为画布描边/徽标。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FolderOpen, Redo2, Undo2 } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';
import { InlineNotice } from '@/components/shared/inline-notice';
import { EmptyState } from '@/components/shared/empty-state';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';
import { useUndoRedo } from '@/lib/workbench/use-undo';
import { recordCommand } from '@/lib/workbench/undo';
import { getInstance, type WorkflowInstance } from '@/lib/api/workflow-runtime';
import {
  createModel,
  deleteModel,
  getCapabilities,
  getModel,
  instantiateModel,
  listModels,
  publishModel,
  runModelInstance,
  updateModel,
  validateModel,
  ModelAuthoringApiError,
  type ModelCapabilities,
  type ModelDraft,
  type ModelFinding,
} from '@/lib/api/model-authoring';
import {
  canConnect,
  findFreeSpot,
  removeNodeCascade,
  uniqueNodeId,
  type CapabilityIOMap,
  type ModelDocument,
  type ModelLayoutPos,
  type ModelNode,
  type ModelParamSpec,
} from '@/lib/workflow/model-graph';
import {
  authorNodeId,
  instanceStatusNoticeVariant,
  instanceStatusI18nKey,
  isTerminalInstanceStatus,
  nodeStateMap,
} from './run-overlay';
import { ModelCanvas, type ConnectRequest } from './model-canvas';
import { NodePalette, type PaletteAddPayload } from './node-palette';
import { NodePropsPanel } from './node-props-panel';
import { FindingsPanel, type PublishChange } from './findings-panel';

const TOOLBTN =
  'rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50';

/** kind → i18n 键（全字面量 —— 键引用扫描器只认字面量/模板串）。 */
const KIND_LABEL_KEY: Record<string, string> = {
  data_input: 'node.kindDataInput',
  transform: 'node.kindTransform',
  analysis: 'node.kindAnalysis',
  cartography: 'node.kindCartography',
  output: 'node.kindOutput',
};

/** 新草稿的空文档（name 取 slug；source.manual 审计缺省）。 */
function emptyDocument(name: string): ModelDocument {
  return {
    schema_version: '1',
    name,
    description: '',
    source: { kind: 'manual', ref: '' },
    nodes: [],
    edges: [],
    output_node_ids: [],
    layout: {},
  };
}

export function ModelBuilderTab({ ownerToken }: { ownerToken?: string | null }) {
  const t = useT('modelBuilder');
  const projectId = useHudStore((s) => s.activeProjectId);

  /* ── 能力目录（一次性 fetch + 手动重试） ── */
  const [caps, setCaps] = useState<ModelCapabilities | null>(null);
  const [capsLoading, setCapsLoading] = useState(false);
  const [capsError, setCapsError] = useState<string | null>(null);
  const [capsTick, setCapsTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const ac = new AbortController();
    setCapsLoading(true);
    setCapsError(null);
    getCapabilities({ signal: ac.signal })
      .then((c) => {
        if (!cancelled) setCaps(c);
      })
      .catch((err: unknown) => {
        if (!cancelled && !(err instanceof DOMException && err.name === 'AbortError')) {
          setCapsError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setCapsLoading(false);
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [capsTick]);

  /** capability → IO 投影（连线预检上下文）。 */
  const catalog = useMemo<CapabilityIOMap>(() => {
    const out: CapabilityIOMap = {};
    for (const c of caps?.capabilities ?? []) {
      out[c.capability] = {
        input_artifact_types: c.input_artifact_types,
        output_artifact_types: c.output_artifact_types,
      };
    }
    return out;
  }, [caps]);

  /* ── 草稿列表 / 激活草稿 ── */
  const [drafts, setDrafts] = useState<ModelDraft[]>([]);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newSlug, setNewSlug] = useState('');
  const [busy, setBusy] = useState<'' | 'creating' | 'deleting'>('');
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    const ac = new AbortController();
    setDraftError(null);
    setActiveSlug(null); // 项目切换 → 释放旧草稿
    listModels(projectId, { signal: ac.signal })
      .then((rows) => {
        if (cancelled) return;
        setDrafts(rows);
        // 首个草稿自动选中（打开面板即见画布，不用再点一次）。
        setActiveSlug((prev) => prev ?? rows[0]?.model_slug ?? null);
      })
      .catch((err: unknown) => {
        if (!cancelled && !(err instanceof DOMException && err.name === 'AbortError')) {
          setDraftError(err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [projectId]);

  /* ── 激活草稿的本地 draft 状态 ── */
  const [doc, setDoc] = useState<ModelDocument | null>(null);
  const [revision, setRevision] = useState(0);
  const [fingerprint, setFingerprint] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ variant: 'error' | 'info' | 'success' | 'warning'; text: string; refresh?: boolean } | null>(null);
  const revisionRef = useRef(0);
  revisionRef.current = revision;

  useEffect(() => {
    if (!projectId || !activeSlug) {
      setDoc(null);
      return;
    }
    let cancelled = false;
    const ac = new AbortController();
    getModel(projectId, activeSlug, { signal: ac.signal })
      .then((d) => {
        if (cancelled) return;
        setDoc(d.document);
        setRevision(d.revision_no);
        setFingerprint(d.document_fingerprint);
        setDirty(false);
        setSelectedNode(null);
        setFindings([]);
        setValid(null);
        setPublishedVersion(null);
        setInstanceId(null);
        setRunBindings({});
        setNotice(null);
      })
      .catch((err: unknown) => {
        if (!cancelled && !(err instanceof DOMException && err.name === 'AbortError')) {
          setNotice({ variant: 'error', text: t('errors.loadFailed', { detail: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err) }) });
        }
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, activeSlug]);

  /* ── 验证 / 发布 ── */
  const [findings, setFindings] = useState<ModelFinding[]>([]);
  const [valid, setValid] = useState<boolean | null>(null);
  const [validating, setValidating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishedVersion, setPublishedVersion] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  // 动作函数以普通函数声明（主体全是 setter —— React Compiler 的
  // preserve-manual-memoization 对 useCallback 依赖数组与推断不一致会
  // 跳过编译，不如不做手动 memo）。
  const doValidate = async () => {
    if (!projectId || !activeSlug) return;
    setValidating(true);
    try {
      const out = await validateModel(projectId, activeSlug);
      setFindings(out.findings);
      setValid(out.valid);
      setFingerprint(out.document_fingerprint);
    } catch (err) {
      setNotice({
        variant: 'error',
        text: t('errors.loadFailed', { detail: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err) }),
      });
    } finally {
      setValidating(false);
    }
  };

  const doPublish = async (change: PublishChange) => {
    if (!projectId || !activeSlug) return;
    setPublishing(true);
    setPublishError(null);
    try {
      const out = await publishModel(projectId, activeSlug, change);
      setPublishedVersion(out.version);
      setFindings(out.findings);
    } catch (err) {
      if (err instanceof ModelAuthoringApiError && err.findings.length > 0) {
        // 发布 422 MODEL_INVALID：findings 直接落本面板。
        setFindings(err.findings);
        setValid(false);
      }
      setPublishError(
        t('findings.publishFailed', {
          code: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setPublishing(false);
    }
  };

  /* ── 运行链：instantiate → run → 有界轮询 ── */
  const [instanceId, setInstanceId] = useState<string | null>(null);
  const [runBindings, setRunBindings] = useState<Record<string, { ok: boolean; code: string; bound_ref?: string }>>({});
  const [runPhase, setRunPhase] = useState<'idle' | 'instantiating' | 'starting'>('idle');
  const [runError, setRunError] = useState<string | null>(null);
  const [pkg, setPkg] = useState<{ package_id: string; version: string } | null>(null);
  const [instance, setInstance] = useState<WorkflowInstance | null>(null);
  const pollStatus = useRef<{ status: string | null }>({ status: null });

  const runPoll = useBoundedPoll<{ instance: WorkflowInstance }>({
    enabled: instanceId != null && !(pollStatus.current.status != null && isTerminalInstanceStatus(pollStatus.current.status)),
    resetKey: instanceId ?? 'none',
    pollIntervalMs: 3000,
    fetcher: async (signal) => {
      const inst = await getInstance(instanceId as string, { ownerToken, signal });
      return { instance: inst };
    },
  });

  useEffect(() => {
    const inst = runPoll.data?.instance ?? null;
    setInstance(inst);
    pollStatus.current.status = inst?.status ?? null;
  }, [runPoll.data]);

  const doRun = async () => {
    if (!projectId || !activeSlug || !doc) return;
    setRunError(null);
    setPublishedVersion(null);
    setRunPhase('instantiating');
    try {
      const out = await instantiateModel(projectId, activeSlug, {});
      setRunBindings(out.bindings);
      setPkg({ package_id: out.package.package_id, version: out.package.version });
      setRunPhase('starting');
      await runModelInstance(out.instance.instance_id, { projectId });
      pollStatus.current.status = null;
      setInstanceId(out.instance.instance_id);
      setRunPhase('idle');
    } catch (err) {
      setRunPhase('idle');
      setRunError(
        t('run.runFailed', {
          code: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err),
        }),
      );
    }
  };

  /* ── 撤销/恢复（WorkbenchCommand 快照对；拖动 layout 不进栈） ── */
  const { canUndo, canRedo, undo, redo } = useUndoRedo();

  const applyCommand = useCallback(
    (label: string, kind: 'add' | 'remove' | 'style', before: ModelDocument, after: ModelDocument) => {
      setDoc(after);
      setDirty(true);
      recordCommand({
        label,
        kind,
        actor: 'user',
        undo: () => {
          setDoc(before);
          setDirty(true);
        },
        redo: () => {
          setDoc(after);
          setDirty(true);
        },
      });
    },
    [],
  );

  /* ── 保存（CAS PUT）── */
  const doSave = useCallback(async () => {
    if (!projectId || !activeSlug || !doc) return;
    setSaveBusy(true);
    setNotice(null);
    try {
      const draft = await updateModel(projectId, activeSlug, {
        document: doc,
        expected_revision: revisionRef.current,
      });
      setRevision(draft.revision_no);
      setFingerprint(draft.document_fingerprint);
      setDirty(false);
      setNotice({ variant: 'success', text: t('toolbar.saved') });
    } catch (err) {
      if (err instanceof ModelAuthoringApiError && err.code === 'REVISION_CONFLICT') {
        setNotice({ variant: 'error', text: t('errors.conflict'), refresh: true });
      } else {
        setNotice({
          variant: 'error',
          text: t('errors.saveFailed', {
            code: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err),
          }),
        });
      }
    } finally {
      setSaveBusy(false);
    }
  }, [projectId, activeSlug, doc, t]);

  const refreshDraft = useCallback(() => {
    if (!projectId || !activeSlug) return;
    getModel(projectId, activeSlug)
      .then((d) => {
        setDoc(d.document);
        setRevision(d.revision_no);
        setFingerprint(d.document_fingerprint);
        setDirty(false);
        setNotice(null);
      })
      .catch(() => setNotice({ variant: 'error', text: t('errors.loadFailed', { detail: '' }) }));
  }, [projectId, activeSlug, t]);

  /* ── 图编辑动作 ── */
  const addNode = useCallback(
    (payload: PaletteAddPayload) => {
      if (!doc) return;
      const base =
        payload.kind === 'data_input'
          ? `in_${payload.role}`
          : payload.kind === 'transform'
            ? `tf_${payload.transform_op}`
            : payload.kind === 'analysis'
              ? `cap_${payload.capability.split('.').pop()?.replace(/[^a-z0-9_]/gi, '') || 'x'}`
              : payload.kind;
      const id = uniqueNodeId(doc, base.toLowerCase());
      const pos = findFreeSpot(doc);
      const node: ModelNode = { node_id: id, kind: payload.kind, title: id };
      if (payload.kind === 'data_input') node.role = payload.role;
      if (payload.kind === 'transform') node.transform_op = payload.transform_op;
      if (payload.kind === 'analysis') node.capability = payload.capability;
      const after: ModelDocument = {
        ...doc,
        nodes: [...doc.nodes, node],
        layout: { ...(doc.layout ?? {}), [id]: pos },
      };
      applyCommand(t(KIND_LABEL_KEY[payload.kind] ?? 'node.kindOutput'), 'add', doc, after);
      setSelectedNode(id);
    },
    [doc, applyCommand, t],
  );

  const removeNode = useCallback(
    (nodeId: string) => {
      if (!doc) return;
      const { document: after } = removeNodeCascade(doc, nodeId);
      applyCommand(nodeId, 'remove', doc, after);
      if (selectedNode === nodeId) setSelectedNode(null);
    },
    [doc, applyCommand, selectedNode],
  );

  const requestConnect = useCallback(
    (req: ConnectRequest) => {
      if (!doc) return;
      const verdict = canConnect(doc, req.fromNode, req.fromPort, req.toNode, req.toPort, catalog);
      if (!verdict.ok || !verdict.edge) {
        setNotice({ variant: 'error', text: t(`connect.${verdict.reason ?? 'unknownNode'}`) });
        return;
      }
      const after: ModelDocument = { ...doc, edges: [...doc.edges, verdict.edge] };
      applyCommand(`${verdict.edge.from_node}->${verdict.edge.to_node}`, 'add', doc, after);
    },
    [doc, catalog, applyCommand, t],
  );

  const rejectConnect = useCallback(
    (reason: string) => {
      setNotice({ variant: 'error', text: t(`connect.${reason}`) });
    },
    [t],
  );

  // 参数/属性修改的 undo 需要 before 快照 —— docRef 同步取当前 draft
  //（函数式 setState 的 prev 在命令入栈时不可用）。
  const docRef = useRef<ModelDocument | null>(null);
  docRef.current = doc;

  /** 参数/属性修改命令（kind=style；拖动 layout 不走这里 —— UI 坐标不进栈）。 */
  const applyEditCommand = useCallback(
    (label: string, before: ModelDocument, after: ModelDocument) => {
      setDoc(after);
      setDirty(true);
      recordCommand({
        label,
        kind: 'style',
        actor: 'user',
        undo: () => {
          setDoc(before);
          setDirty(true);
        },
        redo: () => {
          setDoc(after);
          setDirty(true);
        },
      });
    },
    [],
  );

  /** 拖动提交：只写 layout（UI 坐标），不进 undo 栈、不触发任何请求。 */
  const moveNode = useCallback(
    (nodeId: string, pos: ModelLayoutPos) => {
      setDoc((prev) => (prev ? { ...prev, layout: { ...(prev.layout ?? {}), [nodeId]: pos } } : prev));
      setDirty(true);
    },
    [],
  );

  const patchNode = useCallback(
    (nodeId: string, patch: Partial<ModelNode>) => {
      const before = docRef.current;
      if (!before) return;
      applyEditCommand(
        nodeId,
        before,
        { ...before, nodes: before.nodes.map((n) => (n.node_id === nodeId ? { ...n, ...patch } : n)) },
      );
    },
    [applyEditCommand],
  );

  const patchNodeParams = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      const before = docRef.current;
      if (!before) return;
      applyEditCommand(
        `${nodeId}:params`,
        before,
        {
          ...before,
          nodes: before.nodes.map((n) =>
            n.node_id === nodeId
              ? {
                  ...n,
                  params: {
                    values: Object.fromEntries(
                      Object.entries(values).map(([k, v]) => [k, { value: v }]),
                    ),
                  },
                }
              : n,
          ),
        },
      );
    },
    [applyEditCommand],
  );

  const patchModelParams = useCallback(
    (params: ModelParamSpec[]) => {
      const before = docRef.current;
      if (!before) return;
      applyEditCommand('params', before, { ...before, params });
    },
    [applyEditCommand],
  );

  /* ── 新建 / 删除草稿 ── */
  const doCreate = useCallback(async () => {
    if (!projectId || !newSlug.trim()) return;
    setBusy('creating');
    try {
      const draft = await createModel(projectId, {
        model_slug: newSlug.trim(),
        document: emptyDocument(newSlug.trim()),
      });
      setDrafts((prev) => [draft, ...prev]);
      setActiveSlug(draft.model_slug);
      setCreating(false);
      setNewSlug('');
    } catch (err) {
      setNotice({
        variant: 'error',
        text: t('errors.createFailed', {
          code: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err),
        }),
      });
    } finally {
      setBusy('');
    }
  }, [projectId, newSlug, t]);

  const doDeleteDraft = useCallback(async () => {
    if (!projectId || !activeSlug) return;
    setBusy('deleting');
    try {
      await deleteModel(projectId, activeSlug, revisionRef.current);
      setDrafts((prev) => prev.filter((d) => d.model_slug !== activeSlug));
      setActiveSlug(null);
    } catch (err) {
      setNotice({
        variant: 'error',
        text: t('errors.deleteFailed', {
          code: err instanceof ModelAuthoringApiError ? err.code : err instanceof Error ? err.message : String(err),
        }),
      });
    } finally {
      setBusy('');
    }
  }, [projectId, activeSlug, t]);

  /* ── 运行 overlay 投影 ── */
  const nodeStates = useMemo(
    () => (instance ? nodeStateMap(instance.nodes) : undefined),
    [instance],
  );
  const nodeErrors = useMemo(() => {
    if (!instance) return undefined;
    const out: Record<string, string> = {};
    for (const n of instance.nodes) {
      if (n.error_code) out[authorNodeId(n.node_id)] = n.error_code;
    }
    return out;
  }, [instance]);

  const runStatus = instance?.status ?? null;
  const statusKey = runStatus ? instanceStatusI18nKey(runStatus) : '';

  /* ── 渲染 ── */
  if (!projectId) {
    return (
      <div className="p-2" data-testid="model-builder-tab">
        <EmptyState icon={FolderOpen} title={t('empty.noProjectTitle')} description={t('empty.noProjectDesc')} />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2" data-testid="model-builder-tab">
      {/* 草稿选择行 */}
      <div className="flex items-center gap-1.5">
        <label className="sr-only" htmlFor="mb-draft-select">{t('draft.pickerLabel')}</label>
        <select
          id="mb-draft-select"
          data-testid="draft-select"
          value={activeSlug ?? ''}
          onChange={(e) => setActiveSlug(e.target.value || null)}
          className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-raised px-1 py-1 text-meta text-ink"
        >
          <option value="">{t('draft.pickerLabel')} ({drafts.length})</option>
          {drafts.map((d) => (
            <option key={d.model_slug} value={d.model_slug}>
              {d.model_slug} · {t('draft.revision', { revision: d.revision_no })}
            </option>
          ))}
        </select>
        {activeSlug && (() => {
          const published = drafts.find((d) => d.model_slug === activeSlug)?.latest_published_version ?? '';
          return (
            <>
            <span className="shrink-0 text-micro text-ink-muted">
              {published ? t('draft.publishedVersion', { version: published }) : t('draft.unpublished')}
            </span>
            <button
              type="button"
              aria-label={t('draft.delete')}
              data-testid="delete-draft-btn"
              onClick={doDeleteDraft}
              disabled={busy !== ''}
              className={TOOLBTN}
            >
              {busy === 'deleting' ? '…' : t('draft.delete')}
            </button>
            </>
          );
        })()}
        <button
          type="button"
          data-testid="new-draft-btn"
          onClick={() => setCreating((v) => !v)}
          className={TOOLBTN}
        >
          {t('draft.create')}
        </button>
      </div>
      {creating && (
        <div className="flex items-center gap-1.5">
          <input
            aria-label={t('draft.slugLabel')}
            placeholder={t('draft.slugPlaceholder')}
            value={newSlug}
            onChange={(e) => setNewSlug(e.target.value)}
            className="min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink"
          />
          <button
            type="button"
            data-testid="confirm-create-btn"
            onClick={doCreate}
            disabled={busy !== '' || !newSlug.trim()}
            className={TOOLBTN}
          >
            {busy === 'creating' ? t('draft.creating') : t('draft.create')}
          </button>
        </div>
      )}
      {draftError && <InlineNotice variant="error">{t('errors.loadFailed', { detail: draftError })}</InlineNotice>}
      {notice && (
        <div data-testid="builder-notice">
          <InlineNotice variant={notice.variant}>
            <span>{notice.text}</span>
            {notice.refresh && (
              <button
                type="button"
                data-testid="refresh-draft-btn"
                onClick={refreshDraft}
                className="ml-1 underline"
              >
                {t('errors.refresh')}
              </button>
            )}
          </InlineNotice>
        </div>
      )}

      {/* 无激活草稿 */}
      {!doc ? (
        drafts.length === 0 && !creating ? (
          <EmptyState
            icon={FolderOpen}
            title={t('empty.noDraftsTitle')}
            description={t('empty.noDraftsDesc')}
          />
        ) : null
      ) : (
        <>
          {/* 工具条 */}
          <div className="flex flex-wrap items-center gap-1.5" data-testid="builder-toolbar">
            <button
              type="button"
              aria-label={t('toolbar.undo')}
              title={t('toolbar.undo')}
              onClick={() => undo()}
              disabled={!canUndo}
              className={TOOLBTN}
            >
              <Undo2 size={13} aria-hidden />
            </button>
            <button
              type="button"
              aria-label={t('toolbar.redo')}
              title={t('toolbar.redo')}
              onClick={() => redo()}
              disabled={!canRedo}
              className={TOOLBTN}
            >
              <Redo2 size={13} aria-hidden />
            </button>
            <button
              type="button"
              data-testid="save-btn"
              onClick={doSave}
              disabled={saveBusy || !dirty}
              className={TOOLBTN}
            >
              {saveBusy ? t('toolbar.saving') : t('toolbar.save')}
            </button>
            {dirty && <span className="text-micro text-status-warn">{t('toolbar.dirtyHint')}</span>}
            <span className="flex-1" />
            <button
              type="button"
              data-testid="run-btn"
              onClick={doRun}
              disabled={runPhase !== 'idle' || dirty || findings.some((f) => f.severity === 'error')}
              title={runPhase !== 'idle' ? t('run.instantiating') : dirty ? t('run.idle') : t('run.needsValid')}
              className="rounded-sm bg-status-accent px-2 py-1 text-meta font-medium text-ink-on-accent hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {runPhase === 'instantiating' ? t('run.instantiating') : runPhase === 'starting' ? t('run.starting') : t('toolbar.run')}
            </button>
          </div>

          {/* 三区：目录 / 画布+属性+发现 */}
          <div className="flex min-h-0 gap-2">
            <aside
              aria-label={t('aria.palette')}
              className="w-40 shrink-0 overflow-y-auto border-r border-edge-subtle pr-1"
              data-testid="palette-pane"
            >
              <NodePalette
                capabilities={caps}
                loading={capsLoading}
                error={capsError}
                onAdd={addNode}
                onRetry={() => setCapsTick((v) => v + 1)}
              />
            </aside>
            <div className="min-w-0 flex-1 space-y-2">
              <ModelCanvas
                document={doc}
                selectedNodeId={selectedNode}
                catalog={catalog}
                nodeStates={nodeStates}
                nodeErrors={nodeErrors}
                bindings={runBindings}
                onSelectNode={setSelectedNode}
                onMoveNode={moveNode}
                onDeleteNode={removeNode}
                onRequestConnect={requestConnect}
                onConnectRejected={rejectConnect}
              />
              <p className="sr-only">{t('aria.deleteHint')}</p>
              <NodePropsPanel
                document={doc}
                selectedNodeId={selectedNode}
                capabilities={caps}
                onChangeNode={patchNode}
                onChangeParams={patchNodeParams}
                onChangeModelParams={patchModelParams}
                onDeleteNode={removeNode}
              />
              <FindingsPanel
                findings={findings}
                valid={valid}
                fingerprint={fingerprint}
                validating={validating}
                publishing={publishing}
                publishedVersion={publishedVersion}
                publishError={publishError}
                onValidate={doValidate}
                onPublish={doPublish}
                onJumpToNode={setSelectedNode}
              />
              {/* 运行态条 */}
              {instance && (
                <div data-testid="run-status">
                  <InlineNotice variant={instanceStatusNoticeVariant(instance.status)}>
                    {t('run.instanceLabel', {
                      instance: instance.instance_id,
                      pkg: pkg?.package_id ?? instance.package_id,
                      version: pkg?.version ?? instance.package_version,
                    })}
                    {statusKey ? ` · ${t(statusKey)}` : ` · ${instance.status}`}
                  </InlineNotice>
                </div>
              )}
              {runError && <InlineNotice variant="error">{runError}</InlineNotice>}
              {runPoll.status.pauseReason === 'error-limit' && (
                <InlineNotice variant="warning">{t('run.pollPaused')}</InlineNotice>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
