"use client"
import { useState } from "react"
import {
  Layers,
  Hash,
  Activity,
} from "lucide-react"
import { DraggableLayerList } from "../map/draggable-layer-list"
import { TaskTimeline } from "@/components/hud/task-timeline"
import { AssetCard } from "./asset-card"
import { LayerStylePanel } from '@/components/hud/layer-style-panel';
import { API_BASE } from '@/lib/api/config';
import { useHudStore, type HudState } from "@/lib/store/useHudStore"
import { motion, AnimatePresence } from "framer-motion"
import { Layer } from "@/lib/types/layer"
import { useEffect } from "react"

interface DataHudProps {
  layers?: Layer[]
  sessionId?: string
  onToggleLayer?: (layerId: string) => void
  onRemoveLayer?: (layerId: string) => void
  onUpdateLayer?: (layerId: string, updates: Partial<Layer>) => void
  onReorderLayers?: (layers: Layer[]) => void
  onMapMove?: (center: [number, number], zoom: number) => void
}

export function DataHud({
  layers = [],
  sessionId: sessionIdProp,
  onToggleLayer,
  onRemoveLayer,
  onUpdateLayer,
  onReorderLayers,
  onMapMove: _onMapMove,
}: DataHudProps) {
  void _onMapMove;

  const [activeTab, setActiveTab] = useState<"tasks" | "layers" | "assets">("tasks")
  const editingLayerId = useHudStore((s: HudState) => s.editingLayerId);
  const {
    currentTask,
    analysisAssets,
    fetchAnalysisAssets,
    deleteAsset,
    updateAsset,
    addLayer,
  } = useHudStore()

  const effectiveSessionId = sessionIdProp

  useEffect(() => {
    if (activeTab === "assets") {
      fetchAnalysisAssets(effectiveSessionId)
    }
  }, [activeTab, fetchAnalysisAssets, effectiveSessionId])

  const totalFeatures = layers.reduce(
    (sum, l) => sum + (l.source && typeof l.source === 'object' && 'features' in l.source ? (l.source as any).features?.length || 0 : 0),
    0
  )

  const tabs = [
    { id: "tasks" as const, label: "任务", icon: <Activity className="h-3 w-3" />, count: currentTask ? currentTask.steps.length : 0 },
    { id: "layers" as const, label: "图层", icon: <Layers className="h-3 w-3" />, count: layers.length },
    { id: "assets" as const, label: "资产", icon: <Hash className="h-3 w-3" />, count: analysisAssets.length },
  ]

  const activeIndex = tabs.findIndex(t => t.id === activeTab)

  const slideVariants = {
    enter: (direction: number) => ({
      x: direction > 0 ? 30 : -30,
      opacity: 0,
    }),
    center: { x: 0, opacity: 1 },
    exit: (direction: number) => ({
      x: direction > 0 ? -30 : 30,
      opacity: 0,
    }),
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="relative flex px-3 pt-2 pb-0 gap-0.5">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`
              relative flex items-center gap-1.5 px-3 py-2 text-[10px] font-display font-semibold uppercase tracking-[0.12em] transition-colors rounded-t-lg
              ${activeTab === tab.id
                ? "text-hud-cyan bg-hud-cyan/[0.06]"
                : "text-white/25 hover:text-white/40"
              }
            `}
          >
            {tab.icon}
            {tab.label}
            {tab.count > 0 && (
              <span className={`
                ml-0.5 text-[8px] px-1.5 py-0.5 rounded-full font-mono tabular-nums
                ${activeTab === tab.id ? "bg-hud-cyan/20 text-hud-cyan" : "bg-white/[0.04] text-white/30"}
              `}>
                {tab.count}
              </span>
            )}
          </button>
        ))}
        {/* Animated underline */}
        <motion.div
          className="absolute bottom-0 h-[2px] bg-hud-cyan rounded-full shadow-[0_0_8px_rgba(0,242,255,0.5)]"
          layoutId="tab-indicator"
          style={{ width: `${100 / tabs.length}%` }}
          animate={{ x: activeIndex * (100 / tabs.length) + '%' }}
          transition={{ type: "spring", stiffness: 400, damping: 30 }}
        />
      </div>

      {/* Separator */}
      <div className="h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {editingLayerId ? (
          <LayerStylePanel />
        ) : (
        <AnimatePresence mode="wait" custom={activeIndex}>
          {activeTab === "tasks" ? (
            <motion.div
              key="tasks"
              custom={1}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              {currentTask ? (
                <TaskTimeline />
              ) : (
                <div className="flex flex-col items-center justify-center h-48 text-center px-6">
                  <div className="relative">
                    <Activity className="h-8 w-8 text-white/[0.06]" />
                    <div className="absolute inset-0 animate-ping opacity-20">
                      <Activity className="h-8 w-8 text-hud-cyan/30" />
                    </div>
                  </div>
                  <p className="text-[11px] text-white/20 font-light mt-4">无活跃任务</p>
                  <p className="text-[10px] text-white/10 mt-1">
                    空间分析运行时将在此显示执行进度
                  </p>
                </div>
              )}
            </motion.div>
          ) : activeTab === "layers" ? (
            <motion.div
              key="layers"
              custom={2}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="p-3"
            >
              {/* Stats Summary */}
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="rounded-xl p-3 bg-white/[0.02] border border-white/[0.04] relative overflow-hidden">
                  <div className="absolute top-0 right-0 w-12 h-12 bg-hud-cyan/[0.03] rounded-full -translate-y-1/3 translate-x-1/3" />
                  <div className="flex items-center gap-1.5 text-white/25 text-[9px] mb-1.5 uppercase tracking-wider">
                    <Layers className="h-2.5 w-2.5 text-hud-cyan/40" /> 图层
                  </div>
                  <div className="text-xl font-bold text-hud-cyan font-display tabular-nums">
                    {layers.length}
                  </div>
                </div>
                <div className="rounded-xl p-3 bg-white/[0.02] border border-white/[0.04] relative overflow-hidden">
                  <div className="absolute top-0 right-0 w-12 h-12 bg-emerald-500/[0.03] rounded-full -translate-y-1/3 translate-x-1/3" />
                  <div className="flex items-center gap-1.5 text-white/25 text-[9px] mb-1.5 uppercase tracking-wider">
                    <Hash className="h-2.5 w-2.5 text-emerald-400/40" /> 要素
                  </div>
                  <div className="text-xl font-bold text-emerald-400 font-display tabular-nums">
                    {totalFeatures.toLocaleString()}
                  </div>
                </div>
              </div>

              {/* Draggable Layer List */}
              {layers.length > 0 ? (
                <DraggableLayerList
                  layers={layers}
                  onReorder={onReorderLayers || (() => {})}
                  onToggle={onToggleLayer || (() => {})}
                  onDelete={onRemoveLayer || (() => {})}
                  onUpdate={onUpdateLayer || (() => {})}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-32 text-center">
                  <div className="relative">
                    <Layers className="h-8 w-8 text-white/[0.06]" />
                    <div className="absolute inset-0 animate-ping opacity-20">
                      <Layers className="h-8 w-8 text-hud-cyan/20" />
                    </div>
                  </div>
                  <p className="text-[11px] text-white/20 font-light mt-4">无图层</p>
                  <p className="text-[10px] text-white/10 mt-1">
                    通过 AI 分析自动生成空间图层
                  </p>
                </div>
              )}
            </motion.div>
          ) : (
            <motion.div
              key="assets"
              custom={3}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="p-3 space-y-2"
            >
              {analysisAssets.length > 0 ? (
                analysisAssets.map((asset: any) => (
                  <AssetCard
                    key={asset.id}
                    asset={asset}
                    onDelete={(id) => {
                      fetch(`${API_BASE}/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=delete`)
                        .then(() => deleteAsset(id))
                    }}
                    onRename={(id, newName) => {
                      fetch(`${API_BASE}/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=rename&new_name=${encodeURIComponent(newName)}`)
                        .then(() => updateAsset(id, { original_name: newName }))
                    }}
                    onLoad={(asset) => {
                      addLayer({
                        id: `asset-${asset.id}`,
                        name: asset.original_name,
                        type: "raster",
                        visible: true,
                        opacity: 0.8,
                        source: asset.filename,
                        style: {}
                      })
                    }}
                  />
                ))
              ) : (
                <div className="flex flex-col items-center justify-center h-48 text-center">
                  <div className="relative">
                    <Hash className="h-8 w-8 text-white/[0.06]" />
                    <div className="absolute inset-0 animate-ping opacity-20">
                      <Hash className="h-8 w-8 text-emerald-400/20" />
                    </div>
                  </div>
                  <p className="text-[11px] text-white/20 font-light mt-4">无分析资产</p>
                  <p className="text-[10px] text-white/10 mt-1">
                    完成遥感分析后成果将永久保存在此
                  </p>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
        )}
      </div>
    </div>
  )
}

export { DataHud as ResultsPanel }
