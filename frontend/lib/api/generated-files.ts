import { fromMarkdown } from 'mdast-util-from-markdown';
import { toString as nodeText } from 'mdast-util-to-string';
import { filenameFromUrl, isProtectedDownloadUrl } from './authenticated-download';
import { toApiPath } from './first-party';
import { apiFetch, type ApiFetchOptions } from './transport';

export interface GeneratedFile {
  id: string;
  name: string;
  format: string;
  source: 'export' | 'report' | 'delivery' | 'chat';
  created_at: number | null;
  download_url: string | null;
}

interface FileMessage {
  role: string;
  content: string;
  timestamp?: Date | number | null;
}

function downloadPath(url: string): string | null {
  if (!isProtectedDownloadUrl(url)) return null;
  const path = toApiPath(url).split('#')[0];
  return path || null;
}

export async function listGeneratedFiles(
  sessionId: string,
  options: Pick<ApiFetchOptions, 'ownerToken' | 'signal'> = {},
): Promise<GeneratedFile[]> {
  const response = await apiFetch<{ items: GeneratedFile[] }>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/generated-files`,
    { ...options, cache: 'no-store' },
  );
  return response.items.map((file) => ({
    ...file,
    download_url: file.download_url ? downloadPath(file.download_url) : null,
  }));
}

type MarkdownNode = ReturnType<typeof fromMarkdown>['children'][number];
const messageCache = new WeakMap<FileMessage, GeneratedFile[]>();

function fileKey(path: string): string {
  const url = new URL(path, 'https://local.invalid');
  const member = url.pathname.startsWith('/api/v1/data-delivery/jobs/') && url.searchParams.get('member');
  return member ? `${url.pathname}?member=${encodeURIComponent(member)}` : url.pathname;
}

function deliveryMemberKey(jobId: string, name: string): string {
  return `/api/v1/data-delivery/jobs/${encodeURIComponent(jobId)}/download?member=${encodeURIComponent(name)}`;
}

function messageFiles(message: FileMessage): GeneratedFile[] {
  if (message.role !== 'assistant' || !message.content.includes('/api/v1/')) return [];
  const cached = messageCache.get(message);
  if (cached) return cached;

  const tree = fromMarkdown(message.content);
  const definitions = new Map<string, string>();
  const files: GeneratedFile[] = [];
  const stamp = message.timestamp?.valueOf();
  const created = typeof stamp === 'number' && Number.isFinite(stamp) ? stamp / 1000 : null;

  function add(url: string, label: string) {
    const path = downloadPath(url);
    if (!path) return;
    const key = fileKey(path);
    const urlInfo = new URL(key, 'https://local.invalid');
    const filename = urlInfo.searchParams.get('member')?.split('/').pop() || filenameFromUrl(key);
    const title = label && label !== url ? label : urlInfo.pathname.split('/').at(-2);
    const name = filename === 'download' && title ? title : filename;
    const format = name.match(/\.([a-z0-9]{1,10})$/i)?.[1].toLowerCase() || '';
    files.push({ id: key, name, format, source: 'chat', created_at: created, download_url: path });
  }

  function collectDefinitions(node: MarkdownNode) {
    if (node.type === 'definition' && !definitions.has(node.identifier)) definitions.set(node.identifier, node.url);
    if ('children' in node) node.children.forEach(collectDefinitions);
  }
  function collectLinks(node: MarkdownNode) {
    if (node.type === 'link' || node.type === 'image') {
      add(node.url, nodeText(node));
    } else if (node.type === 'linkReference' || node.type === 'imageReference') {
      const url = definitions.get(node.identifier);
      if (url) add(url, nodeText(node));
    } else if (node.type === 'text') {
      // Some tool replies show the URL as prose. Only match whole URL tokens.
      for (const match of node.value.matchAll(/(?:^|[\s(（])((?:https?:\/\/|\/api\/v1\/)[^\s<>"`）)。，]+)/gi)) {
        add(match[1].replace(/[.,;!?]+$/, ''), '');
      }
    } else if ('children' in node) {
      node.children.forEach(collectLinks);
    }
  }
  tree.children.forEach(collectDefinitions);
  tree.children.forEach(collectLinks);
  messageCache.set(message, files);
  return files;
}

export function filesFromMessages(messages: readonly FileMessage[]): GeneratedFile[] {
  // The newest reply may contain a refreshed signed link for the same file.
  return [...messages].reverse().flatMap(messageFiles);
}

export function generatedFileKey(file: GeneratedFile): string {
  if (file.source === 'delivery') {
    const match = /^delivery:([^:]+):(.+)$/.exec(file.id);
    if (match) return deliveryMemberKey(match[1], match[2]);
  }
  if (file.download_url) return fileKey(file.download_url);
  if (file.source === 'report' && file.id.startsWith('report:')) {
    return `/api/v1/reports/${encodeURIComponent(file.id.slice('report:'.length))}/download`;
  }
  if (file.source === 'export' && file.id.startsWith('ref:export/')) {
    return `/api/v1/export/download/${encodeURIComponent(file.id.slice('ref:export/'.length))}`;
  }
  return file.id;
}

export function mergeGeneratedFiles(catalog: GeneratedFile[], fromChat: GeneratedFile[]): GeneratedFile[] {
  const files = new Map<string, GeneratedFile>();
  const catalogUrls = new Set<string>();
  for (const file of catalog) {
    files.set(generatedFileKey(file), file);
    if (file.download_url) catalogUrls.add(fileKey(file.download_url));
  }
  for (const file of fromChat) {
    const key = generatedFileKey(file);
    if (!files.has(key) && !catalogUrls.has(key)) files.set(key, file);
  }
  return [...files.values()];
}
