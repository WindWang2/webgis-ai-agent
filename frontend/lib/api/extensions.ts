/**
 * 扩展市场 API — /api/v1/extensions/marketplace/*（契约见
 * frontend/docs/knowledge-market-recon.md）。
 *
 * 后端事实（UI 不得美化）：
 * - HTTP 面是**只读**的：browse / search / detail / download。安装、卸载、
 *   启用、停用、已安装列表全部只在运维 CLI（python -m app.extensions_platform），
 *   无 HTTP 端点 —— UI 不出现假安装按钮。
 * - certification / trust store / SBOM 内容均无 HTTP 端点；版本详情里能拿到
 *   的信任信号是 digest / signature / fingerprint / sbom_digest / permissions。
 * - 列表 404 = EXTENSION_REGISTRY_DIR 未配置 = 市场未启用（不是错误）。
 * - 响应是裸 dict（无 ApiResponse 信封）。
 */
import { apiFetch } from './transport';

export type PackageStatus = 'active' | 'deprecated' | 'revoked';

export interface PackageSummary {
  id: string;
  title: string;
  description: string;
  publisher: string;
  status: PackageStatus;
  deprecation_note: string;
  latest_version: string | null;
  versions: string[];
  tags: string[];
}

export interface PackageDependency {
  id?: string;
  required?: boolean;
  version?: string;
  optional?: boolean;
  [key: string]: unknown;
}

export interface PackageVersionDetail {
  package_id: string;
  version: string;
  digest: string;
  size_bytes: number;
  publisher: string;
  key_id: string;
  signature: Record<string, unknown>;
  fingerprint: string;
  sbom_digest: string;
  permissions: string[];
  api_version: string;
  min_core_version: string;
  dependencies: PackageDependency[];
  yanked: boolean;
  created_at: string;
  download: string;
}

export interface PackageListResult {
  total: number;
  offset: number;
  limit: number;
  items: PackageSummary[];
}

export async function listMarketPackages(
  opts: {
    q?: string;
    tag?: string;
    publisher?: string;
    includeRevoked?: boolean;
    offset?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<PackageListResult> {
  const params = new URLSearchParams();
  if (opts.q) params.set('q', opts.q);
  if (opts.tag) params.set('tag', opts.tag);
  if (opts.publisher) params.set('publisher', opts.publisher);
  if (opts.includeRevoked) params.set('include_revoked', 'true');
  params.set('offset', String(opts.offset ?? 0));
  params.set('limit', String(opts.limit ?? 20));
  return apiFetch<PackageListResult>(`/api/v1/extensions/marketplace/packages?${params.toString()}`, {
    signal: opts.signal,
    label: 'Marketplace packages error',
  });
}

export async function getMarketPackage(
  packageId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<PackageSummary> {
  return apiFetch<PackageSummary>(
    `/api/v1/extensions/marketplace/packages/${encodeURIComponent(packageId)}`,
    { signal: opts.signal, label: 'Marketplace package error' },
  );
}

export async function getMarketPackageVersion(
  packageId: string,
  version: string,
  opts: { signal?: AbortSignal } = {},
): Promise<PackageVersionDetail> {
  return apiFetch<PackageVersionDetail>(
    `/api/v1/extensions/marketplace/packages/${encodeURIComponent(packageId)}/versions/${encodeURIComponent(version)}`,
    { signal: opts.signal, label: 'Marketplace version error' },
  );
}
