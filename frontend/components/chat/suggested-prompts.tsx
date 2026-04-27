"use client"
import { motion } from "framer-motion"
import { MapPin, Satellite, BarChart3, Search } from "lucide-react"

interface SuggestedPromptsProps {
  onSend: (message: string) => void
}

const SUGGESTIONS = [
  { icon: <MapPin className="h-4 w-4" />, text: "分析北京市学校分布" },
  { icon: <Satellite className="h-4 w-4" />, text: "计算NDVI植被指数" },
  { icon: <BarChart3 className="h-4 w-4" />, text: "生成人口密度热力图" },
  { icon: <Search className="h-4 w-4" />, text: "搜索成都市天府广场" },
]

export function SuggestedPrompts({ onSend }: SuggestedPromptsProps) {
  return (
    <div className="px-4 py-3 flex gap-2 overflow-x-auto">
      {SUGGESTIONS.map((s, i) => (
        <motion.button
          key={s.text}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05, duration: 0.2 }}
          onClick={() => onSend(s.text)}
          className="flex items-center gap-2 px-3 py-2 rounded-xl border border-white/[0.06] bg-white/[0.02] text-white/50 text-[11px] whitespace-nowrap hover:bg-hud-cyan/[0.06] hover:border-hud-cyan/20 hover:text-hud-cyan transition-all shrink-0"
        >
          {s.icon}
          {s.text}
        </motion.button>
      ))}
    </div>
  )
}
