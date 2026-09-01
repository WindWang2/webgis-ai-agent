"use client"
import { motion } from "framer-motion"
import { MapPin, Satellite, BarChart3, Search } from "lucide-react"

export interface SuggestedPromptsProps {
  onSend: (message: string) => void
  suggestions?: { icon?: React.ReactNode; text: string }[]
}

const DEFAULT_SUGGESTIONS = [
  { icon: <MapPin className="h-3.5 w-3.5" />, text: "分析北京市学校分布" },
  { icon: <Satellite className="h-3.5 w-3.5" />, text: "计算NDVI植被指数" },
  { icon: <BarChart3 className="h-3.5 w-3.5" />, text: "生成人口密度热力图" },
  { icon: <Search className="h-3.5 w-3.5" />, text: "搜索成都市天府广场" },
]

export function SuggestedPrompts({ onSend, suggestions = DEFAULT_SUGGESTIONS }: SuggestedPromptsProps) {
  return (
    <div className="px-4 py-3 flex gap-2 overflow-x-auto">
      {suggestions.map((s, i) => (
        <motion.button
          key={s.text}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.04, duration: 0.2 }}
          onClick={() => onSend(s.text)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-edge-subtle bg-surface-sunken text-ink-secondary hover:text-ink hover:bg-surface-raised hover:border-status-accent-border text-body whitespace-nowrap transition-colors shrink-0 shadow-xs cursor-pointer"
        >
          {s.icon && <span className="text-status-accent shrink-0">{s.icon}</span>}
          <span>{s.text}</span>
        </motion.button>
      ))}
    </div>
  )
}

export default SuggestedPrompts;
