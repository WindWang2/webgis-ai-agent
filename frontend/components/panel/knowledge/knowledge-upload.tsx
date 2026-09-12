'use client';

/**
 * KnowledgeUpload — 知识库文档录入（POST /knowledge/documents）。
 *
 * 后端契约（见 frontend/docs/knowledge-market-recon.md）：只收纯文本
 * （text/markdown/json，content ≤ 100MiB），同步索引，无 multipart、无进度
 * 事件。因此：.txt/.md/.json 由客户端 FileReader 读出文本后 POST JSON；
 * 其他类型诚实拒收并说明（PDF/DOCX 等是后端缺口，不伪装支持）。
 * 索引期间唯一真实的「进度」就是请求在途 —— 用 aria-busy 表达，不造假进度条。
 *
 * 键盘路径：拖拽区本体是 <button>（打开文件选择器），拖拽只是增强路径。
 */
import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import { FileText, Upload } from 'lucide-react';
import {
  KNOWLEDGE_MAX_CONTENT_BYTES,
  addKnowledgeDocument,
  knowledgeFileTypeForFileName,
  type AddDocumentResult,
  type KnowledgeFileType,
} from '@/lib/api/knowledge';

const ACCEPT = '.txt,.md,.markdown,.json';

/** FileReader 读文本（不用 File.text()：jsdom 测试环境未实现该 API）。 */
function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.readAsText(file);
  });
}

type UploadState =
  | { status: 'idle' }
  | { status: 'busy'; phase: 'reading' | 'indexing' }
  | { status: 'done'; result: AddDocumentResult }
  | { status: 'error'; message: string };

interface KnowledgeUploadProps {
  /** 索引成功后回调（父级刷新文档列表）。 */
  onUploaded: (result: AddDocumentResult) => void;
}

export function KnowledgeUpload({ onUploaded }: KnowledgeUploadProps) {
  const [mode, setMode] = useState<'file' | 'paste'>('file');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [fileType, setFileType] = useState<KnowledgeFileType>('text');
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [state, setState] = useState<UploadState>({ status: 'idle' });

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const readFile = useCallback(async (file: File) => {
    const type = knowledgeFileTypeForFileName(file.name);
    if (!type) {
      setState({
        status: 'error',
        message: `暂不支持「${file.name}」：后端知识库仅索引纯文本（.txt / .md / .json），PDF/DOCX 等格式后端尚不支持。`,
      });
      return;
    }
    if (file.size > KNOWLEDGE_MAX_CONTENT_BYTES) {
      setState({
        status: 'error',
        message: `文件超出后端上限（${Math.floor(KNOWLEDGE_MAX_CONTENT_BYTES / (1024 * 1024))} MiB）。`,
      });
      return;
    }
    setState({ status: 'busy', phase: 'reading' });
    try {
      const text = await readFileAsText(file);
      setTitle(file.name.replace(/\.(txt|md|markdown|json)$/i, ''));
      setContent(text);
      setFileType(type);
      setFileName(file.name);
      setState({ status: 'idle' });
    } catch {
      setState({ status: 'error', message: `无法读取「${file.name}」（可能不是 UTF-8 文本）。` });
    }
  }, []);

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      if (state.status === 'busy') return;
      const file = e.dataTransfer.files?.[0];
      if (file) void readFile(file);
    },
    [readFile, state.status],
  );

  const submit = useCallback(async () => {
    if (!content.trim() || !title.trim() || state.status === 'busy') return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'busy', phase: 'indexing' });
    try {
      const result = await addKnowledgeDocument(
        { title: title.trim(), content, fileType },
        { signal: controller.signal },
      );
      setState({ status: 'done', result });
      setContent('');
      setFileName(null);
      if (inputRef.current) inputRef.current.value = '';
      onUploaded(result);
    } catch (err) {
      setState({
        status: 'error',
        message: err instanceof Error ? err.message : '文档索引失败',
      });
    }
  }, [content, title, fileType, state.status, onUploaded]);

  const busy = state.status === 'busy';
  const canSubmit = Boolean(content.trim() && title.trim()) && !busy;

  return (
    <section aria-label="添加文档" className="rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold">
          添加文档
        </div>
        <div role="tablist" aria-label="录入方式" className="flex gap-1">
          {(['file', 'paste'] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              tabIndex={mode === m ? 0 : -1}
              onClick={() => setMode(m)}
              className={`rounded-sm px-2 py-0.5 text-meta font-medium transition-colors ${
                mode === m
                  ? 'bg-surface-sunken text-ink'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              {m === 'file' ? '选择文件' : '粘贴文本'}
            </button>
          ))}
        </div>
      </div>

      {mode === 'file' ? (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`rounded-md border border-dashed px-4 py-5 text-center transition-colors ${
            dragging ? 'border-status-accent bg-surface-sunken' : 'border-edge-subtle'
          }`}
        >
          <FileText size={16} aria-hidden className="mx-auto mb-1.5 text-ink-muted" />
          <p className="text-body text-ink-secondary">
            拖拽纯文本文件到此处，或
            <button
              type="button"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              className="mx-1 font-medium text-status-accent underline underline-offset-2 disabled:opacity-50"
            >
              选择文件
            </button>
           （.txt / .md / .json）
          </p>
          <p className="mt-1 text-meta text-ink-muted">
            文件在本地读取为文本后提交；PDF/DOCX 等格式后端暂不支持。
          </p>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            aria-label="选择知识库文档文件"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void readFile(file);
            }}
          />
          {fileName && (
            <p className="mt-2 text-meta text-ink-muted" data-state="file-loaded">
              已读取：{fileName}（{fileType}）
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <textarea
            aria-label="文档内容"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={busy}
            rows={5}
            placeholder="粘贴要索引的纯文本内容…"
            className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent disabled:opacity-60"
          />
          {mode === 'paste' && !fileName && (
            <p className="text-meta text-ink-muted">
              标题必填；file_type 按内容手工选择（当前：{fileType}）。
              <button
                type="button"
                onClick={() => setFileType(fileType === 'text' ? 'markdown' : fileType === 'markdown' ? 'json' : 'text')}
                className="ml-1 font-medium text-status-accent underline underline-offset-2"
              >
                切换（text → markdown → json）
              </button>
            </p>
          )}
        </div>
      )}

      {mode === 'file' && fileName && (
        <div className="mt-2 flex flex-col gap-2">
          <textarea
            aria-label="已读取的文档内容（可编辑）"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={busy}
            rows={4}
            className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body text-ink focus:outline-none focus:ring-1 focus:ring-status-accent disabled:opacity-60"
          />
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <label htmlFor="knowledge-doc-title" className="text-meta text-ink-muted">
          标题
        </label>
        <input
          id="knowledge-doc-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
          placeholder="文档标题"
          className="h-7 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent disabled:opacity-60"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!canSubmit}
          aria-busy={busy}
          className="inline-flex items-center gap-1.5 rounded-sm bg-status-accent px-3 py-1 text-body font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
        >
          <Upload size={13} aria-hidden />
          {busy ? (state.phase === 'reading' ? '读取中…' : '索引中…') : '索引文档'}
        </button>
      </div>

      {state.status === 'done' && (
        <p role="status" data-state="upload-ok" className="mt-2 text-body font-medium text-status-success">
          已索引：{state.result.document_id} · {state.result.chunk_count} 个分块 · {state.result.status}
        </p>
      )}
      {state.status === 'error' && (
        <p role="alert" className="mt-2 text-body font-medium text-status-critical">
          {state.message}
        </p>
      )}
    </section>
  );
}

export default KnowledgeUpload;
