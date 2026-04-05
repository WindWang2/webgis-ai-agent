"use client"
import { useState, useRef } from "react"
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
  PieChart
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

// Props for receiving analysis results from parent
interface ResultsPanelProps {
  onGenerateReport?: () => void
  analysisResults?: ResultItem[]
}

export function ResultsPanel({ onGenerateReport, analysisResults }: ResultsPanelProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<"results" | "report">("results")
  
  // State for report generation
  const [isGenerating, setIsGenerating] = useState(false)
  const [reportData, setReportData] = useState<ReportData | null>(null)
  const [copied, setCopied] = useState(false)
  const [generatingStep, setGeneratingStep] = useState("")
  
  // Demo results - would be passed from parent in production
  const results: ResultItem[] = analysisResults || [
    {
      id: "1",
      title: "北京市人口密度分析",
      type: "chart",
      content: "分析了北京市各区人口密度数据，最大密度出现在朝阳区，达 21000人/km²",
      timestamp: new Date(),
      chartData: { type: "bar", values: [15000, 18000, 21000, 16500, 12000] }
    },
    {
      id: "2",
      title: "周边设施检索结果",
      type: "map",
      content: "在选定区域内发现公园15个、学校8个、医院3个",
      timestamp: new Date()
    }
  ]

  const toggleExpand = (id: string) => {
    setExpanded(expanded === id ? null : id)
  }

  // Simulated report generation
  const handleGenerateReport = async () => {
    setIsGenerating(true)
    
    // Simulate progress steps
    const steps = [
      { step: "正在收集分析结果...", delay: 800 },
      { step: "正在生成统计图表...", delay: 1200 },
      { step: "正在构建报告内容...", delay: 1600 },
      { step: "正在格式化输出...", delay: 2000 },
    ]
    
    for (const { step, delay } of steps) {
      setGeneratingStep(step)
      await new Promise(resolve => setTimeout(resolve, delay))
    }
    
    // Mock report data
    setReportData({
      title: "空间分析报告",
      summary: "本次分析涵盖研究区域内的各类设施分布情况，综合分析结果表明该区域配套设施完善程度较高其中公远和教育资源尤为丰富",
      charts: [
        { id: "c1", type: "pie", title: "设施类型分布", data: { labels: ["公园", "学校", "医院", "商场"], values: [15, 8, 3, 12] }},
        { id: "c2", type: "bar", title: "各区人口对比", data: { label: ["朝阳", "海淀", "西城", "东城", "丰台"], value: [21000, 18000, 15000, 12000, 9500] }}
      ],
      tables: [
        { headers: ["名称", "类型", "距离(km)", "评分"], rows: [["朝阳公园", "公园", "0.5", "4.5"], ["人大附中", "学校", "1.2", "4.8"], ["朝阳医院", "医院", "2.1", "4.2"]]}
      ],
      generatedAt: new Date()
    })
    
    setIsGenerating(false)
    setGeneratingStep("")
    setActiveTab("report")
  }

  const handleCopyReport = () => {
    if (!reportData) return
    
    const reportText = `# ${reportData.title}
生成时间: ${reportData.generatedAt.toLocaleString()}
${'-'.repeat(40)}
${'## 分析摘要\n'}
${reportData.summary}
${'## 统计数据\n'}
${reportData.charts.map(c => `- ${c.title}`).join('\n')}
${'## 详细数据\n'}
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
        
        {/* Export Button - Only show when report exists */}
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
        {activeTab === "results" ? (
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
                      {/* Chart preview placeholder */}
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
              {/* Report Title */}
              <div className="border-b border-border pb-3">
                <h2 className="font-semibold text-lg">{reportData.title}</h2>
                <p className="text-xs text-muted-foreground mt-1">
                  生成于 {reportData.generatedAt.toLocaleString("zh-CN")}
                </p>
              </div>
              
              {/* Summary Section */}
              <section>
                <h3 className="font-medium text-sm mb-2 flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  分析摘要
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {reportData.summary}
                </p>
              </section>
              
              {/* Charts Section */}
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
              
              {/* Tables Section */}
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
              
              {/* Action Buttons Row */}
              <div className="flex gap-2 pt-2 print:hidden">
                <button
                  onClick={handleCopyReport}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-border py-2 text-sm hover:bg-muted transition-colors"
                >
                  {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                  {copied ? "已复制" : "复制文本"}
                </button>
                <button
                  onClick={() => setReportData(null) || handleGenerateReport()}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-border py-2 text-sm hover:bg-muted transition-colors"
                >
                  <RefreshCw className="h-4 w-4" />
                  重新生成
                </button>
              </div>
            </div>
          ) : (
            /* Empty Report State */
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
        {activeTab === "report" && !reportData ? (
          <button 
            onClick={handleGenerateReport}
            disabled={isGenerating}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {isGenerating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                生成中...
              </>
            ) : (
              <>
                <FileText className="h-4 w-4" />
                生成报告
              </>
            )}
          </button>
        ) : (
          <button 
            onClick={handleGenerateReport}
            disabled={isGenerating || results.length === 0}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {isGenerating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                处理中...
              </>
            ) : (
              <>
                <FileText className="h-4 w-4" />
                生成完整报告
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )
}