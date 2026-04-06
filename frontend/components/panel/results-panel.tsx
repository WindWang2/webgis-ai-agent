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
  Hash
} from "lucide-react"

interface ResultItem {
  id: string
  title: string
  type: "text" | "chart" | "map" | "table"
  content: string
  timestamp: Date
  chartData?: any
}

interface ReportData {
  title: string
  summary: string
  charts: Array<{
    id: string
    type: "bar" | "pie" | "line"
    title: string
    data: any
  }>
  tables: Array<{
    headers: string[]
    rows: string[][]
  }>
  generatedAt: Date
}

// GeoJsonLayer type (matches page.tsx)
interface GeoJsonLayer {
  id: string
  name: string
  geojson: any
  color?: string
  visible?: boolean
}

// Props for receiving analysis results from parent
interface ResultsPanelProps {
  onGenerateReport?: () => void
  analysisResults?: ResultItem[]
  layers?: GeoJsonLayer[]
}

export function ResultsPanel({ onGenerateReport, analysisResults, layers = [] }: ResultsPanelProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"results" | "layers" | "report">("results")
  const [layerVisibility, setLayerVisibility] = useState<Record<string, boolean>>({})
  
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

  // Toggle layer visibility (local state)
  const toggleLayerVisibility = (layerId: string) => {
    setLayerVisibility(prev => ({ ...prev, [layerId]: !prev[layerId] }))
  }

  // Get geometry types from a GeoJSON FeatureCollection
  const getGeometryTypes = (geojson: any): string[] => {
    if (!geojson?.features) return []
    const types = new Set<string>()
    geojson.features.forEach((f: any) => {
      if (f.geometry?.type) types.add(f.geometry.type)
    })
    return Array.from(types)
  }

  // Compute stats
  const totalFeatures = layers.reduce((sum, l) => sum + (l.geojson?.features?.length || 0), 0)

  // Call backend report API
  const handleGenerateReport = async () => {
    if (onGenerateReport) {
      onGenerateReport()
      return
    }

    setIsGenerating(true)
    setReportError(null)

    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://192.168.193.121:8002/api/v1"
      const response = await fetch(`${API_BASE}/reports/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "空间分析报告",
          layers: layers.map(l => ({
            name: l.name,
            feature_count: l.geojson?.features?.length || 0,
            geojson: l.geojson,
          })),
          format: "html",
        }),
      })

      if (!response.ok) throw new Error(`报告生成失败: ${response.status}`)
      const result = await response.json()

      setReportData({
        title: result.title || "空间分析报告",
        summary: result.summary || `本次分析共包含 ${layers.length} 个图层，${totalFeatures} 个要素。`,
        charts: result.charts || [],
        tables: result.tables || [],
        generatedAt: new Date(),
      })
      setActiveTab("report")
    } catch (err: any) {
      // Fallback: generate report from local data
      setReportData({
        title: "空间分析报告",
        summary: `本次分析共包含 ${layers.length} 个图层，${totalFeatures} 个要素。`,
        charts: layers.slice(0, 2).map((l, i) => ({
          id: `c${i}`,
          type: "bar" as const,
          title: l.name,
          data: { labels: getGeometryTypes(l.geojson), values: getGeometryTypes(l.geojson).map(() => l.geojson?.features?.length || 0) }
        })),
        tables: [{
          headers: ["图层名称", "要素数量", "几何类型"],
          rows: layers.map(l => [
            l.name,
            String(l.geojson?.features?.length || 0),
            getGeometryTypes(l.geojson).join(", ") || "未知"
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
            onClick={() => setActiveTab("layers")}
            className={`text-sm font-medium transition-colors ${
              activeTab === "layers"
                ? "text-primary border-b-2 border-primary pb-1"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <span className="flex items-center gap-1">
              <Layers className="h-3.5 w-3.5" />
              图层
            </span>
            {layers.length > 0 && <span className="ml-1 text-xs text-muted-foreground">({layers.length})</span>}
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
            {reportData && <span className="ml-1 text-xs text-green-500">✓</span>}
          </button>
        </div>
        
        {reportData && (
          <button
            onClick={handleExportPDF}
            className="flex items-center gap-1 text-sm text-primary hover:text-primary/80 transition-colors"
            title="导出PDF"
          >
            <Printer className="h-4 w-4" />
            导出
          </button>
        )}
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === "layers" ? (
          /* Layers Tab */
          <div className="space-y-3">
            {/* Stats Summary */}
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-border p-3 bg-muted/30">
                <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
                  <Layers className="h-3.5 w-3.5" /> 图层总数
                </div>
                <div className="text-xl font-semibold">{layers.length}</div>
              </div>
              <div className="rounded-lg border border-border p-3 bg-muted/30">
                <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
                  <Hash className="h-3.5 w-3.5" /> 要素总数
                </div>
                <div className="text-xl font-semibold">{totalFeatures}</div>
              </div>
            </div>

            {layers.length === 0 ? (
              <div className="text-center text-muted-foreground text-sm py-8">
                <Layers className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p>暂无图层</p>
                <p className="text-xs mt-2">上传数据或执行空间分析后，图层将显示在此处</p>
              </div>
            ) : (
              layers.map((layer) => {
                const featureCount = layer.geojson?.features?.length || 0
                const geomTypes = getGeometryTypes(layer.geojson)
                const isVisible = layerVisibility[layer.id] !== false && layer.visible !== false
                return (
                  <div key={layer.id} className="rounded-lg border border-border overflow-hidden">
                    <div className="flex items-center justify-between p-3 bg-muted/30">
                      <div className="flex items-center gap-2 min-w-0">
                        <div
                          className="h-3 w-3 rounded-full flex-shrink-0"
                          style={{ backgroundColor: layer.color || "#3b82f6" }}
                        />
                        <div className="min-w-0">
                          <span className="font-medium text-sm block truncate">{layer.name}</span>
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span className="flex items-center gap-0.5">
                              <MapPin className="h-3 w-3" /> {featureCount} 个要素
                            </span>
                            {geomTypes.length > 0 && (
                              <span>{geomTypes.join(", ")}</span>
                            )}
                          </div>
                        </div>
                      </div>
                      <button
                        onClick={() => toggleLayerVisibility(layer.id)}
                        className="p-1 rounded hover:bg-muted transition-colors"
                        title={isVisible ? "隐藏图层" : "显示图层"}
                      >
                        {isVisible ? (
                          <Eye className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <EyeOff className="h-4 w-4 text-muted-foreground/50" />
                        )}
                      </button>
                    </div>
                  </div>
                )
              })
            )}
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
                    {expanded === result.id ? (
                      <ChevronUp className="h-4 w-4" />
                    ) : (
                      <ChevronDown className="h-4 w-4" />
                    )}
                  </button>
                  {expanded === result.id && (
                    <div className="p-3 text-sm border-t border-border">
                      <p className="mb-2">{result.content}</p>
                      {result.chartData && (
                        <div className="mt-2 p-2 bg-muted/50 rounded text-center text-xs text-muted-foreground">
                          [图表: {result.chartData.type}]
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
                <section>
                  <h3 className="font-medium text-sm mb-2 flex items-center gap-2">
                    <PieChart className="h-4 w-4" />
                    统计图表
                  </h3>
                  <div className="grid grid-cols-2 gap-3">
                    {reportData.charts.map((chart) => (
                      <div key={chart.id} className="p-3 bg-muted/30 rounded-lg">
                        <p className="text-xs font-medium mb-2">{chart.title}</p>
                        <div className="h-20 flex items-end justify-around gap-1">
                          {Array.isArray(chart.data.values || chart.data.value) && 
                            ((chart.data.values || chart.data.value) as number[]).slice(0, 5).map((val, i) => (
                              <div
                                key={i}
                                className="flex-1 bg-primary/60 rounded-t"
                                style={{ height: `${Math.min(val / 250, 1) * 100}%` }}
                              />
                            ))
                          }
                        </div>
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

      {/* Footer - Generate Button */}
      <div className="border-t border-border p-3 print:hidden">
        <button 
          onClick={handleGenerateReport}
          disabled={isGenerating || (layers.length === 0 && results.length === 0)}
          className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {isGenerating ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {generatingStep || "生成中..."}
            </>
          ) : (
            <>
              <FileText className="h-4 w-4" />
              生成报告
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
