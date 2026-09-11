'use client';

/**
 * ProjectAssetsSection — 项目资产区 tab 容器（ADR-0143 P2–P7）。
 *
 * 在既有 project-tab 的项目视图内 append-only 新增五个资产页签；tab 状态
 * 受控提升到 project-tab（P7 交叉导航：工作流视图可回跳并指明目标页签）。
 * 目录边界：本目录不 import components/sidebar/workflow/**（E 线），
 * 只被 project-tab.tsx 引用。
 */

import { useEffect, useState } from 'react';
import { Database, Package, Camera, ShieldCheck, Trash2 } from 'lucide-react';

import { DatasetManager } from './dataset-manager';
import { ArtifactCenter } from './artifact-center';
import { SnapshotTimeline } from './snapshot-timeline';
import { QualityPanel } from './quality-panel';
import { DataGcPanel } from './gc-panel';

export type AssetTab = 'datasets' | 'artifacts' | 'snapshots' | 'quality' | 'gc';

export const ASSET_TABS: Array<{ value: AssetTab; label: string; icon: typeof Database }> = [
  { value: 'datasets', label: '数据集', icon: Database },
  { value: 'artifacts', label: '产物', icon: Package },
  { value: 'snapshots', label: '快照', icon: Camera },
  { value: 'quality', label: '质量', icon: ShieldCheck },
  { value: 'gc', label: '回收', icon: Trash2 },
];

export interface ProjectAssetsSectionProps {
  projectId: string;
  sessionId?: string | null;
  authed: boolean;
  tab: AssetTab;
  onTabChange: (tab: AssetTab) => void;
  /** 交叉导航：质量回执/gc 候选 → 产物血缘定位。 */
  focusArtifactId?: string | null;
  /** layer 型数据集在主地图打开（无集成方提供则不渲染该按钮）。 */
  onOpenInMap?: (datasetId: string) => void;
}

export function ProjectAssetsSection({
  projectId,
  sessionId,
  authed,
  tab,
  onTabChange,
  focusArtifactId,
  onOpenInMap,
}: ProjectAssetsSectionProps) {
  const [localFocus, setLocalFocus] = useState<string | null>(focusArtifactId ?? null);

  // 外部交叉导航（质量回执/gc）更新 focusArtifactId 时同步本地焦点。
  useEffect(() => {
    if (focusArtifactId != null) setLocalFocus(focusArtifactId);
  }, [focusArtifactId]);

  if (!projectId) {
    return (
      <p className="rounded-md border border-edge-subtle bg-surface-raised px-panel py-2 text-micro text-ink-muted">
        选择项目后可管理数据集、产物、快照、质量与回收。
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <div role="tablist" aria-label="项目资产" className="flex flex-wrap gap-1 border-b border-edge-subtle pb-1.5">
        {ASSET_TABS.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => onTabChange(value)}
            className={`flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-micro font-medium transition-colors ${
              tab === value
                ? 'bg-status-accent text-ink-on-accent'
                : 'text-ink-secondary hover:bg-surface-raised'
            }`}
          >
            <Icon size={11} aria-hidden /> {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" aria-label={ASSET_TABS.find((t) => t.value === tab)?.label}>
        {tab === 'datasets' && (
          <DatasetManager projectId={projectId} authed={authed} onOpenInMap={onOpenInMap} />
        )}
        {tab === 'artifacts' && (
          <ArtifactCenter
            projectId={projectId}
            onLocateArtifact={(id) => setLocalFocus(id)}
            focusArtifactId={localFocus}
          />
        )}
        {tab === 'snapshots' && <SnapshotTimeline projectId={projectId} sessionId={sessionId} authed={authed} />}
        {tab === 'quality' && <QualityPanel projectId={projectId} authed={authed} />}
        {tab === 'gc' && <DataGcPanel projectId={projectId} authed={authed} />}
      </div>
    </div>
  );
}
