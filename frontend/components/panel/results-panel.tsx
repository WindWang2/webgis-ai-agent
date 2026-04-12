"use client"
import { useState, useCallback } from "react"
import {
  FileText,
  BarChart3,
  Download,
  ChevronDown,
  ChevronUp,
  FileSpreadsheet,
  ImageIcon,
  Copy,
  Check,
  Loader2,
  Printer,
  Share2,
  RefreshCw,
  AlertCircle,
  PieChart,
  Layers,
  Eye,
  EyeOff,
  MapPin,
  Hash,
  Compass
} from "lucide-react"
import { ChartRenderer } from "./chart-renderer"
import { DraggableLayerList } from "../map/draggable-layer-list"
import type { ChartData, GeoJSONFeatureCollection, GeoJSONFeature, GeoJSONGeometry } from "@/lib/types"

interface ResultItem {
  id: string
  title: string
  type: "text" | "chart" | "map" | "table"
  content: string
  timestamp: Date
  chartData?: ChartData
}

interface ReportData {
  title: string
  summary: string
  charts: Array<{
    id: string
    type: "bar" | "pie" | "line"
    title: string
    data: unknown
  }>
  tables: Array<{
    headers: string[]
    rows: string[][]
  }>
  generatedAt: Date
}

// Layer type (matches lib/types/layer.ts)
interface LayerItem {
  id: string
  name: string
  type: string
  visible: boolean
  opacity: number
  source?: GeoJSONFeatureCollection
  style?: Record<string, unknown>
}

// Props for receiving analysis results from parent
interface ResultsPanelProps {
  onGenerateReport?: () => void
  onMapMove?: (center: [number, number], zoom: number) => void
  analysisResults?: ResultItem[]
  layers?: LayerItem[]
  onToggleLayer?: (layerId: string) => void
  onRemoveLayer?: (layerId: string) => void
  onUpdateLayer?: (layerId: string, updates: Partial<LayerItem>) => void
  onReorderLayers?: (layers: LayerItem[]) => void
}

export function ResultsPanel({ 
  onGenerateReport, 
  onMapMove, 
  analysisResults, 
  layers = [], 
  onToggleLayer,
  onRemoveLayer,
  onUpdateLayer,
  onReorderLayers
}: ResultsPanelProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"results" | "layers" | "report">("results")

  // State for report generation
  const [isGenerating, setIsGenerating] = useState(false)
  const [reportData, setReportData] = useState<ReportData | null>(null)
  const [copied, setCopied] = useState(false)
  const [generatingStep, setGeneratingStep] = useState("")
  const [reportError, setReportError] = useState<string | null>(null)

  // Demo results - would be passed from parent in production
  const results: ResultItem[] = analysisResults || []

  const toggleExpand = (id: string) => {
    setExpanded(expanded === id ? null : id)
  }

  // 从外部layer props获取可见性
  const isLayerVisible = (layerId: string) => {
    const found = layers.find(l => l.id === layerId)
    return found ? (found.visible !== false) : true
  }

  // 切换图层可见性 - 调用外部回调
  const toggleLayerVisibility = (layerId: string) => {
    if (onToggleLayer) {
      onToggleLayer(layerId)
    }
  }

  // 定位到图层
  const handleZoomToLayer = (layer: LayerItem) => {
    if (!onMapMove || !layer.source) return
    
    // 计算中心点 (简化逻辑，实际可能需要更复杂的 BBox 计算)
    const lngs: number[] = []
    const lats: number[] = []
    const features = layer.source.features || []
    
    features.forEach((f: GeoJSONFeature) => {
      if (f.geometry?.type === "Point") {
        lngs.push((f.geometry.coordinates as [number, number])[0])
        lats.push((f.geometry.coordinates as [number, number])[1])
      } else if (f.geometry?.coordinates) {
        // 简化：只取前几个点
        const coords = Array.isArray(f.geometry.coordinates[0]) ? f.geometry.coordinates[0] : [f.geometry.coordinates]
        coords.forEach((c: number[]) => {
          if (Array.isArray(c)) {
            lngs.push(c[0]); lats.push(c[1])
          }
        })
      }
    })

    if (lngs.length > 0) {
      const center: [number, number] = [
        lngs.reduce((a, b) => a + b, 0) / lngs.length,
        lats.reduce((a, b) => a + b, 0) / lats.length
      ]
      onMapMove(center, features.length > 50 ? 11 : 13)
    }
  }

  // Get geometry types from a GeoJSON FeatureCollection
  const getGeometryTypes = (geojson: GeoJSONFeatureCollection): string[] => {
    if (!geojson?.features) return []
    const types = new Set<string>()
    geojson.features.forEach((f: GeoJSONFeature) => {
      if (f.geometry?.type) types.add(f.geometry.type)
    })
    return Array.from(types)
  }

  // Compute stats
  const totalFeatures = (layers || []).reduce((sum, l) => sum + (l.source?.features?.length || 0), 0)

  // Call backend report API
  const handleGenerateReport = async () => {
    if (onGenerateReport) {
      onGenerateReport()
      return
    }

    setIsGenerating(true)
    setReportError(null)

    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""
      const response = await fetch(`${API_BASE}/reports/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "空间分析报告",
          layer: (layers || []).map(l => ({
            name: l.name,
            feature_count: l.source?.features?.length || 0,
            geojson: l.source,
          })),
          format: "html",
        }),
      })

      if (!response.ok) throw new Error(`报告生成失败: ${response.status}`)
      const result = await response.json()

      setReportData({
        title: result.title || "空间分析报告",
        summary: result.summary || `本次分析共包含 ${layers?.length || 0} 个图层，${totalFeatures} 个要素。`,
        charts: result.charts || [],
        tables: result.tables || [],
        generatedAt: new Date(),
      })
      setActiveTab("report")
    } catch (err: unknown) {
      // Fallback: generate report from local data
      setReportData({
        title: "空间分析报告",
        summary: `本次分析共包含 ${layers?.length || 0} 个图层，${totalFeatures} 个要素。`,
        charts: layers.slice(0, 2).map((l, i) => ({
          id: `c${i}`,
          type: "bar" as const,
          title: l.name,
          data: { labels: getGeometryTypes(l.source), values: getGeometryTypes(l.source).map(() => l.source?.features?.length || 0) }
        })),
        tables: [{
          headers: ["图层名称", "要素数量", "几何类型"],
          rows: layers.map(l => [
            l.name,
            String(l.source?.features?.length || 0),
            getGeometryTypes(l.source).join(", ") || "未知"
          ])
        }],
        generatedAt: new Date(),
      })
      setActiveTab("report")
    } finally {
      setIsGenerating(false)
      setGeneratingStep("")
    }
  }

  const handleCopyReport = () => {
    if (!reportData) return

    const reportText = `# ${reportData.title}
生成时间: ${reportData.generatedAt.toLocaleString()}
${'-'.repeat(40)}

## 分析摘要
${reportData.summary}

## 统计数据
${reportData.charts.map(c => `- ${c.title}`).join('\n')}

## 详细数据
${reportData.tables.map(t =>
  `${t.headers.join(' | ')}\n${t.rows.map(r => r.join(' | ')).join('\n')}`
).join('\n\n')}
`

    navigator.clipboard.writeText(reportText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleExportPDF = () => {
    window.print()
  }

  const formatTimestamp = (date: Date) => {
    return date.toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    })
  }

  const getTypeIcon = (type: string) => {
    switch (type) {
      case "chart": return <PieChart className="h-4 w-4" />
      case "map": return <FileText className="h-4 w-4" />
      case "table": return <FileSpreadsheet className="h-4 w-4" />
      default: return <FileText className="h-4 w-4" />
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header - 探险者档案夹风格 */}
      <div className="flex items-center justify-between border-b border-border p-3 bg-background-secondary/40 backdrop-blur-sm">
        <div className="flex p-1 bg-muted/30 rounded-xl gap-1 border border-border/20">
          <button
            onClick={() => setActiveTab("results")}
            className={`flex items-center gap-2 text-xs font-bold px-3 py-1.5 rounded-lg transition-all uppercase tracking-wider ${
              activeTab === "results"
                ? "bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                : "text-muted-foreground hover:text-foreground hover:bg-card/40"
            }`}
          >
            <Compass className="h-3.5 w-3.5" />
            见闻
          </button>
          <button
            onClick={() => setActiveTab("layers")}
            className={`flex items-center gap-2 text-xs font-bold px-3 py-1.5 rounded-lg transition-all uppercase tracking-wider ${
              activeTab === "layers"
                ? "bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                : "text-muted-foreground hover:text-foreground hover:bg-card/40"
            }`}
          >
            <Layers className="h-3.5 w-3.5" />
            图录
            {(layers?.length || 0) > 0 && (
              <span className={`ml-1 text-[10px] px-1 rounded ${activeTab === 'layers' ? 'bg-primary-foreground/20' : 'bg-primary/20 text-primary'}`}>
                {layers?.length || 0}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab("report")}
            className={`flex items-center gap-2 text-xs font-bold px-3 py-1.5 rounded-lg transition-all uppercase tracking-wider ${
              activeTab === "report"
                ? "bg-primary text-primary-foreground shadow-lg shadow-primary/20"
                : "text-muted-foreground hover:text-foreground hover:bg-card/40"
            }`}
          >
            <FileText className="h-3.5 w-3.5" />
            呈报
          </button>
        </div>

        {reportData && (
          <button
            onClick={handleExportPDF}
            className="flex items-center justify-center h-8 w-8 rounded-full bg-accent/10 border border-accent/30 text-accent hover:bg-accent hover:text-white transition-all shadow-sm"
            title="导出PDF"
          >
            <Printer className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === "layers" ? (
          /* Layers Tab */
          <div className="space-y-3">
            {/* Stats Summary - 大陆勘探统计风格 */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <div className="rounded-lg border border-border p-4 bg-card hover:border-primary/30 transition-all">
                <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
                  <Layers className="h-4 w-4 text-primary/60" /> 绘制层数
                </div>
                <div className="text-2xl font-semibold text-primary">{layers?.length || 0}</div>
              </div>
              <div className="rounded-lg border border-border p-4 bg-card hover:border-accent/30 transition-all">
                <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
                  <Hash className="h-4 w-4 text-accent/60" /> 要素总数
                </div>
                <div className="text-2xl font-semibold text-accent">
                  {layers.reduce((acc, l) => acc + (l.source?.features?.length || 0), 0)}
                </div>
              </div>
            </div>

            <DraggableLayerList
              layers={layers}
              onReorder={onReorderLayers || (() => {})}
              onToggle={toggleLayerVisibility}
              onDelete={onRemoveLayer || (() => {})}
              onUpdate={onUpdateLayer || (() => {})}
            />
          </div>
        ) : activeTab === "results" ? (
          /* Results Tab */
          <div className="space-y-3">
            {results.length === 0 ? (
              <div className="text-center text-muted-foreground text-sm py-8">
                <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p>暂无分析结果</p>
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
                      {getTypeIcon(result.type)}
                      <div className="text-left">
                        <span className="font-medium text-sm block">{result.title}</span>
                        <span className="text-xs text-muted-foreground">{formatTimestamp(result.timestamp)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      {result.type === "map" && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            // Logic to find corresponding layer and zoom
                            const layer = layers.find(l => l.name.includes(result.title) || result.id.includes(l.id));
                            if (layer) handleZoomToLayer(layer);
                          }}
                          className="p-1 rounded hover:bg-muted/50 transition-colors"
                          title="在地图上查看"
                        >
                          <MapPin className="h-4 w-4 text-primary" />
                        </button>
                      )}
                      {expanded === result.id ? (
                        <ChevronUp className="h-4 w-4" />
                      ) : (
                        <ChevronDown className="h-4 w-4" />
                      )}
                    </div>
                  </button>
                  {expanded === result.id && (
                    <div className="p-3 text-sm border-t border-border bg-card/20">
                      <p className="mb-3 text-muted-foreground leading-relaxed italic border-l-2 border-primary/30 pl-3">
                        {result.content}
                      </p>
                      {result.chartData && (
                        <div className="mt-4 p-4 bg-muted/20 rounded-xl border border-border/40 shadow-inner">
                          <ChartRenderer chart={result.chartData} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        ) : (
          /* Report Tab */
          isGenerating ? (
            <div className="flex flex-col items-center justify-center h-full">
              <Loader2 className="h-10 w-10 animate-spin text-primary mb-4" />
              <p className="text-sm text-muted-foreground">{generatingStep || "正在生成报告..."}</p>
            </div>
          ) : reportData ? (
            <div className="space-y-4 print:space-y-6">
              <div className="border-b border-border pb-3">
                <h2 className="font-semibold text-lg">{reportData.title}</h2>
                <p className="text-xs text-muted-foreground mt-1">
                  生成于 {reportData.generatedAt.toLocaleString("zh-CN")}
                </p>
              </div>

              <section>
                <h3 className="font-medium text-sm mb-2 flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  分析摘要
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {reportData.summary}
                </p>
              </section>

              {reportData.charts.length > 0 && (
                <section className="bg-muted/10 p-4 rounded-xl border border-border/40 shadow-sm">
                  <h3 className="font-semibold text-xs mb-4 flex items-center gap-2 uppercase tracking-widest text-primary">
                    <PieChart className="h-3.5 w-3.5" />
                    统计数据洞察
                  </h3>
                  <div className="space-y-6">
                    {reportData.charts.map((chart) => (
                      <div key={chart.id} className="p-1 rounded-lg">
                         <ChartRenderer chart={chart} />
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {reportData.tables.map((table, ti) => (
                <section key={ti}>
                  <h3 className="font-medium text-sm mb-2 flex items-center gap-2">
                    <FileSpreadsheet className="h-4 w-4" />
                    数据详情
                  </h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs border-collapse">
                      <thead>
                        <tr className="bg-muted/50">
                          {table.headers.map((h, hi) => (
                            <th key={hi} className="border border-border p-2 text-left font-medium">
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {table.rows.map((row, ri) => (
                          <tr key={ri}>
                            {row.map((cell, ci) => (
                              <td key={ci} className="border border-border p-2">
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}

              <div className="flex gap-2 pt-2 print:hidden">
                <button
                  onClick={handleCopyReport}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-border py-2 text-sm hover:bg-muted transition-colors"
                >
                  {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                  {copied ? "已复制" : "复制文本"}
                </button>
                <button
                  onClick={() => { setReportData(null); handleGenerateReport() }}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-border py-2 text-sm hover:bg-muted transition-colors"
                >
                  <RefreshCw className="h-4 w-4" />
                  重新生成
                </button>
              </div>
            </div>
          ) : (
            <div className="text-center text-muted-foreground text-sm py-8">
              <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>暂无报告</p>
              <p className="text-xs mt-2">点击下方按钮生成分析报告</p>
            </div>
          )
        )}
      </div>

      {/* Footer - 书卷生成按钮风格 */}
      <div className="border-t border-border p-4 bg-card/50 print:hidden">
        <button
          onClick={handleGenerateReport}
          disabled={isGenerating || ((layers?.length || 0) === 0 && results.length === 0)}
          className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-3 text-sm font-medium hover:bg-primary-dark disabled:opacity-50 transition-all border border-primary/30 hover:border-primary hover:shadow-lg"
        >
          {isGenerating ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {generatingStep || "奋笔疾书中..."}
            </>
          ) : (
            <>
              <FileText className="h-4 w-4" />
              撰写探险报告
            </>
          )}
        </button>
        {reportError && (
          <p className="text-xs text-destructive mt-1 text-center">{reportError}</p>
        )}
      </div>
    </div>
  )
}