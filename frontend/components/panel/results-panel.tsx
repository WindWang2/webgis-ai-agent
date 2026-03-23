"use client"

import { FileText, BarChart3, Download, ChevronDown, ChevronUp } from "lucide-react"
import { useState } from "react"

interface AnalysisResult {
  id: string
  title: string
  type: "text" | "chart" | "map"
  content: string
  timestamp: Date
}

export function ResultsPanel() {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"results" | "report">("results")

  const results: AnalysisResult[] = [
    {
      id: "1",
      title: "分析结果摘要",
      type: "text",
      content: "分析完成。共处理数据 3 个图层，生成统计图表 2 个。",
      timestamp: new Date(),
    },
  ]

  const toggleExpand = (id: string) => {
    setExpanded(expanded === id ? null : id)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex gap-4">
          <button
            onClick={() => setActiveTab("results")}
            className={`text-sm font-medium transition-colors ${
              activeTab === "results"
                ? "text-primary border-b-2 border-primary pb-1"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            分析结果
          </button>
          <button
            onClick={() => setActiveTab("report")}
            className={`text-sm font-medium transition-colors ${
              activeTab === "report"
                ? "text-primary border-b-2 border-primary pb-1"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            报告预览
          </button>
        </div>
        <button
          className="flex items-center gap-1 text-sm text-primary hover:text-primary/80 transition-colors"
          title="导出报告"
        >
          <Download className="h-4 w-4" />
          导出
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === "results" ? (
          <div className="space-y-3">
            {results.length === 0 ? (
              <div className="text-center text-muted-foreground text-sm py-8">
                暂无分析结果
                <p className="text-xs mt-2">在左侧输入分析指令开始</p>
              </div>
            ) : (
              results.map((result) => (
                <div
                  key={result.id}
                  className="border border-border rounded-lg overflow-hidden"
                >
                  <button
                    onClick={() => toggleExpand(result.id)}
                    className="w-full flex items-center justify-between p-3 bg-muted hover:bg-muted/80 transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      {result.type === "text" && <FileText className="h-4 w-4" />}
                      {result.type === "chart" && <BarChart3 className="h-4 w-4" />}
                      {result.type === "map" && <FileText className="h-4 w-4" />}
                      <span className="font-medium text-sm">{result.title}</span>
                    </div>
                    {expanded === result.id ? (
                      <ChevronUp className="h-4 w-4" />
                    ) : (
                      <ChevronDown className="h-4 w-4" />
                    )}
                  </button>
                  {expanded === result.id && (
                    <div className="p-3 text-sm border-t border-border">
                      {result.content}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="text-center text-muted-foreground text-sm py-8">
            <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p>报告预览</p>
            <p className="text-xs mt-2">生成分析后将在此处显示报告</p>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border p-3">
        <button className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-medium hover:bg-primary/90 transition-colors">
          <FileText className="h-4 w-4" />
          生成完整报告
        </button>
      </div>
    </div>
  )
}
