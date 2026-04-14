"use client"
import { useState } from "react"
import {
  Layers,
  Hash,
  Activity,
} from "lucide-react"
import { DraggableLayerList } from "../map/draggable-layer-list"
import { TaskTimeline } from "@/components/hud/task-timeline"
import { useHudStore } from "@/lib/store/useHudStore"
import { motion, AnimatePresence } from "framer-motion"
import { Layer } from "@/lib/types/layer"

interface DataHudProps {
  layers?: Layer[]
  onToggleLayer?: (layerId: string) => void
  onRemoveLayer?: (layerId: string) => void
  onUpdateLayer?: (layerId: string, updates: Partial<Layer>) => void
  onReorderLayers?: (layers: Layer[]) => void
  onMapMove?: (center: [number, number], zoom: number) => void
}

export function DataHud({
  layers = [],
  onToggleLayer,
  onRemoveLayer,
  onUpdateLayer,
  onReorderLayers,
  onMapMove: _onMapMove,
}: DataHudProps) {
  void _onMapMove;

  const [activeTab, setActiveTab] = useState<"tasks" | "layers">("tasks")
  const currentTask = useHudStore((s) => s.currentTask)

  const totalFeatures = layers.reduce(
    (sum, l) => sum + (l.source && typeof l.source === 'object' && 'features' in l.source ? (l.source as any).features?.length || 0 : 0),
    0
  )

  const tabs = [
    { id: "tasks" as const, label: "TASKS", icon: <Activity className="h-3 w-3" />, count: currentTask ? currentTask.steps.length : 0 },
    { id: "layers" as const, label: "LAYERS", icon: <Layers className="h-3 w-3" />, count: layers.length },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex px-3 pt-2 pb-1 gap-1 border-b border-white/[0.04]">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-display font-semibold uppercase tracking-[0.12em] transition-all ${
              activeTab === tab.id
                ? "bg-hud-cyan/10 text-hud-cyan border border-hud-cyan/20"
                : "text-white/30 hover:text-white/50 border border-transparent"
            }`}
          >
            {tab.icon}
            {tab.label}
            {tab.count > 0 && (
              <span
                className={`ml-1 text-[9px] px-1.5 py-0.5 rounded-full ${
                  activeTab === tab.id ? "bg-hud-cyan/20" : "bg-white/[0.06]"
                }`}
              >
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          {activeTab === "tasks" ? (
            <motion.div
              key="tasks"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {currentTask ? (
                <TaskTimeline />
              ) : (
                <div className="flex flex-col items-center justify-center h-48 text-center">
                  <Activity className="h-8 w-8 text-white/[0.06] mb-3" />
                  <p className="text-[11px] text-white/20 font-light">
                    无活跃任务
                  </p>
                  <p className="text-[10px] text-white/10 mt-1">
                    空间分析运行时将在此显示执行进度
                  </p>
                </div>
              )}
            </motion.div>
          ) : (
            <motion.div
              key="layers"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="p-3"
            >
              {/* Stats Summary */}
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="rounded-lg p-3 bg-white/[0.02] border border-white/[0.04]">
                  <div className="flex items-center gap-1.5 text-white/30 text-[10px] mb-1">
                    <Layers className="h-3 w-3 text-hud-cyan/40" /> 图层数
                  </div>
                  <div className="text-lg font-semibold text-hud-cyan font-display">
                    {layers.length}
                  </div>
                </div>
                <div className="rounded-lg p-3 bg-white/[0.02] border border-white/[0.04]">
                  <div className="flex items-center gap-1.5 text-white/30 text-[10px] mb-1">
                    <Hash className="h-3 w-3 text-hud-green/40" /> 要素总数
                  </div>
                  <div className="text-lg font-semibold text-hud-green font-display">
                    {totalFeatures}
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
                  <Layers className="h-8 w-8 text-white/[0.06] mb-3" />
                  <p className="text-[11px] text-white/20 font-light">无图层</p>
                  <p className="text-[10px] text-white/10 mt-1">
                    通过 AI 分析自动生成空间图层
                  </p>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* Re-export for backward compat */
export { DataHud as ResultsPanel }