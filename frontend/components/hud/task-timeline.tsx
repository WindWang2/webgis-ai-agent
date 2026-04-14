'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  MapPin,
  Zap,
  Clock,
} from 'lucide-react';
import { useHudStore, TaskStep } from '@/lib/store/useHudStore';
import type { GeoJSONFeatureCollection } from '@/lib/types';

/* Tool display name mapping */
const TOOL_LABELS: Record<string, string> = {
  query_osm_poi: 'POI 查询',
  query_osm_roads: '路网查询',
  query_osm_boundary: '边界查询',
  buffer_analysis: '缓冲区分析',
  overlay_analysis: '叠加分析',
  heatmap_data: '热力图生成',
  generate_chart: '图表生成',
  create_thematic_map: '专题制图',
  path_analysis: '路径分析',
  zonal_stats: '区域统计',
  apply_layer_style: '样式设置',
  geocode: '地理编码',
};

function getToolLabel(tool: string): string {
  return TOOL_LABELS[tool] || tool;
}

function StepIcon({ status }: { status: TaskStep['status'] }) {
  switch (status) {
    case 'running':
      return <Loader2 className="h-3.5 w-3.5 text-hud-cyan animate-spin" />;
    case 'completed':
      return <CheckCircle2 className="h-3.5 w-3.5 text-hud-green" />;
    case 'failed':
      return <AlertTriangle className="h-3.5 w-3.5 text-hud-orange" />;
    default:
      return <Circle className="h-3.5 w-3.5 text-white/20" />;
  }
}

function StepCard({ step, onViewSnapshot }: { step: TaskStep; onViewSnapshot?: (geojson: GeoJSONFeatureCollection) => void }) {
  const [expanded, setExpanded] = useState(false);
  const elapsed = step.completedAt && step.startedAt
    ? ((step.completedAt - step.startedAt) / 1000).toFixed(1)
    : null;

  return (
    <motion.div
      className="relative pl-6"
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Timeline connector line */}
      <div className="absolute left-[7px] top-0 bottom-0 w-px bg-gradient-to-b from-hud-cyan/30 to-transparent" />
      {/* Timeline dot */}
      <div className="absolute left-0 top-1.5">
        <StepIcon status={step.status} />
      </div>

      <div
        className={`rounded-lg p-3 mb-2 transition-all cursor-pointer ${
          step.status === 'running'
            ? 'bg-hud-cyan/[0.05] border border-hud-cyan/20'
            : step.status === 'failed'
            ? 'bg-hud-orange/[0.04] border border-hud-orange/15'
            : 'bg-white/[0.02] border border-white/[0.04] hover:bg-white/[0.04]'
        }`}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="h-3 w-3 text-hud-cyan/60" />
            <span className="text-xs font-medium text-white/80">
              {getToolLabel(step.tool)}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {elapsed && (
              <span className="flex items-center gap-1 text-[10px] text-white/30 font-mono">
                <Clock className="h-2.5 w-2.5" />
                {elapsed}s
              </span>
            )}
            {step.hasGeojson && step.geojsonSnapshot && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onViewSnapshot?.(step.geojsonSnapshot!);
                }}
                className="hud-btn h-5 px-1.5 rounded text-[9px] text-hud-cyan/70 gap-1"
                title="在地图上查看此步骤的数据快照"
              >
                <MapPin className="h-2.5 w-2.5" />
                GeoJSON
              </button>
            )}
            {expanded ? (
              <ChevronUp className="h-3 w-3 text-white/20" />
            ) : (
              <ChevronDown className="h-3 w-3 text-white/20" />
            )}
          </div>
        </div>

        {/* Expanded detail */}
        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="mt-2 pt-2 border-t border-white/[0.04]">
                {step.error && (
                  <p className="text-[11px] text-hud-orange/80 font-mono break-all">
                    ⚠ {step.error}
                  </p>
                )}
                {step.result && typeof step.result === 'object' ? (
                  <pre className="text-[10px] text-white/30 font-mono max-h-32 overflow-y-auto break-all whitespace-pre-wrap">
                    {JSON.stringify(step.result, null, 2).slice(0, 500)}
                  </pre>
                ) : null}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

export function TaskTimeline() {
  const currentTask = useHudStore((s) => s.currentTask);
  const addProcessLayer = useHudStore((s) => s.addProcessLayer);

  if (!currentTask) return null;

  const handleViewSnapshot = (geojson: GeoJSONFeatureCollection) => {
    // Add as a temporary process layer for preview
    const tempId = `snapshot-${Date.now()}`;
    addProcessLayer(tempId, geojson);
    // Auto-remove after 30s
    setTimeout(() => {
      useHudStore.getState().removeProcessLayer(tempId);
    }, 30000);
  };

  return (
    <div className="p-4">
      {/* Task header */}
      <div className="flex items-center gap-2 mb-4">
        <div className={`h-2 w-2 rounded-full ${
          currentTask.status === 'running' ? 'bg-hud-cyan animate-pulse' :
          currentTask.status === 'completed' ? 'bg-hud-green' :
          currentTask.status === 'failed' ? 'bg-hud-orange' :
          'bg-white/20'
        }`} />
        <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-white/40">
          Task {currentTask.id.slice(0, 8)}
        </span>
        <span className="text-[10px] font-mono text-white/25">
          · {currentTask.steps.length} steps
        </span>
      </div>

      {/* Steps Timeline */}
      <div className="space-y-0">
        {currentTask.steps.map((step) => (
          <StepCard
            key={step.id}
            step={step}
            onViewSnapshot={handleViewSnapshot}
          />
        ))}
      </div>

      {/* Summary */}
      <AnimatePresence>
        {currentTask.status === 'completed' && currentTask.summary && (
          <motion.div
            className="mt-4 p-3 rounded-lg bg-hud-green/[0.05] border border-hud-green/15"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <p className="text-[11px] text-hud-green/80 leading-relaxed">
              {currentTask.summary}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
